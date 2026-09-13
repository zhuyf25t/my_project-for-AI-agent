"""纯规则校验：不持有牌局，不修改手牌。"""

from collections import Counter

from common import Action, Cards, Combo, DECK, Hand, RANK_INDEX, RuleError


def _consecutive(ranks: list[str]) -> bool:
    values = [RANK_INDEX[rank] for rank in ranks]
    return bool(values) and values[-1] <= RANK_INDEX["A"] and all(
        right == left + 1 for left, right in zip(values, values[1:])
    )


def _classify(cards: Cards) -> Combo:
    cards = tuple(sorted(cards, key=RANK_INDEX.__getitem__))
    counts = Counter(cards)
    ranks = list(counts)
    shape = tuple(sorted(counts.values()))

    if cards == ("X", "Y"):
        return Combo("王炸", RANK_INDEX["Y"], cards)
    if len(ranks) == 1 and len(cards) <= 4:
        kind = ("单张", "对子", "三张", "炸弹")[len(cards) - 1]
        return Combo(kind, RANK_INDEX[ranks[0]], cards)

    attachments = {
        (1, 3): ("三带一", 3),
        (2, 3): ("三带一对", 3),
        (1, 1, 4): ("四带二张", 4),
        (2, 4): ("四带二张", 4),
        (2, 2, 4): ("四带两对", 4),
    }
    if shape in attachments:
        kind, count = attachments[shape]
        body = next(rank for rank in ranks if counts[rank] == count)
        return Combo(kind, RANK_INDEX[body], cards)

    for width, minimum, kind in ((1, 5, "顺子"), (2, 3, "连对"), (3, 2, "连续三张")):
        if len(ranks) >= minimum and set(shape) == {width} and _consecutive(ranks):
            return Combo(kind, RANK_INDEX[ranks[-1]], cards)

    # README 要求飞机翅膀各不相同，所以所有三张只能属于连续主体。
    body = [rank for rank in ranks if counts[rank] == 3]
    wings = sorted(count for count in counts.values() if count != 3)
    if len(body) >= 2 and _consecutive(body):
        for width, kind in ((1, "飞机带单牌"), (2, "飞机带对子")):
            if wings == [width] * len(body):
                return Combo(kind, RANK_INDEX[body[-1]], cards)
    raise RuleError("COMBINATION", "全部出牌不能组成允许的牌型。")


def _beats(current: Combo, target: Combo) -> bool:
    if target.kind == "王炸":
        return False
    if current.kind == "王炸":
        return True
    if current.kind == "炸弹" and target.kind != "炸弹":
        return True
    return (
        current.kind == target.kind
        and len(current.cards) == len(target.cards)
        and current.high > target.high
    )


def judge(action: Action, hand: Hand, target: Combo | None = None) -> Combo | None:
    """通过返回牌型，合法 pass 返回 None；拒绝抛 RuleError。hand/target 必须来自主程序。"""
    if action.kind not in ("play", "pass"):
        raise RuleError("FORMAT", "动作只能是 play 或 pass。")
    if action.kind == "pass":
        if action.cards:
            raise RuleError("FORMAT", "pass() 不能携带牌。")
        if target is None:
            raise RuleError("PASS", "当前需要领出，不能不出。")
        return None
    if not action.cards:
        raise RuleError("COMBINATION", "play() 至少需要一张牌。")
    if any(card not in RANK_INDEX for card in action.cards):
        raise RuleError("FORMAT", "牌面只能使用 3 至 2、X、Y；10 表示一张十。")

    needed = Counter(action.cards)
    if needed - hand:
        raise RuleError("HAND", "手牌没有这些牌，或对应数量不足。")
    if needed - Counter(DECK):
        raise RuleError("COMBINATION", "普通牌最多四张，每种王最多一张。")
    current = _classify(action.cards)
    if target is not None and not _beats(current, target):
        if current.kind == target.kind and len(current.cards) == len(target.cards):
            raise RuleError("TOO_SMALL", "主体牌面必须严格大于当前目标。")
        raise RuleError("MISMATCH", "牌型或长度不匹配，且不构成炸弹压制。")
    return current
