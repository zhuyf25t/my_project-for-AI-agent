"""三个模块共用的数据类型与牌面常量。"""

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

PLAYERS = ("Alice", "Bob", "Corleone")
RANKS = tuple("3 4 5 6 7 8 9 10 J Q K A 2 X Y".split())
RANK_INDEX = {rank: index for index, rank in enumerate(RANKS)}
DECK = tuple(rank for rank in RANKS[:-2] for _ in range(4)) + ("X", "Y")

Cards: TypeAlias = tuple[str, ...]
Hand: TypeAlias = Counter[str]
Reply: TypeAlias = dict[str, Any]  # text、finish_reason、usage；不丢弃生成诊断信息。


@dataclass(frozen=True)
class Action:
    kind: Literal["play", "pass"]
    cards: Cards = ()


@dataclass(frozen=True)
class Combo:
    """kind 是中文牌型名；high 是主体最大牌面在 RANKS 中的下标。"""

    kind: str
    high: int
    cards: Cards


class RuleError(ValueError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"[{code}] {message}")
