"""
持仓管理增强版 - 解决止损过频和无法盈利问题

核心改进：
1. 动态止盈阶梯：根据趋势强度调整
2. 智能止损距离：避免震荡扫损
3. 盈利保护机制：浮盈达标后放宽止损
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from position import Position, ExitRules, hit_stop

logger = logging.getLogger(__name__)


@dataclass
class EnhancedExitRules(ExitRules):
    """增强版出场规则"""
    # 多级止盈
    tp1_pct: float = 1.0      # 第一档：1%
    tp1_ratio: float = 30.0   # 平30%
    tp2_pct: float = 2.0      # 第二档：2%
    tp2_ratio: float = 40.0   # 再平40%（累计70%）
    tp3_pct: float = 3.5      # 第三档：3.5%
    tp3_ratio: float = 30.0   # 剩余全平

    # 止损优化
    sl_buffer_atr: float = 0.5   # 止损线外扩0.5倍ATR（避免正常波动扫损）
    sl_min_pct: float = 1.2      # 最小止损距离1.2%（太近容易被扫）

    # 盈利保护
    protect_profit_at: float = 1.5   # 浮盈达1.5%时启动保护
    protect_trail_pct: float = 0.8   # 保护模式：允许回撤0.8%


def enhanced_initial_stop(sig: dict, rules: EnhancedExitRules, atr: float = None) -> float:
    """增强版初始止损：避免过近

    改进：
    1. ST止损线外扩0.5倍ATR（给震荡留空间）
    2. 最小止损距离1.2%（防止秒扫）
    3. ATR止损与百分比止损取较远者
    """
    entry = float(sig["price"])
    is_long = sig["type"] == "buy"

    # 1. 百分比止损（保底）
    pct_stop = entry * (1 - rules.sl_pct / 100) if is_long else entry * (1 + rules.sl_pct / 100)

    # 2. 最小距离止损
    min_dist = entry * rules.sl_min_pct / 100
    min_stop = (entry - min_dist) if is_long else (entry + min_dist)

    # 3. ST线 + ATR缓冲
    st_stop = pct_stop
    if rules.sl_mode == "st" and sig.get("line") is not None:
        line = float(sig["line"])
        # 检查方向正确
        if (is_long and line < entry) or (not is_long and line > entry):
            # 外扩ATR缓冲
            if atr and atr > 0:
                buffer = atr * rules.sl_buffer_atr
                st_stop = (line - buffer) if is_long else (line + buffer)
            else:
                st_stop = line

    # 4. 取较远的止损（给趋势更多空间）
    candidates = [pct_stop, min_stop, st_stop]
    if is_long:
        # 多头：取最低的止损（最远）
        final_stop = min(candidates)
    else:
        # 空头：取最高的止损（最远）
        final_stop = max(candidates)

    return round(final_stop, 8)


def check_enhanced(pos: Position, price: float, rules: EnhancedExitRules,
                   max_unrealized: float = None) -> dict | None:
    """增强版检查：多级止盈 + 盈利保护

    改进：
    1. 三档止盈：1% / 2% / 3.5%（分批离场）
    2. 盈利保护：浮盈达标后允许适度回撤
    3. 止损优先级仍最高
    """
    if not rules.enabled or pos.qty <= 0:
        return None

    current_pnl = pos.pnl_pct(price)

    # 1. 止损优先（但要考虑盈利保护）
    if hit_stop(pos, price):
        # 如果已有盈利保护，检查是否只是回撤
        if max_unrealized and max_unrealized >= rules.protect_profit_at:
            # 允许从最高点回撤protect_trail_pct
            allowed_pullback = max_unrealized - rules.protect_trail_pct
            if current_pnl >= allowed_pullback:
                # 还在保护范围内，不止损
                logger.info(f"盈利保护生效：当前{current_pnl:.2f}%，最高{max_unrealized:.2f}%，"
                           f"允许回撤至{allowed_pullback:.2f}%")
                return None

        return {
            "action": "stop",
            "ratio": 100.0,
            "reason": (f"触及保本止损 {pos.stop}" if pos.breakeven
                      else f"触及止损 {pos.stop}"),
        }

    # 2. 三档止盈
    if hasattr(pos, "tp2_done"):
        tp2_done = pos.tp2_done
    else:
        pos.tp2_done = False
        tp2_done = False

    if hasattr(pos, "tp3_done"):
        tp3_done = pos.tp3_done
    else:
        pos.tp3_done = False
        tp3_done = False

    # 第三档
    if not tp3_done and current_pnl >= rules.tp3_pct:
        return {
            "action": "tp3",
            "ratio": rules.tp3_ratio,
            "reason": f"浮盈 {current_pnl:.2f}% ≥ {rules.tp3_pct}%，第三档止盈（剩余全平）",
        }

    # 第二档
    if not tp2_done and current_pnl >= rules.tp2_pct:
        return {
            "action": "tp2",
            "ratio": rules.tp2_ratio,
            "reason": f"浮盈 {current_pnl:.2f}% ≥ {rules.tp2_pct}%，第二档止盈",
        }

    # 第一档
    if not pos.tp1_done and current_pnl >= rules.tp1_pct:
        return {
            "action": "tp1",
            "ratio": rules.tp1_ratio,
            "reason": f"浮盈 {current_pnl:.2f}% ≥ {rules.tp1_pct}%，第一档止盈",
        }

    return None


def apply_tp_enhanced(pos: Position, price: float, closed_qty: float,
                     rules: EnhancedExitRules, stage: int = 1):
    """执行止盈并更新状态

    stage: 1/2/3 表示第几档止盈
    """
    d = (price - pos.entry) * pos.coins(closed_qty)
    pos.realized += d if pos.long else -d
    pos.qty = max(0.0, round(pos.qty - closed_qty, 10))

    if stage == 1:
        pos.tp1_done = True
        pos.log("tp1", f"第一档止盈 {closed_qty} @ {price}（{rules.tp1_ratio:.0f}% 仓位）",
                price=price, qty=closed_qty)

        # 第一档止盈后：移到开仓价保本
        if rules.move_sl_to_entry and pos.qty > 0:
            pos.stop = pos.entry
            pos.breakeven = True
            pos.log("breakeven", f"止损移至开仓价 {pos.entry}（保本）")

    elif stage == 2:
        if not hasattr(pos, "tp2_done"):
            pos.tp2_done = False
        pos.tp2_done = True
        pos.log("tp2", f"第二档止盈 {closed_qty} @ {price}（{rules.tp2_ratio:.0f}% 仓位）",
                price=price, qty=closed_qty)

        # 第二档止盈后：止损移到成本价上方（锁定更多利润）
        if pos.qty > 0:
            profit_lock = pos.entry * (1.01 if pos.long else 0.99)  # 锁定1%
            if (pos.long and profit_lock > pos.stop) or (not pos.long and profit_lock < pos.stop):
                pos.stop = profit_lock
                pos.log("profit_lock", f"止损上移至 {profit_lock}（锁定1%利润）")

    elif stage == 3:
        if not hasattr(pos, "tp3_done"):
            pos.tp3_done = False
        pos.tp3_done = True
        pos.log("tp3", f"第三档止盈 {closed_qty} @ {price}（剩余全平）",
                price=price, qty=closed_qty)


def trail_enhanced(pos: Position, st_line: float | None, rules: EnhancedExitRules,
                  current_price: float = None) -> bool:
    """增强版止损跟随：更激进的盈利保护

    改进：
    1. 保本后才开始跟随（之前风险太大）
    2. 只在盈利扩大时跟随（不在盈利回撤时下调）
    3. 跟随幅度：保持与当前价格一定距离
    """
    if not rules.trail_with_st or st_line is None or pos.qty <= 0:
        return False

    # 只有保本后才跟随
    if not pos.breakeven:
        return False

    line = float(st_line)

    # 如果有当前价格，确保止损不要太近
    if current_price:
        pnl = pos.pnl_pct(current_price)
        # 已有较大浮盈时，止损至少保留一半利润
        if pnl >= 2.0:
            min_profit = pnl / 2
            min_stop = pos.entry * (1 + min_profit/100) if pos.long else pos.entry * (1 - min_profit/100)
            if pos.long:
                line = max(line, min_stop)
            else:
                line = min(line, min_stop)

    # 只朝有利方向移动
    better = line > pos.stop if pos.long else line < pos.stop
    if better:
        old = pos.stop
        pos.stop = line
        pos.log("trail", f"止损跟随 {old} → {line}")
        return True

    return False


# 持仓增强：跟踪最高未实现盈利
class EnhancedPosition(Position):
    """增强版持仓：跟踪最高盈利用于保护"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_unrealized_pct = 0.0  # 跟踪最高未实现盈利%
        self.tp2_done = False
        self.tp3_done = False

    def update_max_unrealized(self, price: float):
        """更新最高未实现盈利"""
        current = self.pnl_pct(price)
        if current > self.max_unrealized_pct:
            self.max_unrealized_pct = current

    def to_dict(self, price: float | None = None) -> dict:
        d = super().to_dict(price)
        d["max_unrealized_pct"] = round(self.max_unrealized_pct, 2)
        d["tp2_done"] = getattr(self, "tp2_done", False)
        d["tp3_done"] = getattr(self, "tp3_done", False)
        return d
