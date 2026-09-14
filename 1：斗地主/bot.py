"""两条消息的模型请求与末行协议解析；不保存手牌、对话或历史回复。"""

import json
import re
import time
from collections.abc import Callable
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from urllib.parse import urlsplit

from common import Action, RANK_INDEX, Reply, RuleError

SYSTEM_PROMPT = """你是三人斗地主玩家，座次按 A → B → C → A 循环。由本地裁判确认动作。

【游戏规则】
地主先出完自己胜；任一农民先出完，两农民共胜。
每人初始17张。三人依次各选一次叫分0/1/2；最高非0为地主，同分先叫优先，全0重发。地主再领3张公开底牌并先出。
牌序3<4<5<6<7<8<9<10<J<Q<K<A<2<X<Y；普通牌各4张，X小王、Y大王各1张。
允许单张、对子、三张、三带一/一对、顺子、连对、连续三张、飞机带单/对子、四带二张/两对、炸弹、王炸。
连续主体仅3至A：顺子至少5点、连对至少3点、三张至少2点。飞机主体每点恰好3张，每组三张带一单或一对，
翅膀点数互异且不含主体；三带一/一对也不能带主体。四带二张可带一个对子，四带两对须不同点数；
四张不能拆成两对，四张带一单一对非法。附带牌可用2或单王；两王可作单牌附带，不能作对子或当王炸。
仅X Y是王炸，压所有；仅四张同点数是炸弹，压普通牌型。其余须同牌型、同长度且主体更大，附带牌不比大小。
无目标必须领出；有目标可不出，即使能压制也允许不出。一次不出保留目标，两次连续不出后清空目标，由原出牌者领出。

【如何阅读局面】
以程序提供的“当前局面”为准：本人身份和当前手牌、各家剩余张数、有效目标。其他玩家的手牌不可见。
手牌按牌面分组，如 4 4 | 5 5 | J | 2 2 | Y；| 仅分隔显示，不限制拆牌或组合。每个牌面符号代表一张牌，10是一张十。
“完整出牌记录”逐行列出本局全部已生效动作，按发生顺序排列；“B 打出：3 3”表示 B 出一对3，“C 不出”表示 C pass。
历史牌面用空格分隔；牌面A与玩家A按字段区分。“当前需要压制的牌”是仍然有效的目标，不一定是历史最后一行。
“本次纠正”仅提供最近被拒动作和原因；该动作未生效，不进入历史。

【如何回答】
出牌只能使用自己的当前手牌。可先用一句话说明选择，最后单独一行只输出一个函数调用，之后不再追加文字。
叫分阶段只能输出 bid(0)、bid(1) 或 bid(2) 中的一项。
出牌阶段只能输出 play(牌面) 或 pass()，例如 play(3 3 3 4)。牌面用空格分隔，也接受 | 分隔。
不加 Action: 或 Bid: 前缀，不使用代码围栏。
可见说明仅供展示，旧回复和思考内容不进入后续请求。"""


def _protocol_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    protocols = [line for line in lines if re.match(r"(?:(?:bid|play|pass)\s*\(|(?:Bid|Action)\s*:)", line)]
    if not lines or "```" in text or "~~~" in text or protocols != [lines[-1]]:
        raise RuleError("FORMAT", "末行只能是一个 bid(分数)、play(牌面) 或 pass()，不加前缀或代码围栏。")
    return lines[-1]


def parse_bid(text: str) -> int:
    match = re.fullmatch(r"bid\s*\(\s*([012])\s*\)", _protocol_line(text))
    if not match:
        raise RuleError("FORMAT", "叫分末行必须为 bid(0)、bid(1) 或 bid(2)。")
    return int(match[1])


def parse_action(text: str) -> Action:
    match = re.fullmatch(r"(play|pass)\s*\(([^()]*)\)", _protocol_line(text))
    if not match:
        raise RuleError("FORMAT", "出牌末行必须为 play(牌面) 或 pass()，不加前缀。")
    kind, body = match.groups()
    cards = body.replace("|", " ").split()
    if kind == "pass" and body.strip():
        raise RuleError("FORMAT", "pass() 不能携带牌。")
    if any(card not in RANK_INDEX for card in cards):
        raise RuleError("FORMAT", "未知牌面；请使用 3 至 2、X、Y，牌之间用空格或 | 分隔。")
    return Action(kind, tuple(sorted(cards, key=RANK_INDEX.__getitem__)))


class Agent:
    """config: api_key、base_url、model 必填；timeout=120、retries=2、backoff=1。

    thinking=disabled、reasoning_effort=low；仅开启思考时发送强度。仅支持 stream=False。
    max_tokens 可选，未提供时不发送该参数。send(messages) 可替代 HTTP 请求，
    返回 Chat Completions 原始响应字典，用于离线验证。不会自动加载 .env 或更换模型。
    on_request(endpoint, body, attempt) 在每次 HTTP 请求前接收实际请求体，不接收密钥。
    """

    def __init__(self, name: str, config: dict, send: Callable | None = None, *, on_request=None):
        self.name = name
        self.config = {"timeout": 120, "retries": 2, "backoff": 1,
                       "thinking": "disabled", "reasoning_effort": "low", "stream": False, **config}
        if not isinstance(self.config["retries"], int) or self.config["retries"] < 0:
            raise ValueError("retries 必须是非负整数。")
        if self.config["thinking"] not in ("enabled", "disabled"):
            raise ValueError("thinking 必须是 enabled 或 disabled。")
        if self.config["reasoning_effort"] not in ("low", "high", "max"):
            raise ValueError("reasoning_effort 必须是 low、high 或 max。")
        if self.config["stream"] is not False:
            raise ValueError("当前程序仅支持 stream=false（非流式响应）。")
        self.send = send if send is not None else self._request
        self.on_request = on_request

    def reply(self, user_prompt: str) -> Reply:
        """输入由 main 筛选；空内容与截断保留元数据，交给主程序决定如何重试。"""
        raw = self.send([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ])
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
        payload = {"model": config["model"], "messages": messages, "stream": config["stream"],
                   "thinking": {"type": config["thinking"]}}
        if config["thinking"] == "enabled":
            payload["reasoning_effort"] = config["reasoning_effort"]
        if config.get("max_tokens") is not None:
            payload["max_tokens"] = config["max_tokens"]
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
        connection_type = HTTPSConnection if url.scheme == "https" else HTTPConnection

        for attempt in range(config["retries"] + 1):
            if self.on_request is not None:
                self.on_request(endpoint, body, attempt + 1)
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
