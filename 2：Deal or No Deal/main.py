"""三节点 LangGraph：AI 提出动作 → execute 校验执行 → 条件边决定下一位。"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from bot import Agent, AgentError, ChatAgent, SYSTEM_PROMPTS, load_configs
from display import show_event, show_result
from game import RuleError, actor_for, apply_action, public_view, tool_schemas
from state import Actor, GameState, initial_state

MAX_FAILED_ATTEMPTS = 3
RECURSION_LIMIT = 500


def route_after_execute(state: GameState) -> str:
    """这是条件边的路由函数，不是第四个节点。"""
    return END if state["status"] != "running" else state["actor"]


def build_graph(agents: dict[Actor, Agent], on_event: Callable[[GameState], None] | None = None,
                max_model_calls: int = 200):
    if set(agents) != {"player", "banker"} or not 1 <= max_model_calls <= 200:
        raise ValueError("必须提供两个角色，并把 max_model_calls 设为 1 至 200。")

    def model_node(actor: Actor):
        def invoke(state: GameState) -> dict:
            if state["model_calls"] >= max_model_calls:
                return {"status": "error", "pending": None, "error": "达到本局 Agent 调用次数上限。"}
            key = f"{actor}_messages"
            snapshot = HumanMessage(content=json.dumps(public_view(state["game"]), ensure_ascii=False))
            messages = [SystemMessage(content=SYSTEM_PROMPTS[actor]), *state[key], snapshot]
            update = {key: [snapshot], "model_calls": state["model_calls"] + 1}
            try:
                update["pending"] = agents[actor].invoke(messages, tool_schemas(state["game"]))
            except AgentError as error:
                update.update({"status": "error", "pending": None, "error": str(error)})
            return update
        return invoke

    def execute_node(state: GameState) -> dict:
        if state["status"] != "running":
            return {"pending": None}
        actor, candidate = state["actor"], state["pending"]
        key = f"{actor}_messages"
        history = state[key]
        error = None
        call = None
        # 结构坏掉的调用不能写进历史，否则下一轮 API 会收到悬空的工具调用。
        if not isinstance(candidate, AIMessage):
            error = "必须返回一个 AIMessage 工具调用。"
        elif candidate.response_metadata.get("protocol_error"):
            error = candidate.response_metadata["protocol_error"]
        elif candidate.response_metadata.get("finish_reason") in ("length", "content_filter"):
            error = "响应未完整结束，请重新提交一个工具调用。"
        elif candidate.invalid_tool_calls or len(candidate.tool_calls) != 1:
            error = "每次必须恰好调用一个工具，不可用普通文本代替。"
        else:
            call = candidate.tool_calls[0]
            used_ids = {message.tool_call_id for message in history if isinstance(message, ToolMessage)}
            if not call.get("id") or not call["id"].strip() or call["id"] in used_ids:
                error = "工具调用 id 必须非空，并且不能重复使用历史调用 id。"
        if error:
            feedback = [HumanMessage(content=f"执行失败（局面未变）：{error}")]
        else:
            # 自己生成消息 id，避免供应商复用 id 导致 add_messages 覆盖历史。
            candidate = candidate.model_copy(update={"id": None})
            try:
                game, result = apply_action(state["game"], actor, call["name"], call["args"])
                payload = {"ok": True, "event": game["events"][-1], "next_phase": game["phase"]}
            except RuleError as failure:
                error = str(failure)
                payload = {"ok": False, "error": error, "state_changed": False}
            feedback = [candidate, ToolMessage(content=json.dumps(payload, ensure_ascii=False),
                                               tool_call_id=call["id"], name=call["name"])]
        if error:
            retries = state["retry_count"] + 1
            exhausted = retries >= MAX_FAILED_ATTEMPTS
            return {key: feedback, "pending": None, "retry_count": retries,
                    "status": "error" if exhausted else "running",
                    "error": f"{actor} 连续 {retries} 次无效响应：{error}" if exhausted else None}
        update = {"game": game, "actor": actor_for(game), "pending": None,
                  key: feedback, "retry_count": 0,
                  "status": "completed" if result else "running", "result": result, "error": None}
        if on_event:
            on_event({**state, **update})
        return update

    graph = StateGraph(GameState)
    graph.add_node("player", model_node("player"))
    graph.add_node("banker", model_node("banker"))
    graph.add_node("execute", execute_node)
    graph.add_edge(START, "player")
    graph.add_edge("player", "execute")
    graph.add_edge("banker", "execute")
    graph.add_conditional_edges("execute", route_after_execute,
                                {"player": "player", "banker": "banker", END: END})
    return graph.compile()


def save_record(state: GameState) -> Path:
    """记录公开事件；不存密钥、提示词、失败原文或未完成局的隐藏映射。"""
    directory = Path(__file__).with_name("records")
    directory.mkdir(exist_ok=True)
    path = directory / f"game-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.json"
    record = {"status": state["status"], "error": state["error"], "model_calls": state["model_calls"],
              "events": state["game"]["events"], "result": state["result"]}
    if state["status"] == "completed":
        record["revealed_boxes"] = state["game"]["boxes"]
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="围观两个 AI 玩 Deal or No Deal；终端无需人工操作。")
    parser.add_argument("--seed", type=int, help="仅供本地调试的洗牌种子，不发送给模型")
    parser.add_argument("--env-file", help="指定配置文件；默认使用项目目录的 .env")
    parser.add_argument("--max-model-calls", type=int, default=200, help="本局 Agent 调用上限（1..200）")
    parser.add_argument("--record", action="store_true", help="在已忽略的 records/ 保存公开事件和结算")
    parser.add_argument("--quiet", action="store_true", help="只显示最终结果")
    args = parser.parse_args()
    try:
        agents = {role: ChatAgent(config) for role, config in load_configs(args.env_file).items()}
        graph = build_graph(agents, None if args.quiet else show_event, args.max_model_calls)
        print("Deal or No Deal | 双 AI 自动对局")
        print("* 表示选手保留箱。分析中的概率假设拒绝所有后续报价并持箱到最后。", flush=True)
        state = graph.invoke(initial_state(args.seed), {"recursion_limit": RECURSION_LIMIT})
        show_result(state)
        if args.record:
            print(f"本地记录：{save_record(state)}")
        return 0 if state["status"] == "completed" else 1
    except (ValueError, OSError) as error:
        print(f"启动或记录失败：{error}")
        return 1
    except KeyboardInterrupt:
        print("\n用户中止运行；未完成对局不结算，本版本不提供恢复。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
