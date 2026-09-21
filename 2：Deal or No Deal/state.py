"""图的共享状态；只有两份消息历史使用 add_messages，其余字段整体替换。"""

import random
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage
from langgraph.graph.message import add_messages

Actor = Literal["player", "banker"]
Phase = Literal["choose", "open", "bank_early", "bank_normal", "offer", "counter", "done"]
PRIZES = (1, 5, 10, 25, 50, 100, 200, 500, 1000, 2500, 5000, 7500,
          10000, 20000, 30000, 50000, 75000, 100000, 250000, 1000000)
ROUND_QUOTAS = (5, 4, 3, 3, 2, 1)
BASE_PERCENT = (45, 55, 65, 75, 85, 95)


class Offer(TypedDict):
    amount: int
    kind: Literal["early", "normal"]
    level: str
    percent: int
    raw_amount: int
    floor_applied: bool
    counter_used: bool


class Event(TypedDict):
    number: int
    round_no: int
    actor: Actor
    action: str
    reason: str
    details: dict


class Result(TypedDict):
    kind: Literal["deal", "counter", "box"]
    payout: int
    own_amount: int


class GameData(TypedDict):
    boxes: dict[int, int]                 # 隐藏的固定映射，不能整体发给模型。
    own_box: int | None
    opened: list[int]
    round_no: int
    opened_in_round: int
    early_calls: int                     # 整局已生效的提前报价数，最多 2。
    early_in_round: bool                # 当前轮是否已提前报价，最多一次。
    phase: Phase
    offer: Offer | None
    counter_amount: int | None
    events: list[Event]                  # 仅记录正式生效的公开事件。


class GameState(TypedDict):
    game: GameData
    actor: Actor
    pending: AIMessage | None           # 未执行的候选，不等于已生效动作。
    player_messages: Annotated[list[AnyMessage], add_messages]
    banker_messages: Annotated[list[AnyMessage], add_messages]
    retry_count: int                    # 当前决策连续失败次数，成功后清零。
    model_calls: int                    # Agent 调用次数，不含 HTTP 内部重试。
    status: Literal["running", "completed", "error"]
    result: Result | None
    error: str | None


def initial_state(seed: int | None = None) -> GameState:
    """先洗牌再选箱。seed 只给本地调试使用，不写入模型可见状态。"""
    amounts = list(PRIZES)
    random.Random(seed).shuffle(amounts)
    return {
        "game": {
            "boxes": dict(zip(range(1, 21), amounts)), "own_box": None,
            "opened": [], "round_no": 1, "opened_in_round": 0,
            "early_calls": 0, "early_in_round": False, "phase": "choose",
            "offer": None, "counter_amount": None, "events": [],
        },
        "actor": "player", "pending": None,
        "player_messages": [], "banker_messages": [],
        "retry_count": 0, "model_calls": 0,
        "status": "running", "result": None, "error": None,
    }
