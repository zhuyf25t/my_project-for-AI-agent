"""python -m unittest -v；不读真实 .env、不调用外部 API。"""

import json
import random
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import convert_to_openai_messages

from bot import AgentError, ChatAgent, DemoAgent, ModelConfig, load_configs, parse_reply
from game import RuleError, actor_for, analysis, apply_action, legal_actions, make_offer, public_view, remaining, tool_schemas
from main import RECURSION_LIMIT, build_graph
from state import PRIZES, ROUND_QUOTAS, initial_state


def act(game, name, **args):
    return apply_action(game, actor_for(game), name, {"reason": "测试公开理由", **args})


def early_stage():
    game, _ = act(initial_state(7)["game"], "choose_box", box_id=1)
    return act(game, "open_box", box_id=2)[0]


def raw_reply(name="choose_box", args=None, finish="tool_calls"):
    return {"choices": [{"finish_reason": finish, "message": {
        "content": None, "tool_calls": [{"id": "call_test", "type": "function", "function": {
            "name": name, "arguments": json.dumps(args if args is not None else {"box_id": 1, "reason": "测试"}),
        }}],
    }}]}


class RulesTests(unittest.TestCase):
    def test_shuffle_is_fixed_and_bijective(self):
        first = initial_state(42)["game"]
        self.assertEqual(first, initial_state(42)["game"])
        self.assertEqual(sorted(first["boxes"].values()), list(PRIZES))
        self.assertEqual(set(first["boxes"]), set(range(1, 21)))

    def test_invalid_actions_do_not_change_state(self):
        game = initial_state(7)["game"]
        before = deepcopy(game)
        cases = [("player", "choose_box", {"box_id": x, "reason": "测试"})
                 for x in (True, 1.5, "1", 0, 21)]
        cases += [("banker", "choose_box", {"box_id": 1, "reason": "测试"}),
                  ("player", "deal", {"reason": "测试"}),
                  ("player", "choose_box", {"box_id": 1, "reason": "测试", "hidden": 0}),
                  ("player", "choose_box", {"box_id": 1, "reason": " "})]
        for actor, name, args in cases:
            with self.subTest(actor=actor, name=name, args=args), self.assertRaises(RuleError):
                apply_action(game, actor, name, args)
            self.assertEqual(game, before)

    def test_cannot_open_own_or_reopen_box(self):
        game, _ = act(initial_state()["game"], "choose_box", box_id=1)
        with self.assertRaises(RuleError):
            act(game, "open_box", box_id=1)
        game, _ = act(game, "open_box", box_id=2)
        game, _ = act(game, "wait")
        with self.assertRaises(RuleError):
            act(game, "open_box", box_id=2)

    def test_wait_early_and_normal_offer_have_different_effects(self):
        game = early_stage()
        game, _ = act(game, "wait")
        self.assertEqual((game["phase"], game["early_calls"], game["early_in_round"]), ("open", 0, False))
        game, _ = act(game, "open_box", box_id=3)
        game, _ = act(game, "offer", level="low")
        self.assertEqual((game["early_calls"], game["early_in_round"]), (1, True))
        game, _ = act(game, "no_deal")
        self.assertEqual((game["round_no"], game["opened_in_round"]), (1, 2))
        for box in (4, 5, 6):
            game, _ = act(game, "open_box", box_id=box)
        self.assertEqual(game["phase"], "bank_normal")
        self.assertEqual(legal_actions(game), ["offer"])
        game, _ = act(game, "offer", level="base")
        self.assertEqual(game["offer"]["kind"], "normal")
        self.assertEqual(game["early_calls"], 1)
        game, _ = act(game, "no_deal")
        self.assertEqual((game["round_no"], game["opened_in_round"], game["early_in_round"]), (2, 0, False))
        game, _ = act(game, "open_box", box_id=7)
        game, _ = act(game, "offer", level="high")
        game, _ = act(game, "no_deal")
        self.assertEqual(game["early_calls"], 2)
        for box in (8, 9, 10):
            game, _ = act(game, "open_box", box_id=box)
        game, _ = act(game, "offer", level="base")
        game, _ = act(game, "no_deal")
        game, _ = act(game, "open_box", box_id=11)
        self.assertEqual((game["phase"], game["early_calls"], game["early_in_round"]), ("open", 2, False))

    def test_invalid_offer_does_not_consume_early_allowance(self):
        game = early_stage()
        for level in ("super_high", [], 3):
            with self.assertRaises(RuleError):
                act(game, "offer", level=level)
        self.assertEqual((game["phase"], game["early_calls"], game["early_in_round"]), ("bank_early", 0, False))

    def test_integer_pricing_and_minimum_protection(self):
        game = early_stage()
        expected = sum(remaining(game)) * 45 // (len(remaining(game)) * 100)
        self.assertEqual(make_offer(game, "base")["amount"], expected)
        game["boxes"], game["opened"], game["round_no"] = {1: 100, 2: 101}, [], 1
        offer = make_offer(game, "low")
        self.assertEqual((offer["raw_amount"], offer["amount"], offer["floor_applied"]), (40, 100, True))

    def test_counter_validation_and_single_use(self):
        game, _ = act(early_stage(), "offer", level="base")
        amount = game["offer"]["amount"]
        before = deepcopy(game)
        for invalid in (True, amount, amount + 0.5, max(remaining(game)) + 1):
            with self.assertRaises(RuleError):
                act(game, "counter", amount=invalid)
            self.assertEqual(game, before)
        game, _ = act(game, "counter", amount=amount + 1)
        game, _ = act(game, "keep_offer")
        self.assertEqual(game["offer"]["amount"], amount)
        self.assertIsNone(game["counter_amount"])
        self.assertEqual(legal_actions(game), ["deal", "no_deal"])
        with self.assertRaises(RuleError):
            act(game, "counter", amount=amount + 2)

    def test_counter_above_ev_is_binding(self):
        game, _ = act(early_stage(), "offer", level="base")
        amount = max(remaining(game))
        game, _ = act(game, "counter", amount=amount)
        game, result = act(game, "accept_counter")
        self.assertEqual(result, {"kind": "counter", "payout": amount, "own_amount": game["boxes"][1]})
        self.assertGreater(game["events"][-1]["details"]["analysis"]["offer_to_ev"], 1)
        self.assertEqual(legal_actions(game), [])

    def test_normal_deal_settles_and_cannot_continue(self):
        game, _ = act(early_stage(), "offer", level="high")
        amount = game["offer"]["amount"]
        game, result = act(game, "deal")
        self.assertEqual((game["phase"], result["payout"], result["kind"]), ("done", amount, "deal"))
        with self.assertRaises(RuleError):
            act(game, "deal")

    def test_analysis_example(self):
        stats = analysis([100, 1000, 10000, 100000], 18000)
        self.assertEqual(stats["ev"], 27775)
        self.assertEqual(stats["median"], 5500)
        self.assertEqual(stats["hold_box_counts"], {"below": 3, "equal": 0, "above": 1})
        self.assertEqual(sum(stats["hold_box_probabilities"].values()), 1)

    def test_hidden_permutation_does_not_change_public_input(self):
        game = early_stage()
        altered = deepcopy(game)
        altered["boxes"][1], altered["boxes"][3] = altered["boxes"][3], altered["boxes"][1]
        self.assertEqual(public_view(game), public_view(altered))
        view = public_view(game)
        self.assertNotIn("boxes", view)
        self.assertNotIn("seed", view)
        self.assertNotIn("own_amount", json.dumps(view))
        view["events"].clear()
        self.assertEqual(len(game["events"]), 2)

    def test_random_legal_games_preserve_invariants(self):
        for seed in range(100):
            rng = random.Random(seed)
            game = initial_state(seed)["game"]
            boxes = deepcopy(game["boxes"])
            for step in range(80):
                name = rng.choice(legal_actions(game))
                args = {}
                if name in ("choose_box", "open_box"):
                    args["box_id"] = rng.choice(public_view(game)["openable_boxes"])
                elif name == "offer":
                    args["level"] = rng.choice(["low", "base", "high"])
                elif name == "counter":
                    args["amount"] = rng.randint(game["offer"]["amount"] + 1, max(remaining(game)))
                game, result = act(game, name, **args)
                self.assertEqual(game["boxes"], boxes)
                self.assertEqual(len(game["opened"]), len(set(game["opened"])))
                self.assertNotIn(game["own_box"], game["opened"])
                self.assertEqual(sum(game["boxes"][box] for box in game["opened"]) + sum(remaining(game)), sum(PRIZES))
                self.assertLessEqual(game["early_calls"], 2)
                self.assertLessEqual(game["opened_in_round"], ROUND_QUOTAS[game["round_no"] - 1])
                early = [e for e in game["events"] if e["action"] == "offer" and e["details"]["kind"] == "early"]
                self.assertEqual(len(early), game["early_calls"])
                self.assertEqual(len({e["round_no"] for e in early}), len(early))
                if result:
                    break
            else:
                self.fail(f"seed {seed} 未在有限动作数内结算")


class RecordingDemo(DemoAgent):
    def __init__(self, actor):
        self.actor, self.requests = actor, []

    def invoke(self, messages, tools):
        self.requests.append((deepcopy(messages), deepcopy(tools)))
        response = super().invoke(messages, tools)
        response.content = f"private-text-{self.actor}"
        return response


class DeclineAgent(DemoAgent):
    def invoke(self, messages, tools):
        response = super().invoke(messages, tools)
        phase = json.loads(messages[-1].content)["phase"]
        if phase in ("bank_early", "offer"):
            response.tool_calls[0].update(name="wait" if phase == "bank_early" else "no_deal", args={"reason": "坚持开箱"})
        return response


class GraphTests(unittest.TestCase):
    def run_graph(self, player=None, banker=None, **options):
        graph = build_graph({"player": player or DemoAgent(), "banker": banker or DemoAgent()}, **options)
        return graph.invoke(initial_state(7), {"recursion_limit": RECURSION_LIMIT})

    def assert_history_paired(self, history):
        for index, message in enumerate(history):
            if isinstance(message, AIMessage):
                self.assertIsInstance(history[index + 1], ToolMessage)
                self.assertEqual(history[index + 1].tool_call_id, message.tool_calls[0]["id"])
            if isinstance(message, ToolMessage):
                self.assertIsInstance(history[index - 1], AIMessage)

    def test_three_node_graph_completes_full_demo(self):
        graph = build_graph({"player": DemoAgent(), "banker": DemoAgent()})
        self.assertEqual(set(graph.get_graph().nodes), {"__start__", "__end__", "player", "banker", "execute"})
        state = graph.invoke(initial_state(7), {"recursion_limit": RECURSION_LIMIT})
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["result"]["kind"], "counter")
        self.assertEqual((state["game"]["round_no"], len(state["game"]["opened"]), state["game"]["early_calls"]), (6, 18, 2))
        self.assertIsNone(state["pending"])

    def test_declining_final_offer_pays_box_and_stops(self):
        state = self.run_graph(DeclineAgent(), DeclineAgent())
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["result"]["kind"], "box")
        self.assertEqual(state["result"]["payout"], state["game"]["boxes"][1])
        self.assertEqual(state["game"]["early_calls"], 0)
        self.assertEqual([e["round_no"] for e in state["game"]["events"] if e["action"] == "offer"], [1, 2, 3, 4, 5, 6])

    def test_messages_are_paired_and_private_histories_are_separate(self):
        player, banker = RecordingDemo("player"), RecordingDemo("banker")
        state = self.run_graph(player, banker)
        for actor, agent, opponent in (("player", player, "banker"), ("banker", banker, "player")):
            self.assert_history_paired(state[f"{actor}_messages"])
            for messages, tools in agent.requests:
                wire = convert_to_openai_messages(messages)
                self.assertNotIn(f"private-text-{opponent}", json.dumps(wire))
                view = json.loads(messages[-1].content)
                self.assertEqual({tool["function"]["name"] for tool in tools}, set(view["legal_actions"]))
                self.assertNotIn("own_amount", json.dumps(wire))
                self.assertNotIn('"boxes"', json.dumps(view))

    def test_rule_error_corrects_without_losing_tool_pairing(self):
        demo = DemoAgent()
        calls = 0
        def invoke(messages, tools):
            nonlocal calls
            calls += 1
            reply = demo.invoke(messages, tools)
            if calls <= 2:
                reply.tool_calls[0]["args"]["box_id"] = 100
            return reply
        state = self.run_graph(Mock(invoke=invoke))
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["retry_count"], 0)
        self.assertEqual(len(state["game"]["events"]), 50)
        self.assertEqual(state["model_calls"], 52)
        self.assert_history_paired(state["player_messages"])
        failures = [m for m in state["player_messages"] if isinstance(m, ToolMessage) and not json.loads(m.content)["ok"]]
        self.assertEqual(len(failures), 2)

    def test_empty_multiple_truncated_or_invalid_calls_stop_after_three(self):
        valid = DemoAgent().invoke([HumanMessage(content=json.dumps(public_view(initial_state()["game"])))], [])
        replies = [AIMessage(content="我想选 1 号箱"),
                   AIMessage(content="", tool_calls=valid.tool_calls * 2),
                   AIMessage(content="", tool_calls=valid.tool_calls, response_metadata={"finish_reason": "length"}),
                   parse_reply(raw_reply(args="not an object"))]
        for reply in replies:
            with self.subTest(reply=reply):
                state = self.run_graph(Mock(invoke=Mock(return_value=reply)))
                self.assertEqual((state["status"], state["model_calls"], state["retry_count"]), ("error", 3, 3))
                self.assertIsNone(state["result"])
                self.assertEqual(state["game"], initial_state(7)["game"])
                self.assertFalse(any(isinstance(m, AIMessage) for m in state["player_messages"]))

    def test_repeated_tool_id_does_not_execute_again(self):
        demo = DemoAgent()
        def invoke(messages, tools):
            reply = demo.invoke(messages, tools)
            reply.tool_calls[0]["id"] = "reused"
            return reply
        state = self.run_graph(Mock(invoke=invoke))
        self.assertEqual((state["status"], state["model_calls"]), ("error", 4))
        self.assertEqual(state["game"]["opened"], [])
        self.assert_history_paired(state["player_messages"])

    def test_call_cap_and_api_error_do_not_settle(self):
        for state in (self.run_graph(max_model_calls=2),
                      self.run_graph(Mock(invoke=Mock(side_effect=AgentError("测试网络错误"))))):
            self.assertEqual(state["status"], "error")
            self.assertIsNone(state["result"])
            self.assertIsNone(state["pending"])


class ApiTests(unittest.TestCase):
    def test_parse_tool_response_and_reject_bad_json(self):
        self.assertEqual(parse_reply(raw_reply()).tool_calls[0]["args"]["box_id"], 1)
        raw = raw_reply()
        raw["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{"
        for value in (raw, {}, [], raw_reply(finish="length")):
            self.assertIn("protocol_error", parse_reply(value).response_metadata)

    @patch("bot.time.sleep")
    @patch("bot.HTTPSConnection")
    def test_transient_retry_sends_native_tools_and_paired_messages(self, factory, sleep):
        responses = [Mock(status=503, read=Mock(return_value=b"busy")),
                     Mock(status=200, read=Mock(return_value=json.dumps(raw_reply()).encode()))]
        factory.return_value.getresponse.side_effect = responses
        config = ModelConfig("test-secret", "https://example.invalid/v1", "test-model")
        ai = parse_reply(raw_reply())
        messages = [SystemMessage(content="测试"), HumanMessage(content="开始"), ai,
                    ToolMessage(content='{"ok": true}', tool_call_id="call_test"), HumanMessage(content="继续")]
        reply = ChatAgent(config).invoke(messages, tool_schemas(initial_state()["game"]))
        self.assertEqual(reply.tool_calls[0]["name"], "choose_box")
        self.assertEqual(factory.call_count, 2)
        sleep.assert_called_once_with(1)
        args = factory.return_value.request.call_args.args
        payload = json.loads(args[2])
        self.assertEqual(args[1], "/v1/chat/completions")
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(payload["messages"][3]["tool_call_id"], "call_test")
        self.assertNotIn("thinking", payload)
        self.assertNotIn("test-secret", json.dumps(payload))

    @patch("bot.time.sleep")
    @patch("bot.HTTPSConnection")
    def test_http_retries_are_bounded_and_errors_redact_response(self, factory, sleep):
        for status, expected in ((401, 1), (429, 3), (500, 3)):
            factory.reset_mock()
            factory.return_value.getresponse.return_value = Mock(status=status, read=Mock(return_value=b"test-secret"))
            with self.assertRaises(AgentError) as caught:
                ChatAgent(ModelConfig("test-secret", "https://example.invalid/v1", "test-model")).invoke([], [])
            self.assertEqual(factory.call_count, expected)
            self.assertNotIn("test-secret", str(caught.exception))

    @patch("bot.load_dotenv")
    def test_config_role_overrides_common_and_never_reads_real_env(self, loader):
        values = {"OPENAI_API_KEY": "test-secret", "OPENAI_BASE_URL": "https://example.invalid/v1",
                  "OPENAI_MODEL": "common", "BANKER_MODEL": "banker-model"}
        with patch.dict("os.environ", values, clear=True):
            configs = load_configs()
        self.assertEqual(configs["player"].model, "common")
        self.assertEqual(configs["banker"].model, "banker-model")
        self.assertNotIn("test-secret", repr(configs))
        self.assertEqual(loader.call_args.args[0].name, ".env")
        self.assertFalse(loader.call_args.kwargs["override"])


if __name__ == "__main__":
    unittest.main()
