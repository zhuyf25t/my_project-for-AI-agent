"""显示边界测试，不调用模型。"""
import unittest
from copy import deepcopy
from io import StringIO
from rich.console import Console
from display import SpectatorDisplay, prize_table, render_dashboard
from game import actor_for, apply_action
from state import initial_state


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


if __name__ == "__main__":
    unittest.main()
