"""终端观战排版；render 返回字符串，不打印、不修改牌局。"""

import json
import os
import sys
from io import StringIO
from shutil import get_terminal_size

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from common import Hand, PLAYERS, RANKS

MUTED = "#bccbc7"
GREEN = "bold #bce5bc"


def _cards(hand: Hand, width: int = 120) -> str:
    """组内逐张显示，组间留白；换行时尽量保留整个牌面组。"""
    lines, line = [], ""
    for rank in RANKS:
        group = " ".join([rank] * hand[rank])
        if not group:
            continue
        if line and len(line) + 3 + len(group) > width:
            lines.append(line)
            line = ""
        line += ("   " if line else "") + group
    return "\n".join([*lines, line]).strip() or "（已出完）"


def _seats(view: dict, width: int) -> Group | Table:
    across = width >= 110
    seat_width = (width - 4) // 3 if across else width
    hands = [_cards(view["hands"][name], max(1, seat_width - 6)) for name in PLAYERS]
    # 边框、上下留白和三行标题区共占 7 行；并排时按最长手牌统一高度。
    height = 7 + max(hand.count("\n") + 1 for hand in hands) if across else None
    panels = []
    for player_id, name, hand in zip("ABC", PLAYERS, hands):
        current = name == view["actor"]
        count = sum(view["hands"][name].values())
        remaining = f"仅剩 {count} 张" if 0 < count <= 3 else f"{count} 张"
        role = view.get("roles", {}).get(name, "待定")
        label = f"{'> ' if current else ''}{player_id} / {name}"
        # 避免 · 等宽度不固定的分隔符；身份靠左，张数在各席内靠右。
        metadata = Table.grid(expand=True)
        metadata.add_column(ratio=1)
        metadata.add_column(justify="right")
        metadata.add_row(Text(f"{role}{' / 当前' if current else ''}", style=MUTED),
                         Text(remaining, style="#f2cf8e" if 0 < count <= 3 else "#f2f5ed"))
        panels.append(Panel(
            Group(Text(label, style=GREEN if current else "bold"), metadata, Text(""),
                  Text(hand, style="bold")),
            box=box.ROUNDED, padding=(1 if across else 0, 2), height=height,
            style="#f2f5ed on #2e4741" if current else "#f2f5ed on #243438",
            border_style="#bce5bc" if current else "#536568",
        ))
    if not across:
        return Group(*panels)
    table = Table.grid(padding=(0, 1))
    for _ in PLAYERS:
        table.add_column(width=seat_width)
    table.add_row(*panels)
    return table


def _statistics(reply: dict, elapsed: float | None) -> str:
    def token(data, key):
        value = data.get(key) if isinstance(data, dict) else None
        return value if type(value) is int and value >= 0 else None

    usage = reply.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    incoming, outgoing = token(usage, "prompt_tokens"), token(usage, "completion_tokens")
    reasoning = token(usage.get("completion_tokens_details"), "reasoning_tokens")
    cached = token(usage, "prompt_cache_hit_tokens")
    if cached is None:
        cached = token(usage.get("prompt_tokens_details"), "cached_tokens")

    def number(value):
        return f"{value:,}" if value is not None else "未提供"

    duration = f"{elapsed:.1f} s" if elapsed is not None else "未提供"
    output = number(outgoing) + (f"（含推理 {number(reasoning)}）" if reasoning is not None else "")
    cache = f"{number(cached)} / {number(incoming)}"
    if cached is not None and incoming is not None and incoming > 0:
        cache += f" = {cached / incoming:.1%}"
    reason = reply.get("finish_reason")
    ending = {"stop": "正常结束", "length": "生成截断", None: "结束原因未提供"}.get(reason, f"结束原因：{reason}")
    return (f"调用 {duration} / 输入 {number(incoming)} / 输出 {output}\n"
            f"缓存命中 {cache} / {ending}")


def render(view: dict, *, width: int | None = None, color: bool | None = None) -> str:
    """kind 为 turn / result / notice；默认 turn。detail=True 展示完整回复与用量。

    turn 使用 hands、actor，以及可选 roles、phase、deal、turn、target、owner。
    result 使用 action 或 bid、before（行动前张数）、next_actor、winner；hands 为行动后手牌。
    notice 使用 event，可选 error。所有类型均可附 reply、elapsed；retry 不重绘手牌。
    width/color 可显式传入供测试；默认随终端，重定向或 NO_COLOR 环境下输出纯文本。
    观战快照包含全手牌，不能作为模型输入。
    """
    width = min(width if width is not None else get_terminal_size().columns, 120)
    if color is None:
        color = sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.getenv("TERM") != "dumb"
    output = StringIO()
    console = Console(file=output, width=width, force_terminal=color,
                      color_system="auto" if color else None, markup=False, highlight=False)
    kind, actor = view.get("kind", "turn"), view.get("actor", "")
    target = view.get("target")
    if kind == "turn":
        turn = view.get("turn", 1)
        bidding = view.get("phase") == "bid"
        stage = "叫地主" if bidding else f"第 {(turn - 1) // 3 + 1} 轮 / 第 {(turn - 1) % 3 + 1} 位"
        console.print(Text(f"第 {view.get('deal', 1):02d} 局 / {stage}    观战席", style=MUTED))
        mode = "叫分" if bidding else "跟牌" if target is not None else "自由领出"
        role = view.get("roles", {}).get(actor, "待定")
        console.print(Text(f"轮到 {actor}    /    {role} / {mode}", style=GREEN))
        if not bidding:
            message = (f"当前目标：{view['owner']} 的{target.kind}  {' '.join(target.cards)}"
                       if target is not None else "当前目标：无，本次必须出牌。")
            console.print(Text(message))
        console.print()
        console.print(_seats(view, width))
        console.print(Text(f"以上为行动前手牌 / 等待 {actor} 响应...", style=MUTED))
    elif kind == "result":
        console.print()
        if "bid" in view:
            console.print(Text(f"{actor} 叫 {view['bid']} 分  [已生效]", style=GREEN))
        else:
            action = view["action"]
            played = f"出  {' '.join(action.cards)}" if action.kind == "play" else "不出"
            after = sum(view["hands"][actor].values())
            console.print(Text(f"{actor} {played}  [已生效]    {view['before']} -> {after} 张", style=GREEN))
            if not view.get("winner"):
                message = (f"{'目标保留' if action.kind == 'pass' else '新目标'}："
                           f"{view['owner']} 的{target.kind}  {' '.join(target.cards)}"
                           if target is not None else "两人连续不出，目标已清空；下一位自由领出。")
                console.print(Text(message, style=MUTED))
        if view.get("winner"):
            console.print(Panel(Text(f"本局结束：{view['winner']}获胜", style=GREEN), border_style="green"))
        elif view.get("next_actor"):
            console.print(Text(f"下一位：{view['next_actor']}"))
    elif kind == "notice":
        console.print(Text(view["event"], style="bold yellow" if view.get("error") else GREEN))
    else:
        raise ValueError(f"未知输出类型：{kind}")
    if "reply" in view:
        reply = view["reply"]
        text = reply["text"].strip()
        if view.get("detail"):
            console.print(Text("完整回复：\n" + (text or "（空响应）")))
        elif kind == "result":
            # 只在主程序已确认协议合法后，去掉末行函数；保留其余可见说明。
            explanation = "\n".join(text.splitlines()[:-1]).strip()
            if explanation:
                console.print(Text("说明：" + explanation))
        console.print()
        console.print(Text(_statistics(reply, view.get("elapsed")), style=MUTED))
        if view.get("detail"):
            console.print(Text("原始用量：\n" + json.dumps(reply.get("usage"), ensure_ascii=False, indent=2)))
    return output.getvalue().rstrip("\n")
