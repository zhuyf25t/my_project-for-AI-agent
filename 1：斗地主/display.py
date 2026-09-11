# 逐行说明（原第 1 行）：用模块文档限定显示层职责，防止观众界面反过来改变游戏规则或状态。
"""终端观众视角；不参与裁判、状态修改或模型请求。"""

# 逐行说明（原第 3 行）：取得默认标准输出，使不传参数时可以直接打印到终端。
import sys
# 逐行说明（原第 4 行）：支持以重复牌列表或计数映射输入，并统一按点数分组。
from collections import Counter
# 逐行说明（原第 5 行）：标明输出流、输入回调和手牌参数类型，方便使用测试替身。
from typing import Callable, Iterable, Mapping, TextIO

# 逐行说明（原第 7 行）：复用裁判模块的牌面顺序、玩家顺序与对象类型，避免显示口径分叉。
from judge import Action, Combination, PLAYERS, RANKS, RuleError


# 逐行说明（原第 10 行）：定义全项目共用的牌面格式化函数，Agent提示词也会使用它。
def format_cards(cards: Iterable[str] | Mapping[str, int]) -> str:
    # 逐行说明（原第 11 行）：统一成计数器，同时保留同点数多张牌。
    counts = Counter(cards)
    # 逐行说明（原第 12 行）：按普通牌顺序构造每个点数的牌组；最后两项王在下一步单独处理。
    groups = [" ".join([rank] * counts[rank]) for rank in RANKS[:-2] if counts[rank]]
    # 逐行说明（原第 13 行）：将存在的小王和大王按X、Y顺序放进同一个末尾分组。
    jokers = " ".join(rank for rank in ("X", "Y") if counts[rank])
    # 逐行说明（原第 14 行）：只有存在王时才添加王组，避免显示多余空分组。
    if jokers:
        # 逐行说明（原第 15 行）：把王组追加到普通牌之后。
        groups.append(jokers)
    # 逐行说明（原第 16 行）：用竖线连接分组，空手牌使用可读占位文字。
    return " | ".join(groups) or "（空）"


# 逐行说明（原第 19 行）：用对象保存输出流和回车函数，避免所有函数反复传递这两个参数。
class Display:
    # 逐行说明（原第 20 行）：允许替换输出流，使测试可以检查打印内容而不污染终端。
    def __init__(self, stream: TextIO | None = None,
                 # 逐行说明（原第 21 行）：允许替换回车函数，使自动测试不需要真人按键。
                 read_input: Callable[[str], str] | None = None):
        # 逐行说明（原第 22 行）：正常运行写标准输出，测试时可写入内存字符串流。
        self.stream = stream if stream is not None else sys.stdout
        # 逐行说明（原第 23 行）：正常运行使用input暂停，测试可注入立即返回或抛EOF的回调。
        self.read_input = read_input if read_input is not None else input

    # 逐行说明（原第 25 行）：将所有终端输出集中在一个入口，统一处理控制字符和刷新。
    def say(self, text: str = "") -> None:
        # 逐行说明（原第 26 行）：原注释说明过滤终端控制字符的目的，防止模型文本改写观众看到的历史。
        # 模型输出不能通过终端控制字符清屏或改写先前的裁判信息。
        # 逐行说明（原第 27 行）：保留可打印字符、换行和制表符，过滤ESC等控制字符；这不是完整ANSI语法解析器。
        safe = "".join(char for char in text if char.isprintable() or char in "\n\t")
        # 逐行说明（原第 28 行）：立即刷新输出，避免用户等待API时还看不到当前回合信息。
        print(safe, file=self.stream, flush=True)

    # 逐行说明（原第 30 行）：接收主程序提供的完整手牌和身份，自己不推导或修改它们。
    def hands(self, hands: Mapping[str, Mapping[str, int]], roles: Mapping[str, str]) -> None:
        # 逐行说明（原第 31 行）：始终按Alice、Bob、Corleone顺序显示，与当前行动顺序分开。
        for name in PLAYERS:
            # 逐行说明（原第 32 行）：打印玩家名称及统一格式的全部手牌。
            self.say(f"{name}: {format_cards(hands[name])}"
                     # 逐行说明（原第 33 行）：补充身份和按计数求和得到的剩余张数，让观众核对扣牌结果。
                     f"  [{roles[name]}，剩余 {sum(hands[name].values())} 张]")

    # 逐行说明（原第 35 行）：接收发牌批次、行动序号和尝试次数，用于定位当前操作。
    def turn(self, deal: int, turn: int, player: str, attempt: int,
             # 逐行说明（原第 36 行）：接收完整手牌及身份，仅供人类观众显示。
             hands: Mapping[str, Mapping[str, int]], roles: Mapping[str, str],
             # 逐行说明（原第 37 行）：接收压制目标及其所有者，用于解释当前为何需要跟牌或领出。
             target: Combination | None, owner: str | None) -> None:
        # 逐行说明（原第 38 行）：把从1开始的行动序号按每三次有效动作换算为展示轮数。
        self.say(f"\n第 {deal} 次发牌 · 第 {(turn - 1) // 3 + 1} 轮 · "
                 # 逐行说明（原第 39 行）：用余数得到轮内位置，并显示非法重试的次数。
                 f"第 {(turn - 1) % 3 + 1} 个玩家 · 尝试 {attempt}")
        # 逐行说明（原第 40 行）：找到当前玩家在固定座位顺序中的位置。
        start = PLAYERS.index(player)
        # 逐行说明（原第 41 行）：从当前玩家循环展示三人行动顺序，模3使末座能绕回首座。
        self.say(" -> ".join(PLAYERS[(start + offset) % 3] for offset in range(3)))
        # 逐行说明（原第 42 行）：另按固定玩家顺序展示完整手牌，满足观战布局要求。
        self.hands(hands, roles)
        # 逐行说明（原第 43 行）：有目标时显示谁出的什么牌以及识别后的牌型。
        self.say(f"待压制：{owner} 的 {format_cards(target.cards)}（{target.label}）"
                 # 逐行说明（原第 44 行）：无目标时明确提示领出，避免误把上一条pass当作可任意跟牌的依据。
                 if target else f"{player} 需要领出。")

    # 逐行说明（原第 46 行）：单独提供模型可见回复的展示入口，和已被裁判接受的动作区别开。
    def response(self, player: str, text: str) -> None:
        # 逐行说明（原第 47 行）：原样展示可见content；空字符串显示“空响应”，此处不能诊断为何为空。
        self.say(f"\n{player} 的可见回复：\n{text or '（空响应）'}")

    # 逐行说明（原第 49 行）：在判定前展示提交动作，让观众知道裁判正在检查什么。
    def submitted(self, player: str, action: Action) -> None:
        # 逐行说明（原第 50 行）：把play和pass转换为人类可读文字；该入口预期只接收已解析的动作。
        text = f"打出 {format_cards(action.cards)}" if action.name == "play" else "不出"
        # 逐行说明（原第 51 行）：标记待裁判，强调打印出来还不代表已经扣牌。
        self.say(f"{player} {text}（待裁判）")

    # 逐行说明（原第 53 行）：为拒绝结果提供统一显示入口。
    def rejected(self, error: RuleError) -> None:
        # 逐行说明（原第 54 行）：明确显示未生效并附规则错误，避免用户误认为非法出牌已执行。
        self.say(f"裁判：无效，未生效。{error}")

    # 逐行说明（原第 56 行）：为通过结果提供统一显示入口。
    def accepted(self, combination: Combination | None) -> None:
        # 逐行说明（原第 57 行）：显示合法牌型；None表示已被裁判允许的不出，不表示异常。
        self.say(f"裁判：合法（{combination.label if combination else '不出'}），已生效。")

    # 逐行说明（原第 59 行）：区分普通回合暂停与终局等待退出。
    def pause(self, finished: bool = False) -> None:
        # 逐行说明（原第 60 行）：通过输入回调阻塞主流程，回车前不会进入下一玩家的API调用。
        self.read_input("按回车结束程序……" if finished else "按回车继续下一回合……")

    # 逐行说明（原第 62 行）：接收终局人物及最后动作，显示层不自行裁定赢家。
    def finished(self, winner: str, landlord: str, action: Action,
                 # 逐行说明（原第 63 行）：接收最终手牌和身份，供观众核对结束状态。
                 hands: Mapping[str, Mapping[str, int]], roles: Mapping[str, str]) -> None:
        # 逐行说明（原第 64 行）：根据先出完者是否地主选择获胜阵营名称。
        team = "地主" if winner == landlord else "农民"
        # 逐行说明（原第 65 行）：地主胜利只列地主；农民胜利列出两位农民，体现共同获胜。
        members = landlord if team == "地主" else "、".join(p for p in PLAYERS if p != landlord)
        # 逐行说明（原第 66 行）：显示阵营、成员和实际先出完的玩家。
        self.say(f"\n本局结束：{team}阵营获胜（{members}）！最先出完：{winner}。")
        # 逐行说明（原第 67 行）：展示触发胜利的最后一次有效出牌。
        self.say(f"最后有效动作：{winner} 打出 {format_cards(action.cards)}")
        # 逐行说明（原第 68 行）：展示所有玩家剩余手牌，结束完整观战记录。
        self.hands(hands, roles)
