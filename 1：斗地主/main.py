"""运行单局斗地主；每次实际 API 请求先写入本地 record 文件。"""

import argparse
import json
import math
import os
import random
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from bot import Agent, parse_action, parse_bid
from common import Action, Cards, Combo, DECK, Hand, PLAYERS, Reply, RuleError
from display import render
from judge import judge

BASE = Path(__file__).resolve().parent


@dataclass
class State:
    hands: dict[str, Hand]
    bottom: Cards
    deal: int = 1
    bids: dict[str, int] = field(default_factory=dict)
    landlord: str | None = None
    actor: str = PLAYERS[0]
    target: Combo | None = None
    owner: str | None = None
    passes: int = 0
    turn: int = 1
    history: list[dict] = field(default_factory=list)


def load_configs(env_file: str | None = None) -> dict[str, dict]:
    if env_file and not Path(env_file).is_file():
        raise ValueError(f"找不到配置文件：{env_file}")
    load_dotenv(env_file or find_dotenv(), override=False)
    options = {
        "timeout": float(os.getenv("API_TIMEOUT") or 120),
        "retries": int(os.getenv("API_RETRIES") or 2),
        "backoff": float(os.getenv("API_BACKOFF") or 1),
    }
    if not math.isfinite(options["timeout"]) or options["timeout"] <= 0:
        raise ValueError("API_TIMEOUT 必须是正数。")
    if options["retries"] < 0 or not math.isfinite(options["backoff"]) or options["backoff"] < 0:
        raise ValueError("重试次数和退避秒数不能为负。")
    if os.getenv("max_tokens", "").strip():
        options["max_tokens"] = int(os.environ["max_tokens"])
        if options["max_tokens"] <= 0:
            raise ValueError("max_tokens 必须是正整数，或留空。")
    configs = {}
    for name in PLAYERS:
        config = dict(options)
        for key in ("api_key", "base_url", "model"):
            config[key] = (os.getenv(f"{name.upper()}_{key.upper()}")
                           or os.getenv(f"OPENAI_{key.upper()}") or "").strip()
            if not config[key]:
                raise ValueError(f"{name} 缺少 {key} 配置，请填写 .env。")
        configs[name] = config
    return configs


def record_request(path: Path, context: dict, endpoint: str, body: bytes, attempt: int) -> None:
    """JSONL 每行是一次请求；写入失败时不继续调用 API。"""
    entry = {
        **context, "http_attempt": attempt, "time": datetime.now().isoformat(),
        "method": "POST", "url": endpoint, "body": json.loads(body),
    }
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(entry, ensure_ascii=False) + "\n")


def deal(agents: dict[str, Agent], rng: random.Random, number: int) -> State:
    cards = list(DECK)
    rng.shuffle(cards)
    hands = {name: tuple(cards[i * 17:(i + 1) * 17]) for i, name in enumerate(PLAYERS)}
    for name, agent in agents.items():
        agent.start_deal(hands[name])
    return State({name: Counter(hand) for name, hand in hands.items()}, tuple(cards[51:]), number)


def public_table(state: State) -> dict:
    """显式构造公开信息，避免把观众快照或完整手牌交给模型。"""
    return {
        "deal": state.deal, "phase": "play" if state.landlord else "bid",
        "actor": state.actor, "turn": state.turn, "bids": dict(state.bids),
        "landlord": state.landlord, "bottom": state.bottom if state.landlord else (),
        "remaining": {name: sum(hand.values()) for name, hand in state.hands.items()},
        "target": asdict(state.target) if state.target else None,
        "owner": state.owner, "passes": state.passes, "history": list(state.history),
    }


def spectator_view(state: State, **event) -> dict:
    roles = {name: ("地主" if name == state.landlord else "农民")
             if state.landlord else "待定" for name in PLAYERS}
    return {
        "hands": state.hands, "actor": state.actor, "roles": roles,
        "phase": "play" if state.landlord else "bid", "deal": state.deal,
        "turn": state.turn, "target": state.target, "owner": state.owner,
        "bottom": state.bottom if state.landlord else (), **event,
    }


def _decide(state: State, agent: Agent, records: Path) -> tuple[int | Action, Combo | None, Reply, int]:
    """叫分与出牌共用纠正循环；成功返回决策、牌型、可见回复和尝试次数。"""
    bidding = state.landlord is None
    slot = PLAYERS.index(state.actor) + 1 if bidding else (state.turn - 1) % 3 + 1
    round_no = (state.turn - 1) // 3 + 1
    filename = f"record-bid-{slot}.jsonl" if bidding else f"record-{round_no}-{slot}.jsonl"
    attempt, truncated = 0, 0
    while True:
        attempt += 1
        context = {"deal": state.deal, "phase": "bid" if bidding else "play",
                   "round": None if bidding else round_no, "slot": slot,
                   "player": state.actor, "decision_attempt": attempt}
        agent.on_request = partial(record_request, records / filename, context)
        print(render(spectator_view(state, attempt=attempt)))
        reply = agent.reply(public_table(state))
        reason = reply["finish_reason"]
        choice, combination = None, None
        if reason == "length":
            truncated += 1
            error = "生成达到上限，动作未生效；请精简说明并输出完整协议行。"
        elif reason not in ("stop", None):
            print(render(spectator_view(state, reply=reply, attempt=attempt)))
            raise RuntimeError(f"API 未正常完成生成：{reason}")
        else:
            try:
                choice = parse_bid(reply["text"]) if bidding else parse_action(reply["text"])
                if not bidding:
                    combination = judge(choice, state.hands[state.actor], state.target)
                return choice, combination, reply, attempt
            except RuleError as rejected:
                error = str(rejected)
        state.history.append({"phase": context["phase"], "turn": state.turn,
                              "player": state.actor, "attempt": attempt, "accepted": False,
                              "action": asdict(choice) if isinstance(choice, Action) else None,
                              "error": error})
        print(render(spectator_view(state, reply=reply, attempt=attempt, event=f"无效，未生效：{error}")), end="\n\n")
        if reason == "length" and truncated > agent.config["retries"]:
            raise RuntimeError("生成截断重试已耗尽；请检查 max_tokens 与模型的生成限制。")


def bid(state: State, agents: dict[str, Agent], records: Path) -> bool:
    for name in PLAYERS:
        state.actor = name
        score, _, reply, attempt = _decide(state, agents[name], records)
        state.bids[name] = score
        state.history.append({"phase": "bid", "player": name, "bid": score,
                              "attempt": attempt, "accepted": True})
        print(render(spectator_view(state, reply=reply, attempt=attempt, event=f"{name} 叫分 {score}，有效。")), end="\n\n")
    landlord = max(PLAYERS, key=state.bids.__getitem__)
    if state.bids[landlord] == 0:
        print("三人都不叫，重新洗牌发牌。")
        print("--------------------------")
        return False
    state.landlord = state.actor = landlord
    state.hands[landlord].update(state.bottom)
    state.history.append({"phase": "play", "event": "landlord", "player": landlord, "bottom": state.bottom})
    print(render(spectator_view(state, event=f"{landlord} 成为地主，领取公开底牌并首先领出。")))
    print("--------------------------")
    return True


def apply(state: State, action: Action, combination: Combo | None) -> None:
    """只执行已经通过 judge 的动作；展示结果后再换人和推进回合计数。"""
    if action.kind == "play":
        state.hands[state.actor] -= Counter(action.cards)
        state.target, state.owner, state.passes = combination, state.actor, 0
    else:
        state.passes += 1
        if state.passes == 2:
            state.target, state.owner, state.passes = None, None, 0


def turn(state: State, agents: dict[str, Agent], records: Path) -> bool:
    action, combination, reply, attempt = _decide(state, agents[state.actor], records)
    apply(state, action, combination)
    state.history.append({"phase": "play", "turn": state.turn, "player": state.actor,
                          "attempt": attempt, "action": asdict(action), "accepted": True})
    finished = not state.hands[state.actor]
    played = f"打出 {' '.join(action.cards)}" if action.kind == "play" else "不出"
    event = f"{state.actor} {played}。裁判：合法，已生效。"
    if action.kind == "pass" and state.target is None:
        event += " 连续两次不出，下一位重新领出。"
    winner = ""
    if finished:
        winner = f"地主 {state.landlord}" if state.actor == state.landlord else "农民（" + "、".join(
            name for name in PLAYERS if name != state.landlord) + "）"
    print(render(spectator_view(state, reply=reply, attempt=attempt, event=event, winner=winner)))
    print("--------------------------" if state.turn % 3 == 0 or finished else "")
    if not finished:
        state.actor = PLAYERS[(PLAYERS.index(state.actor) + 1) % 3]
        state.turn += 1
    return finished


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="三个 AI 玩家自动斗地主，每次请求保存 record。")
    parser.add_argument("--env-file", help="指定 .env；默认从脚本位置向上查找")
    parser.add_argument("--seed", type=int, help="固定洗牌种子")
    parser.add_argument("--records-dir", type=Path, default=BASE / "records", help="请求记录根目录")
    args = parser.parse_args(argv)
    try:
        agents = {name: Agent(name, config) for name, config in load_configs(args.env_file).items()}
        run_dir = args.records_dir / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        run_dir.mkdir(parents=True)
        print(f"本次请求记录：{run_dir.resolve()}")
        rng, number = random.Random(args.seed), 0
        while True:
            number += 1
            state = deal(agents, rng, number)
            records = run_dir / f"deal-{number}"
            records.mkdir()
            if bid(state, agents, records):
                break
        while not turn(state, agents, records):
            pass
        return 0
    except KeyboardInterrupt:
        print("\n用户中止，停止后续请求。")
        return 130
    except (ValueError, RuntimeError, OSError) as error:
        print(f"运行中止：{error}（不因运行故障判定玩家输牌）")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
