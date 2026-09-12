"""观战输出：render 只返回文字，由主程序打印。"""

from collections import Counter
from collections.abc import Callable

from common import Hand, PLAYERS, RANKS


def _cards(hand: Hand) -> str:
    groups = [" ".join([rank] * hand[rank]) for rank in RANKS[:-2] if hand[rank]]
    jokers = " ".join(rank for rank in ("X", "Y") if hand[rank])
    if jokers:
        groups.append(jokers)
    return " | ".join(groups) or "（空）"


def render(view: dict) -> str:
    """观众快照需要 hands（玩家→Counter）和 actor（名字）。

    可选：phase、deal、turn（从 1 起）、attempt、roles、target（Combo）、owner、
    bottom（Cards）、reply（Reply）、event（主程序给出的事件文字）、winner（获胜阵营文字）。
    该快照包含私有手牌和模型说明，不能作为 Agent 的 table。
    """
    actor = view["actor"]
    index = PLAYERS.index(actor)
    turn = view.get("turn", 1)
    stage = (
        "叫地主" if view.get("phase") == "bid"
        else f"第 {(turn - 1) // 3 + 1} 轮 · 第 {(turn - 1) % 3 + 1} 位"
    )
    lines = [
        f"第 {view.get('deal', 1)} 次发牌 · {stage} · {actor} · 尝试 {view.get('attempt', 1)}",
        " -> ".join(PLAYERS[index:] + PLAYERS[:index]),
    ]
    for name in PLAYERS:
        hand = view["hands"][name]
        role = view.get("roles", {}).get(name, "待定")
        lines.append(f"{name}: {_cards(hand)}  [{role}，剩余 {sum(hand.values())} 张]")
    if view.get("bottom"):
        lines.append(f"公开底牌：{_cards(Counter(view['bottom']))}")
    if view.get("phase") != "bid":
        target = view.get("target")
        lines.append(
            f"待压制：{view['owner']} · {target.kind} · {_cards(Counter(target.cards))}"
            if target is not None else f"{actor} 需要领出。"
        )
    if "reply" in view:
        reply = view["reply"]
        lines.extend([f"{actor} 的可见回复：", reply["text"] or "（空响应）"])
        lines.append(f"生成结束原因：{reply.get('finish_reason')}；用量：{reply.get('usage')}")
    if view.get("event"):
        lines.append(view["event"])
    if view.get("winner"):
        lines.append(f"本局结束：{view['winner']}获胜。")
    return "\n".join(lines)


def pause(read: Callable[[str], str] = input) -> None:
    read("按回车继续……")
