"""显示边界测试，不调用模型。"""
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
from unittest.mock import patch
from rich.console import Console
from display import SpectatorDisplay, prize_table, render_dashboard
from game import actor_for, apply_action
from main import main
from state import initial_state
from test_game import RecordingAgent


def sample():
    state = initial_state(7)
    for name, args in (("choose_box", {"box_id": 18}), ("open_box", {"box_id": 2}),
                       ("offer", {"level": "base"}), ("counter", {"amount": 40000})):
        state["game"], state["result"] = apply_action(
            state["game"], actor_for(state["game"]), name,
            {"reason": "[bold]公开理由原文：比较确定收入和剩余奖金，再作决定。", **args})
        state["actor"] = actor_for(state["game"])
    return state


def render(state, width=160):
    output = StringIO()
    console = Console(file=output, width=width, height=50, force_terminal=False)
    console.print(render_dashboard(state, console))
    return output.getvalue()


class DisplayTests(unittest.TestCase):
    def test_hidden_permutations_do_not_change_display(self):
        first = sample()
        second = deepcopy(first)
        boxes = second["game"]["boxes"]
        boxes[1], boxes[18] = boxes[18], boxes[1]
        for status in ("running", "error"):
            first["status"] = second["status"] = status
            self.assertEqual(render(first), render(second))

    def test_prize_ladder_marks_only_opened(self):
        table = list(prize_table(sample()).renderable.renderables)[0]
        cells = [cell for col in table.columns for cell in col._cells]
        self.assertEqual(len(cells), 20)
        removed = [cell for cell in cells if "strike" in str(cell.style)]
        self.assertEqual([cell.plain for cell in removed], ["x 50,000"])

    def test_widths_and_literal_reason(self):
        for width in (60, 100, 140, 160):
            text = render(sample(), width)
            for fragment in ("[bold]", "公开理由", "奖金表", "箱子", "40,000", "再作决定"):
                self.assertIn(fragment, text)

    def test_long_reason_is_marked_and_preserved(self):
        state = sample()
        reason = "先考虑确定收入，再考虑承担风险的意愿。" * 20
        state["game"]["events"][-1]["reason"] = reason
        self.assertIn("已截短", render(state))
        self.assertEqual(state["game"]["events"][-1]["reason"], reason)

    def test_pipe_output_does_not_duplicate_final_event(self):
        output = StringIO()
        console = Console(file=output, width=100, force_terminal=False)
        with SpectatorDisplay(initial_state(), console=console) as display:
            display.update(sample())
            display.update(sample(), final=True)
        self.assertEqual(output.getvalue().count("公开理由原文"), 1)
        self.assertNotIn("\x1b[", output.getvalue())

    def test_quiet_mode_has_no_dashboard(self):
        output = StringIO()
        console = Console(file=output, width=160, force_terminal=True)
        with SpectatorDisplay(sample(), enabled=False, console=console) as display:
            display.update(sample())
        self.assertEqual(output.getvalue(), "")

    def test_terminal_waits_below_static_dashboard(self):
        output = StringIO()
        console = Console(file=output, width=160, height=60, force_terminal=True,
                          legacy_windows=False, color_system=None)
        with patch("display.Live") as live, patch("builtins.input", return_value="") as read:
            with SpectatorDisplay(initial_state(), console=console, pause_after_action=True) as display:
                display.update(sample())
                display.update(sample(), final=True)
        live.assert_not_called()
        read.assert_called_once_with()
        text = output.getvalue()
        self.assertIn("等待回车", text)
        self.assertGreater(text.index("按回车继续下一回合"), text.rindex("最近三步"))

    def test_cli_waits_after_each_action_before_next_model_call(self):
        for quiet in (False, True):
            with self.subTest(quiet=quiet):
                output = StringIO()
                console = Console(file=output, force_terminal=False, width=160)
                agents = {role: RecordingAgent(role) for role in ("player", "banker")}
                snapshots = []

                def read():
                    calls = sum(len(agent.requests) for agent in agents.values())
                    # 第 N 次回车之前，只能有 N 次模型调用，下一回合不能提前运行。
                    self.assertEqual(calls, len(snapshots) + 1)
                    self.assertIn(f"第 {calls} 次行动已完成", output.getvalue())
                    if not quiet:
                        self.assertEqual(output.getvalue().count("公开理由："), calls)
                    snapshots.append(calls)
                    return ""

                args = ["main.py", "--seed", "7"] + (["--quiet"] if quiet else [])
                with patch("main.load_configs", return_value=agents), \
                     patch("main.ChatAgent", side_effect=lambda agent: agent), \
                     patch("display.Console", return_value=console), \
                     patch("sys.argv", args), patch("builtins.input", side_effect=read), \
                     redirect_stdout(StringIO()) as result:
                    code = main()
                self.assertEqual(code, 0)
                self.assertEqual(snapshots, list(range(1, 50)))
                self.assertEqual(sum(len(a.requests) for a in agents.values()), 50)
                self.assertIn("49,876", result.getvalue())

    def test_closed_input_or_interrupt_never_advances_to_next_action(self):
        for failure, expected in ((EOFError, 1), (KeyboardInterrupt, 130)):
            with self.subTest(failure=failure):
                agents = {role: RecordingAgent(role) for role in ("player", "banker")}
                console = Console(file=StringIO(), force_terminal=False)
                with patch("main.load_configs", return_value=agents), \
                     patch("main.ChatAgent", side_effect=lambda agent: agent), \
                     patch("display.Console", return_value=console), \
                     patch("sys.argv", ["main.py"]), \
                     patch("builtins.input", side_effect=failure), redirect_stdout(StringIO()) as result:
                    code = main()
                self.assertEqual(code, expected)
                self.assertEqual(len(agents["player"].requests), 1)
                self.assertEqual(len(agents["banker"].requests), 0)
                self.assertNotIn("最终揭晓", result.getvalue())


if __name__ == "__main__":
    unittest.main()
