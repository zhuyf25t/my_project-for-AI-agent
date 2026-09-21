"""纯 Python 裁判：模型选动作，程序计算金额并原子地提交合法动作。"""

from copy import deepcopy
from statistics import median

from state import Actor, BASE_PERCENT, GameData, Offer, PRIZES, Result, ROUND_QUOTAS


class RuleError(ValueError):
    """可反馈给模型的公开规则错误；消息中不包含隐藏金额。"""


def actor_for(game: GameData) -> Actor:
    return "banker" if game["phase"] in ("bank_early", "bank_normal", "counter") else "player"


def legal_actions(game: GameData) -> list[str]:
    actions = {
        "choose": ["choose_box"], "open": ["open_box"],
        "bank_early": ["offer", "wait"], "bank_normal": ["offer"],
        "counter": ["accept_counter", "keep_offer"], "done": [],
    }
    if game["phase"] == "offer":
        offer = game["offer"]
        assert offer is not None
        can_counter = not offer["counter_used"] and offer["amount"] < max(remaining(game))
        return ["deal", "no_deal"] + (["counter"] if can_counter else [])
    return actions[game["phase"]]


def remaining(game: GameData) -> list[int]:
    return sorted(amount for box, amount in game["boxes"].items() if box not in game["opened"])


def analysis(amounts: list[int], offer: int | None = None) -> dict:
    """比较的是最终持箱到结束的金额，不是含未来报价的继续游戏价值。"""
    n, total = len(amounts), sum(amounts)
    stats = {"remaining_amounts": sorted(amounts), "count": n, "sum": total,
             "min": min(amounts), "max": max(amounts), "median": median(amounts),
             "ev": total / n}
    if offer is not None:
        counts = {"below": sum(x < offer for x in amounts),
                  "equal": sum(x == offer for x in amounts),
                  "above": sum(x > offer for x in amounts)}
        stats.update({"offer": offer, "offer_to_ev": offer * n / total,
                      "offer_minus_ev": offer - total / n,
                      "hold_box_counts": counts,
                      "hold_box_probabilities": {key: value / n for key, value in counts.items()}})
    return stats


def make_offer(game: GameData, level: str) -> Offer:
    amounts = remaining(game)
    percent = BASE_PERCENT[game["round_no"] - 1] + {"low": -5, "base": 0, "high": 5}[level]
    # 全程整数运算；展示出来的两位小数不参与报价计算。
    raw = sum(amounts) * percent // (len(amounts) * 100)
    return {"amount": max(min(amounts), raw),
            "kind": "early" if game["phase"] == "bank_early" else "normal",
            "level": level, "percent": percent, "raw_amount": raw,
            "floor_applied": raw < min(amounts), "counter_used": False}


def public_view(game: GameData) -> dict:
    """按白名单构造公开视图。未开箱只发编号与排序奖金，不发对应关系。"""
    offer = game["offer"]
    view = {
        "phase": game["phase"], "round_no": game["round_no"],
        "own_box": game["own_box"], "prizes": list(PRIZES),
        "opened": [{"box_id": box, "amount": game["boxes"][box]} for box in game["opened"]],
        "unopened_boxes": [box for box in game["boxes"] if box not in game["opened"]],
        "openable_boxes": [box for box in game["boxes"]
                           if box not in game["opened"] and box != game["own_box"]],
        "opened_in_round": game["opened_in_round"],
        "round_quota": ROUND_QUOTAS[game["round_no"] - 1],
        "early_calls": game["early_calls"], "early_in_round": game["early_in_round"],
        "offer": deepcopy(offer), "counter_amount": game["counter_amount"],
        "analysis": analysis(remaining(game), offer["amount"] if offer else None),
        "legal_actions": legal_actions(game), "events": deepcopy(game["events"]),
    }
    if game["phase"] in ("bank_early", "bank_normal"):
        view["candidate_offers"] = {level: make_offer(game, level) for level in ("low", "base", "high")}
    if game["counter_amount"] is not None:
        view["counter_analysis"] = analysis(remaining(game), game["counter_amount"])
    return view


ACTION_PARAMETERS = {
    "choose_box": {"box_id": {"type": "integer", "minimum": 1, "maximum": 20}},
    "open_box": {"box_id": {"type": "integer", "minimum": 1, "maximum": 20}},
    "offer": {"level": {"type": "string", "enum": ["low", "base", "high"]}},
    "counter": {"amount": {"type": "integer", "minimum": 1}},
    "wait": {}, "deal": {}, "no_deal": {}, "accept_counter": {}, "keep_offer": {},
}
DESCRIPTIONS = {
    "choose_box": "选择自己的保留箱。", "open_box": "打开一个其他未开箱。",
    "offer": "选择报价档位；由程序算钱。", "wait": "放弃当前开箱间隙的提前报价机会。",
    "deal": "接受当前报价，立即成交。", "no_deal": "拒绝当前报价，按规则继续或最终开箱。",
    "counter": "提出一次还价；银行家接受即成交。",
    "accept_counter": "接受选手还价，立即成交。", "keep_offer": "拒绝还价，保留原报价。",
}


def tool_schemas(game: GameData) -> list[dict]:
    tools = []
    for name in legal_actions(game):
        properties = deepcopy(ACTION_PARAMETERS[name])
        properties["reason"] = {"type": "string", "minLength": 1, "maxLength": 400,
                                "description": "一句简短的公开理由，事实与推测分开，不输出内部思维过程。"}
        tools.append({"type": "function", "function": {
            "name": name, "description": DESCRIPTIONS[name],
            "parameters": {"type": "object", "properties": properties,
                           "required": list(properties), "additionalProperties": False},
        }})
    return tools


def apply_action(game: GameData, actor: Actor, name: str, args: dict) -> tuple[GameData, Result | None]:
    """先完整校验，再在副本上执行；失败不会扣额度或修改原状态。"""
    if actor != actor_for(game) or name not in legal_actions(game):
        raise RuleError("当前身份或阶段不允许此动作，请使用 legal_actions 中的动作。")
    expected = set(ACTION_PARAMETERS[name]) | {"reason"}
    if not isinstance(args, dict) or set(args) != expected:
        raise RuleError("参数字段必须与当前工具定义完全一致。")
    reason = args["reason"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 400:
        raise RuleError("reason 必须是 1 至 400 字符的简短公开理由。")
    if name in ("choose_box", "open_box"):
        box = args["box_id"]
        if type(box) is not int or box not in game["boxes"]:
            raise RuleError("箱号必须是 1 至 20 的整数。")
        if name == "open_box" and (box == game["own_box"] or box in game["opened"]):
            raise RuleError("只能打开其他尚未打开的箱子，不能打开自己的箱子。")
    if name == "offer" and (not isinstance(args["level"], str) or args["level"] not in ("low", "base", "high")):
        raise RuleError("报价档位只能是 low、base、high。")
    if name == "counter":
        amount, offer = args["amount"], game["offer"]
        assert offer is not None
        if type(amount) is not int or not offer["amount"] < amount <= max(remaining(game)):
            raise RuleError("还价必须是整数，严格高于原报价且不超过剩余最高奖金。")

    updated = deepcopy(game)
    result = None
    details = {key: value for key, value in args.items() if key != "reason"}

    def settle(kind: str, payout: int) -> Result:
        updated["phase"] = "done"
        return {"kind": kind, "payout": payout,
                "own_amount": updated["boxes"][updated["own_box"]]}

    if name == "choose_box":
        updated["own_box"], updated["phase"] = args["box_id"], "open"
    elif name == "open_box":
        updated["opened"].append(args["box_id"])
        updated["opened_in_round"] += 1
        details["amount"] = updated["boxes"][args["box_id"]]
        if updated["opened_in_round"] == ROUND_QUOTAS[updated["round_no"] - 1]:
            updated["phase"] = "bank_normal"
        elif updated["early_calls"] < 2 and not updated["early_in_round"]:
            updated["phase"] = "bank_early"
        else:
            updated["phase"] = "open"
    elif name == "wait":
        updated["phase"] = "open"
    elif name == "offer":
        offer = make_offer(updated, args["level"])
        updated["offer"], updated["counter_amount"], updated["phase"] = offer, None, "offer"
        if offer["kind"] == "early":
            updated["early_calls"] += 1
            updated["early_in_round"] = True
        details.update(deepcopy(offer))
        details["analysis"] = analysis(remaining(updated), offer["amount"])
    elif name == "deal":
        result = settle("deal", updated["offer"]["amount"])
    elif name == "no_deal":
        offer = updated["offer"]
        updated["offer"], updated["counter_amount"] = None, None
        if offer["kind"] == "early":
            updated["phase"] = "open"
        elif updated["round_no"] == len(ROUND_QUOTAS):
            result = settle("box", updated["boxes"][updated["own_box"]])
        else:
            updated["round_no"] += 1
            updated["opened_in_round"] = 0
            updated["early_in_round"] = False
            updated["phase"] = "open"
    elif name == "counter":
        updated["counter_amount"] = args["amount"]
        updated["offer"]["counter_used"] = True
        updated["phase"] = "counter"
        details["analysis"] = analysis(remaining(updated), args["amount"])
    elif name == "accept_counter":
        result = settle("counter", updated["counter_amount"])
        details["analysis"] = analysis(remaining(updated), updated["counter_amount"])
    elif name == "keep_offer":
        updated["counter_amount"], updated["phase"] = None, "offer"
    if result is not None:
        details["result"] = result
    updated["events"].append({"number": len(game["events"]) + 1, "round_no": game["round_no"],
                              "actor": actor, "action": name, "reason": reason.strip(), "details": details})
    return updated, result
