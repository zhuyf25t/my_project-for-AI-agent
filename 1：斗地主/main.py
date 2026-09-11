# 逐行说明（原第 1 行）：声明主程序承担状态和流程组织，三个模块通过这里配合。
"""主程序：发牌、叫地主、状态提交、公开历史、重试和逐回合暂停。"""

# 逐行说明（原第 3 行）：解析命令行参数，提供指定.env和检查配置等入口。
import argparse
# 逐行说明（原第 4 行）：给公开历史制作深拷贝，避免接收方改写真实记录。
import copy
# 逐行说明（原第 5 行）：提供可固定种子的洗牌随机源。
import random
# 逐行说明（原第 6 行）：处理程序退出码和标准输出编码。
import sys
# 逐行说明（原第 7 行）：用计数器维护真实手牌、弃牌和牌库守恒。
from collections import Counter
# 逐行说明（原第 8 行）：用数据类表示状态，并给可变字段提供独立默认容器。
from dataclasses import dataclass, field

# 逐行说明（原第 10 行）：导入接口与配置边界，主程序负责决定API错误是否结束运行。
from bot import (APIError, Agent, ChatClient, ConfigurationError, load_configs,
                 # 逐行说明（原第 11 行）：导入协议解析和公开拒绝记录需要的末行提取函数。
                 parse_action, parse_bid, submitted_line)
# 逐行说明（原第 12 行）：复用显示器和牌面格式，不在主流程里重新实现打印细节。
from display import Display, format_cards
# 逐行说明（原第 13 行）：引入裁判和统一数据定义，使状态与规则共用同一套牌面常量。
from judge import Action, Combination, DECK, PLAYERS, RuleError, judge, sort_cards


# 逐行说明（原第 16 行）：自动生成状态初始化和比较方法，减少大量字段的样板代码。
@dataclass
# 逐行说明（原第 17 行）：集中保存本局权威状态；模型响应不是这些字段的权威来源。
class GameState:
    # 逐行说明（原第 18 行）：区分全员不叫产生的不同发牌批次。
    deal_no: int
    # 逐行说明（原第 19 行）：保存三人的实际剩余手牌，只有主程序扣牌会改变它。
    hands: dict[str, Counter]
    # 逐行说明（原第 20 行）：保存本次发牌的初始17张牌，供Agent长期推导自己的剩余手牌。
    initial: dict[str, tuple[str, ...]]
    # 逐行说明（原第 21 行）：保存三张底牌；分配后仍留作公开记录，但守恒计算不会重复计入。
    bottom: tuple[str, ...]
    # 逐行说明（原第 22 行）：用阶段标识控制哪些动作当前可接受。
    phase: str = "bid"
    # 逐行说明（原第 23 行）：用座位下标表示行动者，避免同时维护另一个可变名字字段。
    current_index: int = 0
    # 逐行说明（原第 24 行）：地主未定时为None，确定后用于身份和胜负计算。
    landlord: str | None = None
    # 逐行说明（原第 25 行）：为每个状态创建独立叫分字典，避免多个牌局共享默认可变对象。
    bids: dict[str, int] = field(default_factory=dict)
    # 逐行说明（原第 26 行）：保存最近需要压制的合法非空组合，pass不直接覆盖它。
    target: Combination | None = None
    # 逐行说明（原第 27 行）：保存目标出牌者，两次pass后此人重新领出。
    target_owner: str | None = None
    # 逐行说明（原第 28 行）：跟踪连续pass次数，第二次后立即归零并重置目标。
    consecutive_passes: int = 0
    # 逐行说明（原第 29 行）：从第一个有效行动机会开始编号，非法重试不增加这个值。
    turn: int = 1
    # 逐行说明（原第 30 行）：累计已打出牌，供54张牌守恒校验使用。
    discarded: Counter = field(default_factory=Counter)
    # 逐行说明（原第 31 行）：保存本次发牌的结构化公开事件，不混入模型可见解释正文。
    events: list[dict] = field(default_factory=list)
    # 逐行说明（原第 32 行）：未结束时无赢家，合法出完手牌才设置。
    winner: str | None = None

    # 逐行说明（原第 34 行）：用类方法把洗牌、切牌和状态初始化放在一个构造入口。
    @classmethod
    # 逐行说明（原第 35 行）：接收随机源和批次号，使测试可以复现牌局。
    def deal(cls, rng: random.Random, deal_no: int) -> "GameState":
        # 逐行说明（原第 36 行）：复制常量牌库为可洗牌列表，避免修改全局DECK。
        deck = list(DECK)
        # 逐行说明（原第 37 行）：原地随机打乱这份新列表。
        rng.shuffle(deck)
        # 逐行说明（原第 38 行）：按座位各取连续17张并排序，保留独立初始手牌。
        initial = {name: sort_cards(deck[i * 17:(i + 1) * 17]) for i, name in enumerate(PLAYERS)}
        # 逐行说明（原第 39 行）：用初始牌计数建立真实手牌容器。
        state = cls(deal_no, {name: Counter(cards) for name, cards in initial.items()},
                    # 逐行说明（原第 40 行）：将末尾三张单独保存为未分配底牌。
                    initial, sort_cards(deck[51:]))
        # 逐行说明（原第 41 行）：只向公开历史记录发牌事实，不写入任何完整私有手牌。
        state.record("deal", status="accepted", message="各玩家17张牌，3张底牌尚未公开，身份待定。")
        # 逐行说明（原第 42 行）：发牌后立即确认没有丢牌或多牌。
        state.assert_invariants()
        # 逐行说明（原第 43 行）：返回可进入叫地主流程的状态对象。
        return state

    # 逐行说明（原第 45 行）：用计算属性提供当前玩家名，避免名字和下标不同步。
    @property
    # 逐行说明（原第 46 行）：定义当前行动者的只读查询入口。
    def current_player(self) -> str:
        # 逐行说明（原第 47 行）：根据固定座位顺序从当前下标得到名字。
        return PLAYERS[self.current_index]

    # 逐行说明（原第 49 行）：身份由地主字段派生，不额外维护可变身份表。
    @property
    # 逐行说明（原第 50 行）：定义全员身份查询入口。
    def roles(self) -> dict[str, str]:
        # 逐行说明（原第 51 行）：地主未定则全员待定，否则匹配地主的为地主、其余为农民。
        return {name: ("待定" if self.landlord is None else "地主" if name == self.landlord else "农民")
                # 逐行说明（原第 52 行）：为固定三位玩家各生成一个身份项。
                for name in PLAYERS}

    # 逐行说明（原第 54 行）：建立统一事件入口，要求公共元数据格式一致。
    def record(self, event_type: str, *, player: str | None = None,
               # 逐行说明（原第 55 行）：接收尝试次数和具体事件字段，避免为每一种事件另写记录类。
               attempt: int | None = None, **details) -> None:
        # 逐行说明（原第 56 行）：将新事件追加到当前发牌的历史末尾。
        self.events.append({
            # 逐行说明（原第 57 行）：用列表长度产生有序编号，附事件种类和发牌批次。
            "event": len(self.events) + 1, "type": event_type, "deal": self.deal_no,
            # 逐行说明（原第 58 行）：记录阶段；叫分没有出牌回合号，出牌和终局则保留当前编号。
            "phase": self.phase, "turn": self.turn if self.phase in ("play", "finished") else None,
            # 逐行说明（原第 59 行）：记录玩家和尝试次数，再加入该事件特有的信息。
            "player": player, "attempt": attempt, **details,
        # 逐行说明（原第 60 行）：完成一次记录追加；事件自身不等于执行出牌。
        })

    # 逐行说明（原第 62 行）：明确生成Agent允许读取的公共视图，而不是暴露整个状态对象。
    def public_table(self, attempt: int = 1) -> dict:
        # 逐行说明（原第 63 行）：原文档强调字段白名单，避免未来新增私有状态时被自动发给模型。
        """显式白名单，不把整个 GameState 序列化给模型。"""
        # 逐行说明（原第 64 行）：新建公共字典，接收方不直接持有GameState引用。
        return {
            # 逐行说明（原第 65 行）：告诉模型当前发牌、阶段和轮到谁行动。
            "deal": self.deal_no, "phase": self.phase, "current_player": self.current_player,
            # 逐行说明（原第 66 行）：给出当前行动号和重试次数，便于理解最近判罚。
            "turn": self.turn if self.phase == "play" else None, "attempt": attempt,
            # 逐行说明（原第 67 行）：公开身份和剩余张数，但不列出别人具体有哪些牌。
            "roles": self.roles, "remaining_counts": {p: sum(self.hands[p].values()) for p in PLAYERS},
            # 逐行说明（原第 68 行）：目标为空表示领出；否则构造可跟牌的公开目标摘要。
            "target": (None if self.target is None else {
                # 逐行说明（原第 69 行）：公开上次有效出牌者及其已打出的牌。
                "player": self.target_owner, "cards": format_cards(self.target.cards),
                # 逐行说明（原第 70 行）：附牌型和主体长度，帮助模型遵守比较规则。
                "kind": self.target.label, "length": self.target.length,
            # 逐行说明（原第 71 行）：结束目标摘要，仍不暴露对手剩余手牌。
            }),
            # 逐行说明（原第 72 行）：明确一次pass之后当前目标仍有效。
            "consecutive_passes": self.consecutive_passes,
            # 逐行说明（原第 73 行）：深拷贝全部公开事件；没有静默截断历史，也不会让调用方修改原记录。
            "history": copy.deepcopy(self.events),
        # 逐行说明（原第 74 行）：完成公共视图构造。
        }

    # 逐行说明（原第 76 行）：为非法尝试建立统一的记录入口，不执行任何牌局动作。
    def reject(self, player: str, response: str, error: RuleError, attempt: int) -> None:
        # 逐行说明（原第 77 行）：把该次尝试标成rejected，防止模型误减牌。
        self.record("rejected", player=player, attempt=attempt, status="rejected",
                    # 逐行说明（原第 78 行）：仅摘出候选协议行；其他说明不会进入对手的公共输入。
                    submitted_line=submitted_line(response),
                    # 逐行说明（原第 79 行）：附具体错误码和说明，强调未扣牌未换人；错误信息因此也是公开的。
                    error={"code": error.code, "message": error.message}, effect="未生效，未扣牌，未换人")

    # 逐行说明（原第 81 行）：验证并提交一个叫分，必要时完成地主选择。
    def apply_bid(self, player: str, bid: int, attempt: int = 1) -> None:
        # 逐行说明（原第 82 行）：在处理输入前检测已有状态是否损坏，程序错误不能冒充模型违规。
        self.assert_invariants()
        # 逐行说明（原第 83 行）：只接受叫分阶段当前玩家的叫分。
        if self.phase != "bid" or player != self.current_player:
            # 逐行说明（原第 84 行）：越阶段或越座位叫分作为可识别错误拒绝。
            raise RuleError("WRONG_TURN", "不是当前叫地主的玩家或阶段。")
        # 逐行说明（原第 85 行）：严格限制为整数0、1、2，也排除Python中可当整数的bool。
        if type(bid) is not int or bid not in (0, 1, 2):
            # 逐行说明（原第 86 行）：返回明确分数范围，让玩家重试。
            raise RuleError("FORMAT", "叫地主分数只能是0、1或2。")
        # 逐行说明（原第 87 行）：所有叫分检查通过后才保存分数。
        self.bids[player] = bid
        # 逐行说明（原第 88 行）：记录有效叫分，后续玩家可以看到它。
        self.record("bid", player=player, attempt=attempt, status="accepted", bid=bid)
        # 逐行说明（原第 89 行）：三人尚未全部叫分时，只需换下一位。
        if len(self.bids) < 3:
            # 逐行说明（原第 90 行）：叫分顺序固定向后，不因已经有人叫2而提前结束。
            self.current_index += 1
            # 逐行说明（原第 91 行）：结束本次叫分提交，等待下一玩家。
            return
        # 逐行说明（原第 92 行）：全部叫完后求最高档位。
        highest = max(self.bids.values())
        # 逐行说明（原第 93 行）：全员为0时，本次发牌不能选出地主。
        if highest == 0:
            # 逐行说明（原第 94 行）：公开记录这副牌作废的原因。
            self.record("redeal", status="accepted", message="全员不叫，本次发牌作废。")
            # 逐行说明（原第 95 行）：通过阶段通知外层重新发牌，而不在状态对象内部直接调用API。
            self.phase = "redeal"
            # 逐行说明（原第 96 行）：提前结束，不分配底牌。
            return
        # 逐行说明（原第 97 行）：按固定叫分顺序取第一个最高分者，落实平分时先叫优先。
        self.landlord = next(name for name in PLAYERS if self.bids[name] == highest)
        # 逐行说明（原第 98 行）：真正把底牌加进地主手牌计数。
        self.hands[self.landlord].update(self.bottom)
        # 逐行说明（原第 99 行）：地主确定后进入出牌阶段。
        self.phase = "play"
        # 逐行说明（原第 100 行）：将地主所在座位设为首个行动者。
        self.current_index = PLAYERS.index(self.landlord)
        # 逐行说明（原第 101 行）：记录地主确定事件，给模型提供身份变更。
        self.record("landlord", status="accepted", player=self.landlord,
                    # 逐行说明（原第 102 行）：将底牌与全员身份加入公开历史，地主据此自行推导20张手牌。
                    bottom_cards=format_cards(self.bottom), roles=self.roles)
        # 逐行说明（原第 103 行）：分配底牌后再次检查守恒，防止重复计数。
        self.assert_invariants()

    # 逐行说明（原第 105 行）：作为合法动作的唯一状态提交入口，返回牌型供显示。
    def apply_action(self, player: str, action: Action, attempt: int = 1) -> Combination | None:
        # 逐行说明（原第 106 行）：先检查已有游戏状态，避免在损坏的数据上继续扣牌。
        self.assert_invariants()
        # 逐行说明（原第 107 行）：给裁判真实手牌和当前目标，未知玩家使用空映射但还会被行动资格检查拒绝。
        combination = judge(player, action, self.hands.get(player, {}), self.target,
                            # 逐行说明（原第 108 行）：明确提供权威行动者与阶段，不能由模型自己选择。
                            current_player=self.current_player, phase=self.phase)
        # 逐行说明（原第 109 行）：原注释标出检查与提交的分界；这不是数据库事务，后续仍是顺序修改内存字段。
        # 所有格式和规则检查均在此之前完成；以下只提交一次确定的合法状态变化。
        # 逐行说明（原第 110 行）：裁判通过后记录accepted动作，非法动作到不了这里。
        self.record("action", player=player, attempt=attempt, status="accepted",
                    # 逐行说明（原第 111 行）：保存规范牌面；pass没有牌，因此记录None。
                    action={"name": action.name, "cards": format_cards(action.cards) if action.cards else None},
                    # 逐行说明（原第 112 行）：附已识别牌型，pass的牌型为None。
                    kind=combination.label if combination else None)
        # 逐行说明（原第 113 行）：非空牌型意味着合法play，进入扣牌分支。
        if combination is not None:
            # 逐行说明（原第 114 行）：按完整合法组合从玩家手牌逐张扣减。
            self.hands[player].subtract(combination.cards)
            # 逐行说明（原第 115 行）：Counter一元加号保留正计数项，清掉已经扣成零的点数。
            self.hands[player] = +self.hands[player]
            # 逐行说明（原第 116 行）：把同一批牌移入已出牌计数，保持牌库总数不变。
            self.discarded.update(combination.cards)
            # 逐行说明（原第 117 行）：更新下一位需要压制的目标。
            self.target = combination
            # 逐行说明（原第 118 行）：将当前玩家记为目标所有者。
            self.target_owner = player
            # 逐行说明（原第 119 行）：新的合法出牌打断此前连续pass记录。
            self.consecutive_passes = 0
            # 逐行说明（原第 120 行）：扣牌后立即检查是否已经出完，不能等下一个玩家行动。
            if not self.hands[player]:
                # 逐行说明（原第 121 行）：将当前玩家确定为先出完者。
                self.winner = player
                # 逐行说明（原第 122 行）：进入终局，禁止继续接受普通动作。
                self.phase = "finished"
                # 逐行说明（原第 123 行）：记录终局事实和实际先出完的玩家。
                self.record("finished", status="accepted", player=player,
                            # 逐行说明（原第 124 行）：根据先出完者身份确定获胜阵营，两名农民共享胜利。
                            winning_team="地主" if player == self.landlord else "农民")
                # 逐行说明（原第 125 行）：返回前验证终局手牌和总牌数一致。
                self.assert_invariants()
                # 逐行说明（原第 126 行）：获胜时立即返回，不再推进座位或回合。
                return combination
        # 逐行说明（原第 127 行）：牌型为None只能是裁判允许的pass。
        else:
            # 逐行说明（原第 128 行）：本次合法不出增加连续次数，非法动作不会进入这里。
            self.consecutive_passes += 1
            # 逐行说明（原第 129 行）：两位后手都不出时才重置牌权。
            if self.consecutive_passes == 2:
                # 逐行说明（原第 130 行）：在清空目标前记录原出牌者，下一轮由此人领出。
                self.record("lead_reset", status="accepted", player=self.target_owner,
                            # 逐行说明（原第 131 行）：在公开历史中说明牌权重置原因。
                            message="连续两人不出，原出牌者重新领出。")
                # 逐行说明（原第 132 行）：清空待压制组合，下一行动必须领出。
                self.target = None
                # 逐行说明（原第 133 行）：同步清空目标所有者，保持两个字段一致。
                self.target_owner = None
                # 逐行说明（原第 134 行）：重置pass计数，避免把旧轮次的不出带到新领出中。
                self.consecutive_passes = 0
        # 逐行说明（原第 135 行）：合法且未结束的动作都顺时针换下一人；两次pass后自然绕回原出牌者。
        self.current_index = (self.current_index + 1) % 3
        # 逐行说明（原第 136 行）：只为有效动作增加回合序号。
        self.turn += 1
        # 逐行说明（原第 137 行）：检查提交后的状态一致性，异常视为程序问题。
        self.assert_invariants()
        # 逐行说明（原第 138 行）：将合法牌型或合法pass结果交给显示层。
        return combination

    # 逐行说明（原第 140 行）：集中检查主要状态约束，用于及早发现程序实现错误；它不是对全部状态的形式化证明。
    def assert_invariants(self) -> None:
        # 逐行说明（原第 141 行）：从已出牌开始累计当前全部54张牌。
        total = Counter(self.discarded)
        # 逐行说明（原第 142 行）：要求手牌字典恰好包含固定三人。
        if set(self.hands) != set(PLAYERS):
            # 逐行说明（原第 143 行）：缺失或额外玩家说明状态容器损坏，直接报程序错误。
            raise RuntimeError("玩家手牌容器损坏。")
        # 逐行说明（原第 144 行）：遍历每人的真实剩余手牌。
        for hand in self.hands.values():
            # 逐行说明（原第 145 行）：检查手牌计数为正整数，零项应已清理，负数不允许。
            if any(type(count) is not int or count <= 0 for count in hand.values()):
                # 逐行说明（原第 146 行）：无效手牌计数不能通过让模型重试来修复。
                raise RuntimeError("手牌计数损坏。")
            # 逐行说明（原第 147 行）：把该玩家剩余牌加入总计数。
            total.update(hand)
        # 逐行说明（原第 148 行）：地主未定时底牌尚未进任何手牌。
        if self.landlord is None:
            # 逐行说明（原第 149 行）：把未分配底牌加进守恒总数；地主确定后这一步不执行。
            total.update(self.bottom)
        # 逐行说明（原第 150 行）：比较每一种牌的张数，而不只比较总数54。
        if total != Counter(DECK):
            # 逐行说明（原第 151 行）：总牌分布不一致时中止，避免错误牌局继续运行。
            raise RuntimeError("牌库守恒失败：手牌、已出牌和未分配底牌不再等于原始54张牌。")
        # 逐行说明（原第 152 行）：要求目标和目标所有者同时存在或同时为空。
        if (self.target is None) != (self.target_owner is None):
            # 逐行说明（原第 153 行）：对一有一无的不一致状态报告程序错误。
            raise RuntimeError("压制目标及其所有者不一致。")
        # 逐行说明（原第 154 行）：合法提交后的连续pass只能为0或1；无目标时也不能保留pass次数。
        if self.consecutive_passes not in (0, 1) or (self.target is None and self.consecutive_passes):
            # 逐行说明（原第 155 行）：指出牌权和连续不出状态损坏。
            raise RuntimeError("连续不出状态损坏。")
        # 逐行说明（原第 156 行）：已设赢家时必须处于终局且赢家手牌确实为空。
        if self.winner is not None and (self.phase != "finished" or self.hands[self.winner]):
            # 逐行说明（原第 157 行）：拒绝提前宣布或未清空手牌的错误胜负状态。
            raise RuntimeError("胜负状态损坏。")


# 逐行说明（原第 160 行）：组织交互流程，把状态对象、Agent和显示器串起来。
class Game:
    # 逐行说明（原第 161 行）：接收三个玩家及可替换显示器，让测试不用真人终端。
    def __init__(self, agents: dict[str, Agent], display: Display | None = None,
                 # 逐行说明（原第 162 行）：接收可替换随机源，方便可复现的完整对局测试。
                 rng: random.Random | None = None):
        # 逐行说明（原第 163 行）：检查映射键恰好是三人，且每个Agent身份与所在键一致。
        if set(agents) != set(PLAYERS) or any(agent.name != name for name, agent in agents.items()):
            # 逐行说明（原第 164 行）：对缺失或错配的Agent明确拒绝初始化。
            raise ValueError("必须提供 Alice、Bob、Corleone 三个对应的独立 Agent。")
        # 逐行说明（原第 165 行）：保存三个玩家对象；主循环每次只请求当前一位。
        self.agents = agents
        # 逐行说明（原第 166 行）：默认创建终端显示器，测试可以传入内存版本。
        self.display = display if display is not None else Display()
        # 逐行说明（原第 167 行）：默认使用新随机源，传入固定种子时则按调用者要求复现发牌。
        self.rng = rng if rng is not None else random.Random()
        # 逐行说明（原第 168 行）：游戏尚未开始时没有活动牌局状态。
        self.state: GameState | None = None
        # 逐行说明（原第 169 行）：单独保存观众可见回复，避免把解释文本混进对手公开输入。
        self.observer_records: list[dict] = []

    # 逐行说明（原第 171 行）：统一当前玩家的请求、观战记录和回复展示步骤。
    def _response(self, state: GameState, attempt: int) -> str:
        # 逐行说明（原第 172 行）：从权威状态确定玩家，忽略模型是否自称另一个身份。
        player = state.current_player
        # 逐行说明（原第 173 行）：只把白名单公共视图传给当前Agent，私有初始牌由Agent自身添加。
        response = self.agents[player].respond(state.public_table(attempt))
        # 逐行说明（原第 174 行）：保存本次回复属于哪副牌、哪个阶段。
        self.observer_records.append({"deal": state.deal_no, "phase": state.phase,
                                      # 逐行说明（原第 175 行）：补充行动号与玩家，便于观众定位响应。
                                      "turn": state.turn, "player": player,
                                      # 逐行说明（原第 176 行）：保存尝试次数和可见回复全文；不包含客户端丢弃的API元数据。
                                      "attempt": attempt, "response": response})
        # 逐行说明（原第 177 行）：打印模型可见回复，是否执行还要经过后续解析与裁判。
        self.display.response(player, response)
        # 逐行说明（原第 178 行）：将回复交给当前阶段的协议解析器。
        return response

    # 逐行说明（原第 180 行）：单独实现一位玩家的叫分纠正循环。
    def _bid_turn(self, state: GameState) -> None:
        # 逐行说明（原第 181 行）：固定本次叫分者，重试不会意外换人。
        player = state.current_player
        # 逐行说明（原第 182 行）：尝试次数从1开始，仅用于记录和显示。
        attempt = 1
        # 逐行说明（原第 183 行）：按README要求，叫分格式错误持续由同一玩家重试。
        while True:
            # 逐行说明（原第 184 行）：显示发牌批次和叫分顺位。
            self.display.say(f"\n第 {state.deal_no} 次发牌 · 叫地主第 {state.current_index + 1} 位 · "
                             # 逐行说明（原第 185 行）：显示玩家、尝试次数以及允许的叫分选项。
                             f"{player} · 尝试 {attempt}（不叫 / 叫 x1 / 叫 x2）")
            # 逐行说明（原第 186 行）：请求当前Agent；APIError不属于下面捕获的RuleError，会向外终止运行。
            response = self._response(state, attempt)
            # 逐行说明（原第 187 行）：只捕获模型可通过修改叫分纠正的问题。
            try:
                # 逐行说明（原第 188 行）：从回复末行提取合法整数叫分。
                bid = parse_bid(response)
                # 逐行说明（原第 189 行）：把分数交给状态对象检查并提交。
                state.apply_bid(player, bid, attempt)
            # 逐行说明（原第 190 行）：仅处理格式或行动资格等规则错误。
            except RuleError as error:
                # 逐行说明（原第 191 行）：记录错误，使下一次请求的完整历史包含此次反馈。
                state.reject(player, response, error, attempt)
                # 逐行说明（原第 192 行）：向观众显示未生效原因。
                self.display.rejected(error)
                # 逐行说明（原第 193 行）：增加尝试次数，但不增加有效回合或换人。
                attempt += 1
            # 逐行说明（原第 194 行）：只有解析与提交均成功才展示有效叫分。
            else:
                # 逐行说明（原第 195 行）：把分数转换为人类描述；“有效”在这里表示叫分合法，不是API配置验证。
                self.display.say(f"{player}：{'不叫' if bid == 0 else f'叫 x{bid}'}，叫分有效。")
                # 逐行说明（原第 196 行）：完成这一位叫分者的工作，返回外层控制下一玩家。
                return

    # 逐行说明（原第 198 行）：实现一个出牌行动机会，包括必要的多次纠正。
    def _play_turn(self, state: GameState) -> None:
        # 逐行说明（原第 199 行）：固定本回合玩家，直到该人给出合法动作。
        player = state.current_player
        # 逐行说明（原第 200 行）：初始化该回合尝试计数。
        attempt = 1
        # 逐行说明（原第 201 行）：按原需求无限纠正规则错误；若错误来自固定输出预算，原样重试可能反复失败。
        while True:
            # 逐行说明（原第 202 行）：展示本回合、玩家、尝试次数及观众全手牌。
            self.display.turn(state.deal_no, state.turn, player, attempt, state.hands,
                              # 逐行说明（原第 203 行）：传入身份和当前目标，使显示器无需读取整个状态对象。
                              state.roles, state.target, state.target_owner)
            # 逐行说明（原第 204 行）：同步请求当前Agent；等待回车期间外层不会执行到这里。
            response = self._response(state, attempt)
            # 逐行说明（原第 205 行）：将动作语法、规则检查和提交放在可纠正异常范围内。
            try:
                # 逐行说明（原第 206 行）：从末行解析Action，说明文字不会被当作Python执行。
                action = parse_action(response)
                # 逐行说明（原第 207 行）：展示候选动作并标待裁判，让观众知道它尚未生效。
                self.display.submitted(player, action)
                # 逐行说明（原第 208 行）：调用唯一状态提交入口，其中会先执行只读裁判检查。
                combination = state.apply_action(player, action, attempt)
            # 逐行说明（原第 209 行）：只把RuleError归为玩家需纠正的问题。
            except RuleError as error:
                # 逐行说明（原第 210 行）：把非法动作与反馈加入历史，真实手牌仍保持未扣状态。
                state.reject(player, response, error, attempt)
                # 逐行说明（原第 211 行）：显示此次拒绝的原因。
                self.display.rejected(error)
                # 逐行说明（原第 212 行）：继续同一回合下一次尝试，不要求用户回车。
                attempt += 1
            # 逐行说明（原第 213 行）：只有没有规则错误时才进入有效动作后的显示与暂停。
            else:
                # 逐行说明（原第 214 行）：显示合法牌型或合法pass，状态此时已提交。
                self.display.accepted(combination)
                # 逐行说明（原第 215 行）：立即识别状态中已经确定的终局。
                if state.winner is not None:
                    # 逐行说明（原第 216 行）：一次性显示赢家、阵营、最后动作和剩余手牌。
                    self.display.finished(state.winner, state.landlord, action, state.hands, state.roles)
                # 逐行说明（原第 217 行）：没有结束时显示行动后的普通牌桌状态。
                else:
                    # 逐行说明（原第 218 行）：标出接下来是扣牌后的状态，区别于回合开始展示。
                    self.display.say("行动后手牌：")
                    # 逐行说明（原第 219 行）：展示更新后的真实手牌供人类检查。
                    self.display.hands(state.hands, state.roles)
                    # 逐行说明（原第 220 行）：一次有效动作后目标为空意味着刚发生两次pass重置。
                    if state.target is None:
                        # 逐行说明（原第 221 行）：明确通知下一回合由谁重新领出。
                        self.display.say(f"连续两人不出，{state.current_player} 下回合重新领出。")
                # 逐行说明（原第 222 行）：每次合法动作后暂停，终局则等待退出；下一玩家API尚未调用。
                self.display.pause(finished=state.winner is not None)
                # 逐行说明（原第 223 行）：等待结束后返回外层，由外层决定是否还有下一回合。
                return

    # 逐行说明（原第 225 行）：定义从发牌到终局的完整生命周期。
    def run(self) -> GameState:
        # 逐行说明（原第 226 行）：从零开始累计发牌次数，第一次循环内增为1。
        deal_no = 0
        # 逐行说明（原第 227 行）：外层循环只用于全员不叫后重新发牌，不是自动连续开新局。
        while True:
            # 逐行说明（原第 228 行）：增加批次号，避免旧牌历史与新牌混淆。
            deal_no += 1
            # 逐行说明（原第 229 行）：创建全新状态，同时保存到实例供中止处理或测试检查。
            self.state = state = GameState.deal(self.rng, deal_no)
            # 逐行说明（原第 230 行）：为三位玩家分别初始化私有信息。
            for name in PLAYERS:
                # 逐行说明（原第 231 行）：每人只收到自己的17张牌，重新发牌覆盖旧私有消息。
                self.agents[name].start_deal(state.initial[name])
            # 逐行说明（原第 232 行）：提醒全手牌展示属于观众视角。
            self.display.say(f"\n第 {deal_no} 次发牌完成；以下完整手牌仅向观众展示。")
            # 逐行说明（原第 233 行）：显示待定身份和17张初始手牌，不把这份文本发给Agent。
            self.display.hands(state.hands, state.roles)
            # 逐行说明（原第 234 行）：在叫分阶段依次处理当前玩家。
            while state.phase == "bid":
                # 逐行说明（原第 235 行）：一次调用完成一位玩家的合法叫分及其重试。
                self._bid_turn(state)
            # 逐行说明（原第 236 行）：状态标记作废时进入重新发牌分支。
            if state.phase == "redeal":
                # 逐行说明（原第 237 行）：告知观众重新发牌；下一次循环会实际替换Agent的私有消息。
                self.display.say("三人均不叫，重新洗牌发牌；Agent 的旧牌局上下文已作废。")
                # 逐行说明（原第 238 行）：跳到外层新发牌，不进入当前这副牌的出牌阶段。
                continue
            # 逐行说明（原第 239 行）：地主确定后向观众公开底牌。
            self.display.say(f"\n地主：{state.landlord}；公开底牌：{format_cards(state.bottom)}")
            # 逐行说明（原第 240 行）：展示新的身份和20/17/17张开局状态。
            self.display.hands(state.hands, state.roles)
            # 逐行说明（原第 241 行）：只要尚未终局就继续处理一个有效行动机会。
            while state.phase == "play":
                # 逐行说明（原第 242 行）：内部会处理错误纠正并等待回车，返回后才可能请求下一玩家。
                self._play_turn(state)
            # 逐行说明（原第 243 行）：返回结束的这局状态，不自动开始另一局。
            return state


# 逐行说明（原第 246 行）：命令行入口负责组装对象和选择退出码。
def main(argv: list[str] | None = None) -> int:
    # 逐行说明（原第 247 行）：建立参数解析器及帮助说明。
    parser = argparse.ArgumentParser(description="三个独立 AI Agent 的终端斗地主观战程序")
    # 逐行说明（原第 248 行）：允许用户明确选择.env，避免多层目录配置造成歧义。
    parser.add_argument("--env-file", help="指定 .env；默认使用 find_dotenv() 从脚本目录向上查找")
    # 逐行说明（原第 249 行）：支持固定洗牌种子；不保证远端模型回复可复现。
    parser.add_argument("--seed", type=int, help="固定洗牌随机种子，方便复现")
    # 逐行说明（原第 250 行）：增加仅本地检查配置的开关；不会向服务验证密钥或模型。
    parser.add_argument("--check-config", action="store_true", help="仅检查配置，不发起 API 请求")
    # 逐行说明（原第 251 行）：读取传入参数或当前进程命令行。
    args = parser.parse_args(argv)
    # 逐行说明（原第 252 行）：创建终端显示器，错误也从同一输出入口显示。
    display = Display()
    # 逐行说明（原第 253 行）：初始化为空，以便启动中途失败时也能安全处理中止。
    game = None
    # 逐行说明（原第 254 行）：捕获配置、接口和用户中止等顶层结束条件。
    try:
        # 逐行说明（原第 255 行）：加载三个玩家的有效本地配置值，不调用聊天API。
        configs = load_configs(args.env_file)
        # 逐行说明（原第 256 行）：用户只要求检查配置时跳过整个游戏运行。
        if args.check_config:
            # 逐行说明（原第 257 行）：依次显示每位玩家最终解析得到的模型名。
            for name in PLAYERS:
                # 逐行说明（原第 258 行）：“配置有效”只代表本地格式通过，此措辞容易误导为服务可用；本次按要求保留原代码。
                display.say(f"{name}：配置有效，模型 {configs[name].model}（密钥不显示）。")
            # 逐行说明（原第 259 行）：本地检查通过就以成功码退出。
            return 0
        # 逐行说明（原第 260 行）：为每人新建客户端与Agent，配置可共用但私有信息不共用。
        agents = {name: Agent(name, ChatClient(configs[name],
                    # 逐行说明（原第 261 行）：用lambda默认参数绑定当前名字，避免延迟调用时三人重试通知都显示最后一个名字。
                    on_retry=lambda message, player=name: display.say(f"{player} API：{message}")))
                  # 逐行说明（原第 262 行）：遍历固定三人，构造按名字索引的Agent字典。
                  for name in PLAYERS}
        # 逐行说明（原第 263 行）：将Agent、显示器和洗牌随机源组合成游戏实例。
        game = Game(agents, display, random.Random(args.seed))
        # 逐行说明（原第 264 行）：启动完整游戏；这里同步等待所有叫分、出牌与回车。
        game.run()
        # 逐行说明（原第 265 行）：正常运行到返回后以成功码退出。
        return 0
    # 逐行说明（原第 266 行）：配置或服务故障单独处理，不计为某位玩家的规则失败。
    except (ConfigurationError, APIError) as error:
        # 逐行说明（原第 267 行）：显示中止原因，并避免把接口故障算成输牌。
        display.say(f"运行中止：{error}；没有因接口故障判定任何玩家输牌。")
        # 逐行说明（原第 268 行）：使用非零退出码让终端或脚本知道运行失败。
        return 1
    # 逐行说明（原第 269 行）：捕获用户Ctrl+C以及输入流关闭导致的EOF。
    except (KeyboardInterrupt, EOFError):
        # 逐行说明（原第 270 行）：如果已经判胜，仅是在最后退出提示处中止，仍算正常结束。
        if game is not None and game.state is not None and game.state.winner is not None:
            # 逐行说明（原第 271 行）：告知牌局已完成，此时不再撤销胜负。
            display.say("\n牌局已结束，退出观战。")
            # 逐行说明（原第 272 行）：终局之后中止等待仍返回成功。
            return 0
        # 逐行说明（原第 273 行）：尚未结束时明确记录未完成，避免错误宣布赢家。
        display.say("\n用户中止或输入流关闭；本局未完成，不判胜负。")
        # 逐行说明（原第 274 行）：使用130表示中断，供外部运行器区分普通失败。
        return 130
    # 逐行说明（原第 275 行）：为未预期异常兜底；此宽泛捕获也降低了调试透明度。
    except Exception as error:
        # 逐行说明（原第 276 行）：原注释说明隐藏异常正文的意图，但这也隐藏了可能有帮助的程序错误详情。
        # 不把可能包含 HTTP 凭据的第三方异常正文写进终端。
        # 逐行说明（原第 277 行）：仅显示异常类型，没有堆栈、位置或具体原因，是当前诊断设计的不足。
        display.say(f"程序错误（{type(error).__name__}）；本局未完成，请检查程序状态。")
        # 逐行说明（原第 278 行）：用失败码结束，不把程序异常放进无限模型重试循环。
        return 1


# 逐行说明（原第 281 行）：只在直接运行此文件时启动，导入测试时不会自动发牌或请求API。
if __name__ == "__main__":
    # 逐行说明（原第 282 行）：同时考虑正常输出与错误输出的编码。
    for stream in (sys.stdout, sys.stderr):
        # 逐行说明（原第 283 行）：只有支持reconfigure的流才调整，避免替换为测试流后失败。
        if hasattr(stream, "reconfigure"):
            # 逐行说明（原第 284 行）：用UTF-8减少Windows管道中中文乱码，不改变游戏逻辑。
            stream.reconfigure(encoding="utf-8")
    # 逐行说明（原第 285 行）：执行命令入口，并把返回值作为进程退出码交给操作系统。
    sys.exit(main())
