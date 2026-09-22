# Deal or No Deal：围观两个 AI 博弈

选手 AI 保留一个箱子，逐个打开其他箱子；银行家 AI 选择来电时机、报价档位，并回应还价。你运行程序后只需看它们博弈，终端会展示公开理由、报价与期望值的比较，以及最后的结算。

当前实现是 **Python 终端 + 两个 AI + 三个 LangGraph 节点**。使用 `TypedDict`、`StateGraph`、`add_messages`、`AIMessage.tool_calls`、`ToolMessage` 和条件边；一次 `invoke()` 自动运行一局。两个角色可以共用一个模型，也可以分别配置模型。

这是根据后续讨论收敛后的第一版：状态仅在内存中，不包含人类输入、`interrupt`、检查点恢复、SQLite、子图或并行节点。旧的人类参赛 / 九节点设计可从 Git 历史查看。

- [简化版架构 PDF：横向 A4](langgraph-design-simple.pdf)：学习时主要参考这一份。
- [早期完整架构 PDF](langgraph-design.pdf)：保留作设计记录，与当前三节点实现的范围不同。

## 1. 先运行起来

需要 Python 3.12 或更高版本。在本项目目录中安装依赖：

```sh
python -m venv .venv
# Windows PowerShell：.venv\Scripts\Activate.ps1
# macOS / Linux：source .venv/bin/activate
python -m pip install -r requirements.txt
```

如果已有的 `.venv` 来自 WSL，请在 WSL 中使用；Windows 可以另建 `.venv-windows`。两者都被 Git 忽略。

按下一节配置真实模型后运行：

```sh
python main.py
python main.py --record
```

两个角色均使用 `ChatAgent` 调用真实模型。每次启动都是新的一局，不需要终端菜单操作。`--record` 会把公开事件与结算写入本地 `records/`，该目录不提交 Git；这份记录不具备继续游戏功能。默认只打印，不写记录。

其他选项：

| 参数 | 含义 |
| --- | --- |
| `--env-file 路径` | 指定配置文件，默认读取本项目旁的 `.env` |
| `--seed 整数` | 只固定本地洗牌结果，不固定真实模型的决策，不发送给模型 |
| `--max-model-calls 80` | 限制本局 Agent 决策请求数；默认 200，范围 1～200 |
| `--quiet` | 省略逐步输出，只打印最终结果 |

## 2. 模型配置

在本项目目录的 `.env` 中填写下面的配置。系统环境变量优先于文件；角色配置优先于共用配置。

```dotenv
OPENAI_API_KEY=你的密钥
OPENAI_BASE_URL=https://你的服务地址/v1
OPENAI_MODEL=实际支持工具调用的模型名

# 按需覆盖；未填写时复用上面的共用配置。
# PLAYER_API_KEY=...
# PLAYER_BASE_URL=...
# PLAYER_MODEL=...
# BANKER_API_KEY=...
# BANKER_BASE_URL=...
# BANKER_MODEL=...

API_TIMEOUT=120
API_RETRIES=2
stream=false

# 仅当供应商明确支持这些扩展参数时填写，否则保持未设置。
# thinking=disabled
# reasoning_effort=low
# max_tokens=2048
```

接口采用兼容 Chat Completions 的 HTTP 请求，必须支持 `tools`、`tool_choice="required"`、`tool_calls` 和 `tool` 消息。地址会自动补 `/chat/completions`，已包含该后缀时直接使用。只支持非流式响应。

不支持工具调用的服务会报错，程序不会退回自然语言猜动作。`thinking` 只在明确设置时发送；`reasoning_effort` 仅在 `thinking=enabled` 时发送。这些是供应商扩展，不保证所有兼容接口都接受。

`100084` / `100084.*`、`.env`、`*.env`、`env`、虚拟环境、`output/` 和运行记录都已被忽略。本程序不读取 `100084.txt`。不要将真实密钥写入 Python 文件。

## 3. 文件与阅读顺序

| 文件 | 职责 | 建议关注 |
| --- | --- | --- |
| `state.py` | 类型、规则常量、初始化并洗牌 | `GameData` 和 `GameState` 的区别 |
| `game.py` | 公开视图、工具定义、统计、校验和执行 | `public_view()`、`apply_action()` |
| `bot.py` | 两个角色的提示词、真实模型 API 适配器 | 模型只接收公开视图与自己的消息历史 |
| `main.py` | 三节点、路由、CLI、可选本地记录 | `build_graph()`、`execute_node()` |
| `display.py` | 逐步事件、报价分析和结算 | 展示不维护另一份游戏状态 |
| `test_game.py` | 规则、实际图运行、消息协议与模拟 HTTP 测试 | 直接运行，无额外测试框架依赖 |

阅读时建议按 `state.py → game.py → main.py → bot.py`；最后看展示和测试。

## 4. State 怎么设计

`GameState` 分成“游戏事实”和“图的运行信息”。

| 字段 | 类型 / 含义 | 更新方式 |
| --- | --- | --- |
| `game` | `GameData`，包含箱子、阶段、报价和有效事件 | 裁判在副本上执行成功后整体替换 |
| `actor` | `player` / `banker`，下一位行动者 | 合法动作后按阶段更新 |
| `pending` | `AIMessage` 或 `None`，尚未执行的候选响应 | 模型节点写入，执行节点清空 |
| `player_messages` | 选手自己的消息历史 | `Annotated[..., add_messages]` |
| `banker_messages` | 银行家自己的消息历史 | `Annotated[..., add_messages]` |
| `retry_count` | 当前决策连续无效响应数 | 失败递增，成功清零 |
| `model_calls` | 已发起的 Agent 决策调用数 | 每次调用递增，不含内部 HTTP 重试 |
| `status` | `running` / `completed` / `error` | 区分正常结算与中止 |
| `result` | 结算方式、实得金额、自有箱实际金额 | 只在合法终局写入 |
| `error` | 可展示的中止原因或 `None` | 出错时写入 |

只有两份消息历史使用 reducer。节点返回**新增消息**，不把整段旧历史再返回一次；其他字段默认替换。

`GameData` 的核心字段：

```python
boxes: dict[int, int]       # 私有：箱号 → 奖金，开局洗牌后固定
own_box: int | None         # 选手保留箱的编号
opened: list[int]           # 按开箱顺序记录编号
round_no: int              # 1～6
opened_in_round: int        # 本轮已开数量
early_calls: int            # 整局已生效的提前报价次数：0～2
early_in_round: bool        # 当前轮是否已经提前报价
phase: Phase               # 当前游戏阶段
offer: Offer | None        # 当前有效报价：金额、种类、档位、系数、还价使用情况
counter_amount: int | None  # 等待银行家答复的还价
events: list[Event]         # 按序记录合法且已生效的公开动作
```

剩余金额、期望值、可开箱号、合法动作等随时由事实计算，避免多处保存后相互不一致。报价事件保存当时的分析，拒绝并清空当前报价后，历史仍可供查看。

### `early_calls` 与 `early_in_round`

它们分别限制“整局”和“本轮”：

```text
开局：                       early_calls = 0，early_in_round = False
第一轮第一次提前报价生效：   early_calls = 1，early_in_round = True
拒绝提前报价，继续第一轮：   early_calls = 1，early_in_round = True
拒绝第一轮末报价，进入第二轮：early_calls = 1，early_in_round = False
第二轮提前报价生效：         early_calls = 2，early_in_round = True
进入第三轮：                 early_calls = 2，early_in_round = False
```

第三轮虽然本轮没有来电过，但整局额度已经用完，所以仍不能提前来电。`wait`、无效输出、API 重试以及正常轮末报价都不增加 `early_calls`。

## 5. 三个节点与边

```text
START → player → execute
                 ├─ 下一位是选手   → player → execute → …
                 ├─ 下一位是银行家 → banker → execute → …
                 └─ 已结算或出错   → END
```

图中实际节点名是 `player`、`banker`、`execute`：

1. **player / banker**：拼接该角色的系统提示词、自己的历史、最新公开局面；只提供当前阶段合法的工具定义；调用 Agent；把响应写入 `pending`。
2. **execute**：校验候选响应、身份、阶段、参数和额度。合法才执行；成功后更新游戏事实、返回工具反馈并显示事件；失败则反馈原因，让同一角色限次重试。
3. **route_after_execute**：普通路由函数，不是节点。`status != running` 返回 `END`，否则按 `actor` 选择下一位。

两个模型节点都有一条固定边通向 `execute`，只有 `execute` 后面是条件边。AI 无权直接指定图跳到哪个节点。

### 消息闭环

一次合法结构的调用形成下面的历史：

```text
HumanMessage（当前公开局面）
AIMessage（tool_calls 中恰好一个工具调用）
ToolMessage（同一个 tool_call_id，包含执行成功或规则错误）
```

如果调用缺失、多工具并发、参数 JSON 损坏、id 缺失或重复、响应截断，则不把损坏的 AI 消息加入历史，而是追加一条纠正用的 `HumanMessage`。避免下一次请求携带无法配对的工具消息。

规则校验失败但调用结构完整时，仍写入配对的 `AIMessage + ToolMessage`，其中 `ok=false`，游戏事实保持不变。

## 6. 阶段、动作与游戏规则

| `phase` | 行动者 | 当前合法动作 |
| --- | --- | --- |
| `choose` | 选手 | `choose_box(box_id, reason)` |
| `open` | 选手 | `open_box(box_id, reason)` |
| `bank_early` | 银行家 | `offer(level, reason)` / `wait(reason)` |
| `bank_normal` | 银行家 | `offer(level, reason)` |
| `offer` | 选手 | `deal(reason)` / `no_deal(reason)` / 有资格时 `counter(amount, reason)` |
| `counter` | 银行家 | `accept_counter(reason)` / `keep_offer(reason)` |
| `done` | 无 | 没有动作 |

每次只调用一个工具。`reason` 是 1～400 字符的简短公开理由，不是模型内部思维过程。程序不依靠理由改变金额或规则。

- 20 个箱子，编号 1～20。奖金为：`1, 5, 10, 25, 50, 100, 200, 500, 1000, 2500, 5000, 7500, 10000, 20000, 30000, 50000, 75000, 100000, 250000, 1000000`。
- 选手选箱前随机分配奖金，之后固定。不能打开自己的箱子、重复开箱、换箱或一次开多个箱子。整数参数不接受字符串、小数或布尔值。
- 六轮开箱数为 `5、4、3、3、2、1`；累计开 18 个，最后剩自己的箱子与另一个箱子。
- 每次开箱后先检查本轮配额：已开完就必须正常报价；否则检查是否有提前来电资格；无资格则选手继续开箱。
- 提前来电全局最多两次、每轮最多一次。`wait` 放弃当前间隙，下一次符合条件的开箱后仍可判断；只有合法的提前报价生效才消耗额度。
- “打断”发生在一次开箱执行完毕之后、下一次开箱决策之前；不涉及线程抢占、取消 API 请求或撤回已开的箱子。
- 拒绝提前报价继续本轮；拒绝正常报价进入下一轮；拒绝第六轮的正常报价直接领取自己箱内奖金。没有最后换箱环节。
- 每个报价最多合法还价一次，金额必须严格大于原报价，且不超过剩余最高奖金。银行家接受即成交；拒绝就保留原价，选手只能接受或拒绝。
- 接受初始报价、接受还价、最终领取箱内奖金均立即结算。没有真实支付、额外赌注、资产账户或绝对胜负标签。

## 7. 定价与分析

银行家选择 `low / base / high`，由程序计算初始报价，不能随意写一个金额。

| 轮次 | low | base | high |
| --- | --- | --- | --- |
| 1 | 40% | 45% | 50% |
| 2 | 50% | 55% | 60% |
| 3 | 60% | 65% | 70% |
| 4 | 70% | 75% | 80% |
| 5 | 80% | 85% | 90% |
| 6 | 90% | 95% | 100% |

```python
EV = sum(remaining_amounts) / len(remaining_amounts)
raw_amount = sum(remaining_amounts) * percent // (len(remaining_amounts) * 100)
offer = max(min(remaining_amounts), raw_amount)
```

未开箱集合始终包含选手自己的箱子。取整使用整数计算，最低金额保护触发时明确展示；提前报价使用当前轮系数。报价不保证逐次提高。这些系数是教学规则，不是节目官方或最优定价公式。

终端显示剩余数、最低 / 最高 / 中位数 / EV；报价时补充档位、系数、取整、最低金额保护、`报价 / EV`、`报价 - EV`。还价可以高于 EV，接受后如实展示溢价。

三项概率是：**从当前局面拒绝所有后续报价并持箱到底，最终箱值低于 / 等于 / 高于该报价的概率**。它们不包含未来报价，也不等于“继续游戏会亏钱的概率”。`报价 < EV` 不能独自证明拒绝是最优决定。

终局揭晓所有箱值，展示实得金额与自己箱值的事后对照；一次结果不能证明当时策略一定正确或错误。

## 8. 信息边界与错误处理

完整 `boxes` 仅由程序保存。两位 AI 通过 `public_view()` 看相同公开信息：已开箱对应关系、未开箱编号、排序后的剩余奖金、当前报价、统计与有效历史。随机种子与未开箱的金额对应关系不进入模型请求；两个角色的完整消息历史也不互通。

模型返回的工具名只是受校验的动作候选，不会被当作 Python 代码运行。阶段限制、额度、箱号与金额都由本地裁判复查，不能只信任工具 JSON Schema。

每次决策最多尝试 3 次（首次 + 两次纠正），失败不改变游戏。HTTP 408 / 429 / 5xx、网络错误可在适配器内重试，默认最多两次，退避 1 秒、2 秒；其他 HTTP 错误直接中止。每次请求默认超时 120 秒。

全局默认最多 200 次 Agent 调用；图的 `recursion_limit=500` 是最后一道步数保护。状态为 `error` 时不结算、不判输赢，也不揭晓未完成局的隐藏箱值。API 错误不打印原始响应或密钥。

可选记录保存公开有效事件、终局结果、调用数和中止原因。只有正常完成才写入全箱揭晓；不保存原始 API 请求或两份消息历史。不支持断点恢复与按记录重新运行模型。

## 9. 验证与 review

```sh
python -m unittest -v
```

测试包含规则边界、100 局随机合法动作的不变量检查、完整六轮图执行、拒绝最后报价后的结算、非法响应纠正、消息配对、两份历史隔离、隐藏信息投影，以及模拟 HTTP 重试与配置覆盖。固定响应桩仅定义在 `test_game.py` 中，不参与实际对局。测试不读取真实 `.env`，也不调用付费模型；真实供应商连通性需要使用自己的兼容接口运行验证。

可优先 review：`apply_action()` 是否严格执行规则，`public_view()` 是否只暴露公开信息，`execute_node()` 是否正确写入配对反馈，以及条件边是否总能结束。

图与 reducer 的用法参考 [LangGraph 官方 Graph API 文档](https://docs.langchain.com/oss/python/langgraph/graph-api)。
