"""终端只展示公开事件与程序计算的分析，不参与裁判。"""

from game import analysis, remaining
from state import GameState

NAMES = {"player": "选手 AI", "banker": "银行家 AI"}


def safe_line(text: str) -> str:
    return "".join(char if char.isprintable() else " " for char in text)


def show_analysis(stats: dict) -> None:
    print(f"  剩余 {stats['count']} 箱 | 最低 {stats['min']:,} | 最高 {stats['max']:,}"
          f" | 中位数 {stats['median']:,.2f} | EV {stats['ev']:,.2f}")
    print("  剩余奖金：" + ", ".join(f"{amount:,}" for amount in stats["remaining_amounts"]))
    if "offer" in stats:
        print(f"  报价 {stats['offer']:,} = EV 的 {stats['offer_to_ev']:.2%}"
              f" | 报价 − EV = {stats['offer_minus_ev']:+,.2f}")
        counts, n = stats["hold_box_counts"], stats["count"]
        labels = {"below": "低于", "equal": "等于", "above": "高于"}
        print("  持箱到底时，箱值相对该报价：" + "，".join(
            f"{labels[key]} {value}/{n} ({value/n:.2%})" for key, value in counts.items()))


def show_event(state: GameState) -> None:
    event = state["game"]["events"][-1]
    action, details = event["action"], event["details"]
    descriptions = {
        "choose_box": f"保留 {details.get('box_id')} 号箱",
        "open_box": f"打开 {details.get('box_id')} 号箱，排除奖金 {details.get('amount', 0):,}",
        "offer": f"{'提前来电' if details.get('kind') == 'early' else '轮末报价'}：{details.get('amount', 0):,}",
        "wait": "暂不来电，继续开箱", "deal": "DEAL！接受报价",
        "no_deal": "NO DEAL！拒绝报价", "counter": f"还价 {details.get('amount', 0):,}",
        "accept_counter": "接受还价，立即成交", "keep_offer": "拒绝还价，保留原价",
    }
    print(f"\n[{event['number']:02d} / 第 {event['round_no']} 轮] {NAMES[event['actor']]}：{descriptions[action]}")
    print(f"  公开理由：{safe_line(event['reason'])}")
    if action == "open_box":
        game = state["game"]
        show_analysis(analysis(remaining(game)))
        print("  未开箱：" + " ".join(f"[{box}{'*' if box == game['own_box'] else ''}]"
                                    for box in game["boxes"] if box not in game["opened"]))
    if action == "offer":
        print(f"  档位 {details['level']} | 系数 {details['percent']}%"
              f" | 向下取整 {details['raw_amount']:,} | 最低金额保护：{'触发' if details['floor_applied'] else '未触发'}"
              f" | 提前来电已用 {state['game']['early_calls']}/2")
    if "analysis" in details:
        show_analysis(details["analysis"])


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
