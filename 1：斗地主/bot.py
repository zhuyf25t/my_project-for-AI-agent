"""独立玩家上下文、模型请求与末行协议解析；环境配置由主程序加载后传入。"""

import json
import re
import time
from collections import Counter
from collections.abc import Callable
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from urllib.parse import urlsplit

from common import Action, Cards, DECK, PLAYERS, RANK_INDEX, Reply, RuleError

SYSTEM_PROMPT = """你是三人斗地主的一位玩家，座位顺序固定为 Alice、Bob、Corleone。
你只知道自己的初始17张牌与完整公开牌桌。根据公开底牌和已接受的出牌推导剩余手牌；
被拒动作不扣牌。地主自己先出完获胜；任一农民先出完，两位农民共同获胜。
叫地主阶段身份待定，按座位各叫一次0/1/2，最高非零分获地主，同分先叫者优先；
全0重新发牌。地主公开领取3张底牌并首先领出。
牌面：3 < 4 < 5 < 6 < 7 < 8 < 9 < 10 < J < Q < K < A < 2 < X < Y。
普通牌各4张，X小王和Y大王各1张。牌面用空格或 | 分隔，一个10是一张牌。
允许牌型：单张、对子、三张、三带一、三带一对、顺子、连对、连续三张、
飞机带单牌、飞机带对子、四带二张、四带两对、炸弹、王炸。
顺子至少5个点数，连对至少3个点数，连续三张和飞机主体至少2个点数；
这些连续主体仅允许3至A，不含2或王。飞机主体点数在整手出牌中各恰好3张；
每组三张带1张单牌或1个对子，翅膀点数互不相同，也不能与主体相同。
三带一/一对的附带点数不同于主体。四带二张允许带一个对子；四带两对须两个不同对子。
四张加一单一对不合法，不能把四张拆作两个附带对子。附带牌可用2；单牌可用王，
两王可同时附带，但不能作对子，附带的两王不具有王炸效果。
王炸仅为X Y，最大；炸弹仅为四张同点数，压所有普通牌型，炸弹间比较点数。
其余必须同牌型、同长度且主体严格更大；附带牌不参与大小比较。
领出时不能pass；跟牌时可以pass。一次pass保留目标，两次连续pass后原出牌者重新领出。
只提出动作，不自行扣牌或宣布动作生效；以牌桌的接受/拒绝事件和错误反馈为准。
可以先给出可见说明，最后一个非空行必须是唯一的协议行，不使用代码围栏：
叫地主：Bid: 0 或 Bid: 1 或 Bid: 2
出牌：Action: play(3 3 3 | 4) 或 Action: pass()
根据当前阶段只使用对应协议，协议行之后不要追加文字。
"""


def _protocol_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    protocols = [line for line in lines if re.match(r"(?:Bid|Action)\s*:", line)]
    if not lines or "```" in text or "~~~" in text or protocols != [lines[-1]]:
        raise RuleError("FORMAT", "请在最后单独输出唯一的 Bid: 或 Action: 协议行，不加代码围栏。")
    return lines[-1]


def parse_bid(text: str) -> int:
    match = re.fullmatch(r"Bid\s*:\s*([012])", _protocol_line(text))
    if not match:
        raise RuleError("FORMAT", "叫分末行必须为 Bid: 0、Bid: 1 或 Bid: 2。")
    return int(match[1])


def parse_action(text: str) -> Action:
    match = re.fullmatch(r"Action\s*:\s*(play|pass)\s*\(([^()]*)\)", _protocol_line(text))
    if not match:
        raise RuleError("FORMAT", "出牌末行必须为 Action: play(牌面) 或 Action: pass()。")
    kind, body = match.groups()
    cards = body.replace("|", " ").split()
    if kind == "pass" and body.strip():
        raise RuleError("FORMAT", "pass() 不能携带牌。")
    if any(card not in RANK_INDEX for card in cards):
        raise RuleError("FORMAT", "未知牌面；请使用 3 至 2、X、Y，牌之间用空格或 | 分隔。")
    return Action(kind, tuple(sorted(cards, key=RANK_INDEX.__getitem__)))


class Agent:
    """config: api_key、base_url、model 必填；timeout=120、retries=2、backoff=1。

    max_tokens 可选，未提供时不发送该参数。send(messages) 可替代 HTTP 请求，
    返回 Chat Completions 原始响应字典，用于离线验证。不会自动加载 .env 或更换模型。
    """

    def __init__(self, name: str, config: dict, send: Callable | None = None):
        self.name = name
        self.config = {"timeout": 120, "retries": 2, "backoff": 1, **config}
        if not isinstance(self.config["retries"], int) or self.config["retries"] < 0:
            raise ValueError("retries 必须是非负整数。")
        self.send = send if send is not None else self._request
        self.initial_cards: Cards | None = None

    def start_deal(self, cards: Cards) -> None:
        if len(cards) != 17 or Counter(cards) - Counter(DECK):
            raise ValueError("初始手牌必须是牌库中的17张牌。")
        self.initial_cards = tuple(sorted(cards, key=RANK_INDEX.__getitem__))

    def _messages(self, table: dict) -> list[dict]:
        if self.initial_cards is None:
            raise RuntimeError("请先调用 start_deal()。")
        private = {"name": self.name, "seats": PLAYERS, "initial_cards": " ".join(self.initial_cards)}
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(private, ensure_ascii=False)},
            {"role": "user", "content": json.dumps(table, ensure_ascii=False)},
        ]

    def reply(self, table: dict) -> Reply:
        """table 仅含公开数据。空内容与截断仍保留元数据，交给主程序决定如何重试。"""
        raw = self.send(self._messages(table))
        try:
            choice = raw["choices"][0]
            content = choice["message"]["content"]
            if content is not None and not isinstance(content, str):
                raise TypeError("content 必须为文本或 null")
            return {
                "text": content or "",
                "finish_reason": choice.get("finish_reason"),
                "usage": raw.get("usage"),
            }
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError(f"API 响应结构不符合 Chat Completions 协议：{error}") from error

    def _request(self, messages: list[dict]) -> dict:
        config = self.config
        if any(not config.get(key) for key in ("api_key", "base_url", "model")):
            raise ValueError("请配置 api_key、base_url 和实际可用的 model。")
        endpoint = config["base_url"].rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        url = urlsplit(endpoint)
        if url.scheme not in ("http", "https") or not url.hostname or url.query or url.fragment:
            raise ValueError("base_url 必须是无查询参数的 HTTP(S) 地址。")
        payload = {"model": config["model"], "messages": messages, "stream": False}
        if config.get("max_tokens") is not None:
            payload["max_tokens"] = config["max_tokens"]
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
        connection_type = HTTPSConnection if url.scheme == "https" else HTTPConnection

        for attempt in range(config["retries"] + 1):
            connection = connection_type(url.hostname, url.port, timeout=config["timeout"])
            try:
                connection.request("POST", url.path, body, headers)
                response = connection.getresponse()
                text = response.read().decode("utf-8", errors="replace")
                if 200 <= response.status < 300:
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError as error:
                        raise RuntimeError("API 返回的成功响应不是 JSON。") from error
                detail = text.replace(config["api_key"], "***")[:500]
                failure = RuntimeError(f"HTTP {response.status}: {detail}")
                if response.status not in (408, 429) and not 500 <= response.status < 600:
                    raise failure
            except (OSError, HTTPException) as error:
                failure = RuntimeError(f"API 网络请求失败：{error}")
            finally:
                connection.close()
            if attempt == config["retries"]:
                raise failure
            time.sleep(min(config["backoff"] * 2 ** attempt, 30))
