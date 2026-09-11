# 逐行说明（原第 1 行）：用模块文档声明裁判的边界，提醒调用者这里不会扣牌或推进回合。
"""斗地主牌面、动作和确定性裁判；此模块不修改游戏状态。"""

# 逐行说明（原第 3 行）：引入多重计数器，因为同一牌面可能有四张，普通集合会丢失数量。
from collections import Counter
# 逐行说明（原第 4 行）：用数据类减少动作和牌型对象的初始化样板代码。
from dataclasses import dataclass
# 逐行说明（原第 5 行）：标注可迭代牌序列和只读映射接口，说明函数不要求调用者使用某一种容器。
from typing import Iterable, Mapping

# 逐行说明（原第 7 行）：把三个名字和座位顺序固定为唯一公共常量，避免各模块自行定义顺序。
PLAYERS = ("Alice", "Bob", "Corleone")
# 逐行说明（原第 8 行）：明确斗地主的牌面顺序，使 10、A、2 和王不会被按字符串错误排序。
RANKS = ("3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A", "2", "X", "Y")
# 逐行说明（原第 9 行）：把牌面映射成递增整数，后续排序、连续性检查和比较都复用这个尺度。
RANK_INDEX = {rank: index for index, rank in enumerate(RANKS)}
# 逐行说明（原第 10 行）：构造完整54张牌，普通牌四张、大小王各一张，作为发牌与守恒检查的基准。
DECK = tuple(rank for rank in RANKS for _ in range(1 if rank in ("X", "Y") else 4))

# 逐行说明（原第 12 行）：建立牌型内部标识到中文展示名称的映射，避免判断逻辑依赖显示文字。
KIND_NAMES = {
    # 逐行说明（原第 13 行）：为不带牌的单张、对子和三张提供中文名称。
    "single": "单张", "pair": "对子", "triple": "三张",
    # 逐行说明（原第 14 行）：将三带一与三带一对分成不同类型，防止错误互相压制。
    "triple_single": "三带一", "triple_pair": "三带一对",
    # 逐行说明（原第 15 行）：区分顺子、连对和连续三张，因为它们的每点张数和最短长度不同。
    "straight": "顺子", "pair_straight": "连对", "triple_straight": "连续三张",
    # 逐行说明（原第 16 行）：区分飞机带单和带对，使比较时可以检查带法是否一致。
    "plane_single": "飞机带单牌", "plane_pair": "飞机带对子",
    # 逐行说明（原第 17 行）：区分六张的四带二和八张的四带两对。
    "four_single": "四带二张", "four_pair": "四带两对",
    # 逐行说明（原第 18 行）：为两种具有特殊压制权限的牌型提供独立标识。
    "bomb": "炸弹", "rocket": "王炸",
# 逐行说明（原第 19 行）：结束牌型名称映射；它只负责命名，合法性仍由后面的函数决定。
}


# 逐行说明（原第 22 行）：用专门异常标记模型可以通过重新出牌纠正的错误，与网络和程序故障区分。
class RuleError(ValueError):
    # 逐行说明（原第 23 行）：说明这类错误可以反馈给玩家，而不是直接结束游戏。
    """可反馈给当前玩家的格式或规则错误。"""

    # 逐行说明（原第 25 行）：让每个规则异常同时携带稳定错误码和可读说明。
    def __init__(self, code: str, message: str):
        # 逐行说明（原第 26 行）：保存机器可识别的错误码，便于主程序记录和测试断言。
        self.code = code
        # 逐行说明（原第 27 行）：单独保存说明文字，便于写入结构化公开历史。
        self.message = message
        # 逐行说明（原第 28 行）：初始化标准异常文本，使终端打印异常时同时看见代码和原因。
        super().__init__(f"[{code}] {message}")


# 逐行说明（原第 31 行）：自动生成数据对象方法并禁止改写字段，避免解析后的动作被意外修改。
@dataclass(frozen=True)
# 逐行说明（原第 32 行）：用一个小对象表达玩家提交的意图；这还不代表动作已经合法或执行。
class Action:
    # 逐行说明（原第 33 行）：保存 play 或 pass，供裁判选择检查分支。
    name: str
    # 逐行说明（原第 34 行）：用元组保存所有提交牌；空元组允许表达 pass，也让空 play 留给裁判拒绝。
    cards: tuple[str, ...] = ()


# 逐行说明（原第 37 行）：将已识别牌型设为不可变对象，便于作为后续回合的稳定压制目标。
@dataclass(frozen=True)
# 逐行说明（原第 38 行）：把牌型识别结果集中表示，避免后续比较反复解析牌面。
class Combination:
    # 逐行说明（原第 39 行）：保存内部牌型标识，比较时首先要求普通牌型相同。
    kind: str
    # 逐行说明（原第 40 行）：保存主体最高点数的序号，带牌大小不进入该比较值。
    high: int
    # 逐行说明（原第 41 行）：保存连续主体长度，避免不同长度的顺子或飞机互相压制。
    length: int
    # 逐行说明（原第 42 行）：保留整个出牌组合，供展示、扣牌和总张数比较使用。
    cards: tuple[str, ...]

    # 逐行说明（原第 44 行）：将中文名称做成只读计算属性，不再额外存一份可能不同步的名称。
    @property
    # 逐行说明（原第 45 行）：定义牌型中文名称的访问入口。
    def label(self) -> str:
        # 逐行说明（原第 46 行）：从统一名称表取得展示文字，保持裁判和显示口径一致。
        return KIND_NAMES[self.kind]


# 逐行说明（原第 49 行）：把符号校验与规范排序集中到一个函数供解析和裁判复用。
def sort_cards(cards: Iterable[str]) -> tuple[str, ...]:
    # 逐行说明（原第 50 行）：先固定输入为元组，避免生成器在校验后耗尽，也避免修改调用者的列表。
    cards = tuple(cards)
    # 逐行说明（原第 51 行）：在排序前检查所有符号，防止未知牌面触发难理解的字典查找错误。
    if any(card not in RANK_INDEX for card in cards):
        # 逐行说明（原第 52 行）：将未知牌面明确归为可纠正的格式错误。
        raise RuleError("FORMAT", "牌面符号无效；只能使用 3 至 10、J Q K A 2 X Y。")
    # 逐行说明（原第 53 行）：使用预先定义的斗地主大小排序，同时保留重复牌并返回不可变结果。
    return tuple(sorted(cards, key=RANK_INDEX.__getitem__))


# 逐行说明（原第 56 行）：把模型提交的展示文本转成裁判可处理的牌面元组。
def parse_cards(text: str) -> tuple[str, ...]:
    # 逐行说明（原第 57 行）：说明这里只处理分隔和符号，是否允许空牌要由有回合上下文的裁判判断。
    """空格与竖线等价；空输入交由裁判报告为空出牌。"""
    # 逐行说明（原第 58 行）：将竖线视为空格、拆成牌面标记，再统一校验排序；不会执行模型文本。
    return sort_cards(text.replace("|", " ").split())


# 逐行说明（原第 61 行）：抽出连续性检查，供顺子、连对和飞机主体共用。
def _consecutive(ranks: Iterable[str], minimum: int) -> bool:
    # 逐行说明（原第 62 行）：将点数转换为有序整数，便于检查有没有缺口。
    values = sorted(RANK_INDEX[rank] for rank in ranks)
    # 逐行说明（原第 63 行）：先检查长度与最大点数；短路求值也防止空列表访问末项，且排除2和王。
    return (len(values) >= minimum and values[-1] <= RANK_INDEX["A"]
            # 逐行说明（原第 64 行）：要求整数列表恰好等于连续区间，从而排除跳点和首尾循环。
            and values == list(range(values[0], values[0] + len(values))))


# 逐行说明（原第 67 行）：识别完整出牌组合，返回用于比较的结构化结果。
def classify(cards: Iterable[str]) -> Combination:
    # 逐行说明（原第 68 行）：说明不会只挑选合法子集；主体唯一依赖本项目对翅膀点数的限制。
    """识别整个组合；翅膀限制确保每个合法组合的主体唯一。"""
    # 逐行说明（原第 69 行）：先校验符号并排序，保证下文使用一致的牌面数据。
    cards = sort_cards(cards)
    # 逐行说明（原第 70 行）：统计每个点数的数量，牌型的主要结构由这些次数决定。
    counts = Counter(cards)
    # 逐行说明（原第 71 行）：保存总张数，供单张、王炸和四带二等分支判定。
    size = len(cards)
    # 逐行说明（原第 72 行）：单独处理空组合，避免把空 play 当作合法的不出。
    if not size:
        # 逐行说明（原第 73 行）：告知玩家空出牌无效，并指出正确的不出协议。
        raise RuleError("INVALID_COMBINATION", "出牌不能为空；不出请使用 pass()。")
    # 逐行说明（原第 74 行）：检查单副牌的物理上限，使 classify 单独被调用时也不会接受五张同点或两张小王。
    if any(count > (1 if rank in ("X", "Y") else 4) for rank, count in counts.items()):
        # 逐行说明（原第 75 行）：将超出牌库容量的组合归为牌型错误；真实持牌不足会在 judge 中更早检查。
        raise RuleError("INVALID_COMBINATION", "组合中的牌张数超过一副牌的上限。")

    # 逐行说明（原第 77 行）：抽出结果构造，减少各牌型分支重复计算主体点数的代码。
    def result(kind: str, body: Iterable[str], length: int = 1) -> Combination:
        # 逐行说明（原第 78 行）：用主体最大点数、长度及完整原牌构造识别结果，附带牌不参与主值计算。
        return Combination(kind, max(RANK_INDEX[rank] for rank in body), length, cards)

    # 逐行说明（原第 80 行）：对出现次数排序，例如 [1,3] 表示一组三张和一张附带牌。
    groups = sorted(counts.values())
    # 逐行说明（原第 81 行）：最简单的单张优先识别。
    if size == 1:
        # 逐行说明（原第 82 行）：单张的主体就是该牌本身。
        return result("single", counts)
    # 逐行说明（原第 83 行）：在对子之前单独识别大小王组成的特殊二张牌型。
    if size == 2 and set(counts) == {"X", "Y"}:
        # 逐行说明（原第 84 行）：返回王炸标识，以便比较阶段获得最高压制权限。
        return result("rocket", counts)
    # 逐行说明（原第 85 行）：同一牌面且已排除单张、数量越界时，只可能是对子、三张或炸弹。
    if len(counts) == 1:
        # 逐行说明（原第 86 行）：按张数选择不带牌的类型；这个简写依赖前面的数量和单张检查。
        return result({2: "pair", 3: "triple", 4: "bomb"}[size], counts)
    # 逐行说明（原第 87 行）：找出恰好出现三次的点数，作为三带或飞机的候选主体。
    triples = [rank for rank, count in counts.items() if count == 3]
    # 逐行说明（原第 88 行）：用次数分布判断是否恰好是三张加另一点数一张。
    if groups == [1, 3]:
        # 逐行说明（原第 89 行）：只把三张主体用于比较，忽略附带单牌的大小。
        return result("triple_single", triples)
    # 逐行说明（原第 90 行）：用次数分布判断是否恰好是三张加一个不同点数对子。
    if groups == [2, 3]:
        # 逐行说明（原第 91 行）：返回三带一对，其类型与三带一分开。
        return result("triple_pair", triples)
    # 逐行说明（原第 92 行）：把三类不带牌的连续组合放进同一循环，减少重复判断代码。
    for copies, minimum, kind in (
        # 逐行说明（原第 93 行）：给出每点张数、最少点数数量和牌型标识；这些是项目规则而不是API参数。
        (1, 5, "straight"), (2, 3, "pair_straight"), (3, 2, "triple_straight")
    # 逐行说明（原第 94 行）：结束这组规则参数，开始逐类尝试匹配。
    ):
        # 逐行说明（原第 95 行）：同时要求每点张数一致、点数连续且长度达标，不能只检查其中一个条件。
        if all(count == copies for count in counts.values()) and _consecutive(counts, minimum):
            # 逐行说明（原第 96 行）：保存该连续组合的点数数量，后续比较必须长度相同。
            return result(kind, counts, len(counts))
    # 逐行说明（原第 97 行）：飞机必须至少包含两组连续三张，且连续性函数会排除2和王。
    if _consecutive(triples, 2):
        # 逐行说明（原第 98 行）：把所有非主体点数的计数作为翅膀，确保没有遗漏提交的牌。
        wings = [count for rank, count in counts.items() if rank not in triples]
        # 逐行说明（原第 99 行）：翅膀点数的组数必须等于主体组三张的数量。
        if len(wings) == len(triples):
            # 逐行说明（原第 100 行）：每个翅膀点数出现一次，落实本项目单翅膀互不相同的约定。
            if all(count == 1 for count in wings):
                # 逐行说明（原第 101 行）：返回带单牌的飞机，并记录主体组数。
                return result("plane_single", triples, len(triples))
            # 逐行说明（原第 102 行）：每个翅膀点数出现两次，拒绝将四张同点数拆成两个附带对子。
            if all(count == 2 for count in wings):
                # 逐行说明（原第 103 行）：返回带对子的飞机，使其不能与带单牌飞机混比。
                return result("plane_pair", triples, len(triples))
    # 逐行说明（原第 104 行）：找出四张同点数的候选主体，供四带二识别。
    fours = [rank for rank, count in counts.items() if count == 4]
    # 逐行说明（原第 105 行）：必须只有一个四张主体，排除把另一个四张解释为两个对子。
    if len(fours) == 1:
        # 逐行说明（原第 106 行）：六张四带二允许两个不同单点，也允许两张附带牌恰好同点数。
        if size == 6 and groups in ([1, 1, 4], [2, 4]):
            # 逐行说明（原第 107 行）：返回普通四带二类型，不赋予它炸弹权限。
            return result("four_single", fours)
        # 逐行说明（原第 108 行）：八张组合必须恰好由四张主体和两个不同点数对子构成。
        if size == 8 and groups == [2, 2, 4]:
            # 逐行说明（原第 109 行）：返回四带两对，与六张四带二保持独立类型。
            return result("four_pair", fours)
    # 逐行说明（原第 110 行）：所有允许牌型均不匹配时才抛出统一的非法组合错误。
    raise RuleError(
        # 逐行说明（原第 111 行）：设置稳定错误码，供纠正记录和测试使用。
        "INVALID_COMBINATION",
        # 逐行说明（原第 112 行）：错误说明提醒连续主体范围，帮助模型排查常见原因。
        "不合法牌面组合。请检查主体连续性、长度及附带牌：连续主体限 3 至 A；"
        # 逐行说明（原第 113 行）：补充翅膀和四带二限制；这是概括性提示，尚未细分每个失败条件。
        "飞机单翅膀点数各异，对子翅膀点数各异；四带二限六张或八张。",
    # 逐行说明（原第 114 行）：完成该异常构造并抛出，识别函数不会默默接受未知组合。
    )


# 逐行说明（原第 117 行）：对两个已经识别的组合比较；持牌与行动资格由 judge 负责。
def compare(current: Combination, previous: Combination) -> None:
    # 逐行说明（原第 118 行）：用文档说明通过时无返回数据，失败通过异常携带原因。
    """不能压制时抛出具体错误；通过时返回 None。"""
    # 逐行说明（原第 119 行）：先处理目标王炸，避免后面的炸弹特例错误越过它。
    if previous.kind == "rocket":
        # 逐行说明（原第 120 行）：目标王炸不可压制，包括同样大小也不能压制。
        raise RuleError("NOT_GREATER", "王炸不能被任何牌型压制。")
    # 逐行说明（原第 121 行）：目标不是王炸时，当前王炸一定能压制。
    if current.kind == "rocket":
        # 逐行说明（原第 122 行）：立即通过，跳过普通牌型和长度检查。
        return
    # 逐行说明（原第 123 行）：当前炸弹只在目标不是炸弹时直接通过；目标王炸已经更早拒绝。
    if current.kind == "bomb" and previous.kind != "bomb":
        # 逐行说明（原第 124 行）：允许炸弹跨普通牌型压制，而不要求张数一致。
        return
    # 逐行说明（原第 125 行）：普通比较首先要求类型和总张数相同。
    if (current.kind != previous.kind or len(current.cards) != len(previous.cards)
            # 逐行说明（原第 126 行）：再要求连续主体长度相同，避免带法或长度不同被误当作可比。
            or current.length != previous.length):
        # 逐行说明（原第 127 行）：返回可识别的类型错误，并指出当前目标牌型。
        raise RuleError("TYPE_MISMATCH", f"牌型或长度不匹配：当前需要压制 {previous.label}，"
                        # 逐行说明（原第 128 行）：补充目标张数及炸弹特例，帮助模型选择新的动作。
                        f"共 {len(previous.cards)} 张；炸弹和王炸可按特殊规则压制。")
    # 逐行说明（原第 129 行）：相同或较小主体都不允许，落实严格大于规则。
    if current.high <= previous.high:
        # 逐行说明（原第 130 行）：用独立错误码区分大小不足和牌型不匹配。
        raise RuleError("NOT_GREATER", "牌面组合不够大：主体必须严格大于待压制组合。")


# 逐行说明（原第 133 行）：定义裁判入口，接收提交者、动作以及该玩家的真实手牌计数。
def judge(player: str, action: Action, hand: Mapping[str, int],
          # 逐行说明（原第 134 行）：接收当前压制目标，并要求显式提供程序认定的行动者。
          previous: Combination | None, *, current_player: str,
          # 逐行说明（原第 135 行）：接收阶段并声明返回牌型或None；这里None仅代表合法pass。
          phase: str = "play") -> Combination | None:
    # 逐行说明（原第 136 行）：强调状态须来自主程序，不能相信模型自己声称的手牌或行动资格。
    """只读检查当前动作；真实手牌和行动者必须由主程序提供。"""
    # 逐行说明（原第 137 行）：先检查动作名以及pass参数，防止其他动作混入规则流程。
    if action.name not in ("play", "pass") or (action.name == "pass" and action.cards):
        # 逐行说明（原第 138 行）：为动作协议错误提供可供模型重试的说明。
        raise RuleError("FORMAT", "仅允许 play(牌面组合) 或不带参数的 pass()。")
    # 逐行说明（原第 139 行）：对提交牌进行符号校验和规范排序。
    cards = sort_cards(action.cards)
    # 逐行说明（原第 140 行）：对照权威阶段和行动者，禁止越阶段或越座位出牌。
    if phase != "play" or player != current_player:
        # 逐行说明（原第 141 行）：拒绝错误玩家或阶段，不改变任何手牌。
        raise RuleError("WRONG_TURN", "当前阶段或行动者错误，不能提交该动作。")
    # 逐行说明（原第 142 行）：单独处理pass，因为它没有可识别的非空牌型。
    if action.name == "pass":
        # 逐行说明（原第 143 行）：没有目标即拥有领出权，本轮不能不出。
        if previous is None:
            # 逐行说明（原第 144 行）：明确反馈领出必须出牌，避免牌局停滞。
            raise RuleError("PASS_FORBIDDEN", "当前需要领出，不能不出牌。")
        # 逐行说明（原第 145 行）：存在目标时允许主动不出，不要求先搜索玩家是否有可压制牌。
        return None
    # 逐行说明（原第 146 行）：逐点统计玩家本次想出的数量，不能只检查某点是否存在。
    for rank, amount in Counter(cards).items():
        # 逐行说明（原第 147 行）：与真实手牌比较，缺失点数按零张处理。
        if amount > hand.get(rank, 0):
            # 逐行说明（原第 148 行）：指出提交了多少张哪种牌，帮助修正持牌数量错误。
            raise RuleError("INSUFFICIENT_CARDS", f"手牌不足：提交了 {amount} 张 {rank}，"
                            # 逐行说明（原第 149 行）：补充实际数量并强调未扣牌；该判罚会进入公开历史。
                            f"实际只有 {hand.get(rank, 0)} 张；本次未扣牌。")
    # 逐行说明（原第 150 行）：持牌数量通过后再识别完整牌型，确保错误反馈顺序一致。
    combination = classify(cards)
    # 逐行说明（原第 151 行）：只有存在待压制组合时才需要比较大小。
    if previous is not None:
        # 逐行说明（原第 152 行）：复用统一比较规则，保持领出和跟牌的牌型定义一致。
        compare(combination, previous)
    # 逐行说明（原第 153 行）：返回已验证牌型，由主程序决定何时扣牌和推进状态。
    return combination
