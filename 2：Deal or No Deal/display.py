"""观战显示：箱子、双方公开理由、两列奖金表；不参与游戏裁判。"""

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from game import analysis, remaining
from state import GameState, PRIZES, ROUND_QUOTAS

NAMES = {"player": "选手 AI", "banker": "银行家 AI"}
COLORS = {"player": "green", "banker": "yellow"}


def safe_line(text: str) -> str:
    return "".join(char if char.isprintable() else " " for char in text)


def action_text(event: dict) -> str:
    action, d = event["action"], event["details"]
    descriptions = {
        "choose_box": f"保留 {d.get('box_id')} 号箱",
        "open_box": f"打开 {d.get('box_id')} 号箱，排除 {d.get('amount', 0):,}",
        "offer": f"{'提前来电' if d.get('kind') == 'early' else '轮末报价'} {d.get('amount', 0):,}",
        "wait": "暂不来电，继续开箱",
        "deal": "DEAL！接受报价",
        "no_deal": "NO DEAL！拒绝报价",
        "counter": f"还价 {d.get('amount', 0):,}",
        "accept_counter": "接受还价，立即成交",
        "keep_offer": "拒绝还价，保留原价",
    }
    return descriptions[action]


def box_board(state: GameState) -> Panel:
    game = state["game"]
    table = Table(box=box.SIMPLE, show_header=False, padding=0, leading=1, expand=True)
    for _ in range(5):
        table.add_column(justify="center", ratio=1)
    for start in range(1, 21, 5):
        row = []
        for number in range(start, start + 5):
            own = number == game["own_box"]
            opened = number in game["opened"]
            visible = opened or state["status"] == "completed"
            label = f"[{number:02d}{'*' if own else ''}]\n"
            label += f"{game['boxes'][number]:,}" if visible else "?"
            row.append(Text(label, style="bold green" if own else "dim" if opened else "", justify="center"))
        table.add_row(*row)
    return Panel(Group(table, Text("* 自留箱  灰色已开\n未开箱金额保密", style="dim")),
                 title="箱子", border_style="cyan")


def prize_table(state: GameState) -> Panel:
    game = state["game"]
    excluded = {game["boxes"][number] for number in game["opened"]}
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(justify="right", ratio=1)
    table.add_column(justify="right", ratio=1)
    for low, high in zip(PRIZES[:10], PRIZES[10:]):
        cells = []
        for amount, color in ((low, "cyan"), (high, "yellow")):
            gone = amount in excluded
            cells.append(Text(f"{'x' if gone else '·'} {amount:,}",
                              style="dim strike" if gone else f"bold {color}"))
        table.add_row(*cells)
    return Panel(Group(table, Text(f"\n仍余 {20-len(excluded)} 项\nx 已排除；与箱号不对应", style="dim")),
                 title="奖金表", border_style="yellow")


def reason_panel(state: GameState, actor: str, console: Console, width: int) -> Panel:
    event = next((e for e in reversed(state["game"]["events"]) if e["actor"] == actor), None)
    if event is None:
        content = Text("尚未提交行动与公开理由。", style="dim")
    else:
        heading = Text(f"第 {event['round_no']} 轮 · #{event['number']}\n{action_text(event)}\n",
                       style=f"bold {COLORS[actor]}")
        # 只展示已执行事件中的模型原文，Text 不把方括号等内容解释为样式。
        lines = Text(safe_line(event["reason"])).wrap(console, max(12, width - 4), overflow="fold")
        reason = Text("\n").join(lines[:4])
        reason.overflow = "fold"
        reason.no_wrap = False
        if len(lines) > 4:
            reason.append("\n…（已截短，完整理由可用 --record 保存）", style="dim")
        content = Group(heading, reason)
    return Panel(content, title=f"{NAMES[actor]} · 最近公开理由", border_style=COLORS[actor])


def render_dashboard(state: GameState, console: Console, waiting: bool = False) -> Group:
    game = state["game"]
    wide = console.width >= 140
    middle_width = console.width - 78 if wide else console.width
    if state["status"] == "completed":
        status = "本局已结算"
    elif state["status"] == "error":
        status = "对局中止：" + safe_line(state["error"] or "未知错误")
    elif waiting:
        status = f"等待回车 · 下一步由{NAMES[state['actor']]}行动"
    else:
        status = f"{NAMES[state['actor']]} 决策中"
    heading = Text(f"DEAL OR NO DEAL  |  {status}\n", style="bold cyan")
    heading.append(
        f"第 {game['round_no']}/6 轮   本轮 {game['opened_in_round']}/{ROUND_QUOTAS[game['round_no']-1]}"
        f"   未开 {20-len(game['opened'])} 箱   自留 {game['own_box'] or '未选'}"
        f"   提前来电 {game['early_calls']}/2", style="white")
    offer = game["offer"]
    if state["status"] == "completed":
        focus = f"已结算：{state['result']['payout']:,} 虚拟币"
    elif offer:
        focus = f"当前报价 {offer['amount']:,}"
        if game["counter_amount"] is not None:
            focus += f"  |  还价 {game['counter_amount']:,}"
        else:
            focus += f"  |  可还价 {'0' if offer['counter_used'] else '1'} 次"
    else:
        focus = "当前无有效报价"
    facts = analysis(remaining(game), offer["amount"] if offer else None)
    focus += f"\n持箱均值 {facts['ev']:,.2f}"
    if offer:
        focus += f"  |  报价/均值 {facts['offer_to_ev']:.2%}"
    center = Group(
        Panel(Text(focus), title="当前谈判", border_style="blue"),
        reason_panel(state, "banker", console, middle_width),
        reason_panel(state, "player", console, middle_width),
    )
    if wide:
        body = Table.grid(expand=True, padding=(0, 1))
        body.add_column(width=43, overflow="fold")
        body.add_column(ratio=1, overflow="fold")
        body.add_column(width=29, overflow="fold")
        body.add_row(box_board(state), center, prize_table(state))
    else:
        body = Group(center, box_board(state), prize_table(state))
    recent = Text()
    for event in game["events"][-3:]:
        recent.append(f"#{event['number']} {NAMES[event['actor']]}：{action_text(event)}\n",
                      style=COLORS[event["actor"]])
    if not game["events"]:
        recent.append("等待选手选择自留箱。")
    else:
        recent.rstrip()
    return Group(Panel(heading, border_style="cyan"), body,
                 Panel(recent, title="最近三步", border_style="dim"),
                 Text("只展示已提交的公开理由。持箱均值不包含未来报价；--record 可保存完整理由。", style="dim"))


def show_event(state: GameState, console: Console | None = None) -> None:
    """输出到文件/管道时保留简洁事件流，避免每步重复整张牌桌。"""
    console = console or Console()
    event = state["game"]["events"][-1]
    console.print(Text(f"[{event['number']:02d} / 第 {event['round_no']} 轮] "
                       f"{NAMES[event['actor']]}：{action_text(event)}", style=COLORS[event["actor"]]))
    console.print(Text("  公开理由：" + safe_line(event["reason"])))


class SpectatorDisplay:
    """逐步观战时打印静态快照并等待回车；其余情况支持固定刷新。"""

    def __init__(self, state: GameState, enabled: bool = True, console: Console | None = None,
                 pause_after_action: bool = False):
        self.state, self.enabled = state, enabled
        self.console = console or Console()
        self.pause_after_action = pause_after_action
        self.live = None

    def __enter__(self):
        if self.enabled and self.console.is_terminal:
            frame = render_dashboard(self.state, self.console)
            if not self.pause_after_action and self._fits(frame):
                self.live = Live(frame, console=self.console, refresh_per_second=2)
                self.live.start()
            else:
                self.console.print(frame)
        return self

    def _fits(self, frame) -> bool:
        return (self.console.width >= 140 and
                len(self.console.render_lines(frame, self.console.options)) + 1 <= self.console.height)

    def update(self, state: GameState, final: bool = False) -> None:
        self.state = state
        waiting = (self.pause_after_action and not final and state["status"] == "running"
                   and bool(state["game"]["events"]))
        if self.enabled and self.console.is_terminal:
            frame = render_dashboard(state, self.console, waiting=waiting)
            if self.live and not self._fits(frame):
                self.live.stop()
                self.live = None
            if self.live:
                self.live.update(frame, refresh=True)
            elif not final:
                self.console.print(frame)
        elif self.enabled and not final and state["game"]["events"]:
            show_event(state, self.console)
        if waiting:
            # 同步阻塞 execute 的事件回调，回车前不会发起下一次模型请求。
            # 不根据 is_terminal 跳过暂停；stdin 关闭时让主程序明确中止。
            number = state["game"]["events"][-1]["number"]
            self.console.input(f"第 {number} 次行动已完成。按回车继续下一回合"
                               f"（{NAMES[state['actor']]}）；Ctrl+C 退出：")
            self.console.print(Text(f"已继续，等待{NAMES[state['actor']]}决策……", style="dim"))

    def __exit__(self, exc_type, exc, traceback):
        if self.live:
            if exc is not None:
                interrupted = {**self.state, "status": "error", "error": "运行已中止，未进行结算"}
                self.live.update(render_dashboard(interrupted, self.console), refresh=True)
            self.live.stop()


def show_result(state: GameState) -> None:
    print(f"\n{'=' * 58}\nAgent 决策调用：{state['model_calls']} 次（不含 HTTP 内部重试）")
    if state["status"] != "completed":
        print(f"对局未完成：{safe_line(state['error'] or '未知错误')}；未进行奖金结算。")
        return
    result, game = state["result"], state["game"]
    print(f"结算方式：{ {'deal': '接受原报价', 'counter': '还价成交', 'box': '领取箱内奖金'}[result['kind']] }")
    print(f"选手获得 / 银行家支出：{result['payout']:,} 虚拟币")
    print(f"自己的 {game['own_box']} 号箱实际为 {result['own_amount']:,}"
          f" | 实得 − 箱值 = {result['payout'] - result['own_amount']:+,}")
    print("  这是事后对照，不能据此判定当时的策略一定正确或错误。")
    print("  最终揭晓：" + " | ".join(f"{box}号={amount:,}" for box, amount in game["boxes"].items()))
