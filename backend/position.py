"""
持仓状态机 + 止盈止损规则（合约，支持多空）

规则（网页「挂单」页可调）：
    开仓        buy 信号 → 开多；sell 信号 → 开空
                名义价值 = 保证金 × 杠杆
    分批止盈    浮盈 ≥ tp1_pct（默认 1.5%）→ 平掉 tp1_ratio（默认 70%）仓位
    止损上移    止盈后把止损抬到开仓价（保本），剩余仓位变成「无风险持有」
    剩余离场    剩下的 30% 等反向信号出现时全部平掉
    止损        价格触及止损线 → 全部平掉
                初始止损默认用信号自带的超趋线（SuperTrend 本身就是跟踪止损）

⚠️ 浮盈按【价格变动幅度】算，不是按保证金收益率。
   1.5% 的价格波动，在 10 倍杠杆下对应保证金 15% 的盈亏 —— 杠杆放大的是
   保证金收益率，止盈线仍以价格幅度为准，这样规则不随杠杆漂移。

状态流转（多头为例，空头对称）：
    flat ──buy信号──> open ──浮盈≥1.5%──> partial ──反向信号/触止损──> flat
                        └──────触止损/反向信号─────────────────────┘

价格检查跑在 ticker 推送里（秒级），不等 K 线收盘 —— 止损必须及时。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ExitRules:
    """止盈止损规则，前端可改。"""
    enabled:    bool  = True
    tp1_pct:    float = 1.5    # 第一档止盈触发的【价格】涨跌幅 %
    tp1_ratio:  float = 70.0   # 触发时平掉的仓位比例 %
    # 止盈后把止损移到开仓价（保本）
    move_sl_to_entry: bool = True
    # 初始止损来源：'st' = 用信号的超趋线（跟踪止损）；'pct' = 开仓价固定百分比
    sl_mode:    str   = "st"
    sl_pct:     float = 2.0    # sl_mode='pct' 时用
    # 剩余仓位是否跟随超趋线移动止损（超趋线会随趋势推进）
    trail_with_st: bool = True


@dataclass
class Position:
    """一笔持仓。合约支持多空：side = long / short。

    qty 的单位随品类不同：
        SWAP 合约 → 张数，1 张 = ct_val 个币
        SPOT 现货 → 币数（ct_val = 1）
    盈亏换算必须乘 ct_val，否则 BTC 合约会差 100 倍（ctVal=0.01）。
    """
    symbol:     str
    side:       str                  # long / short
    tf:         str                  # 开仓信号来自哪个周期
    entry:      float                # 开仓价
    qty:        float                # 当前剩余数量（张 / 币）
    init_qty:   float                # 初始数量
    stop:       float                # 当前止损价
    leverage:   int
    entry_ts:   int
    order_id:   str = ""
    ct_val:     float = 1.0          # 每张面值（现货为 1）
    # 开仓时按 ER 落在哪档定下的出场规则，整个持仓周期不变 ——
    # 中途 ER 漂到别档也不换规则，否则止损会莫名其妙跳动
    profile:    str = "normal"       # normal 标准档 / quick 快进快出档
    tp1_done:   bool = False         # 第一档止盈是否已执行
    breakeven:  bool = False         # 止损是否已移到开仓价
    realized:   float = 0.0          # 已实现盈亏（USDT）
    events:     list = field(default_factory=list)

    @property
    def long(self) -> bool:
        return self.side == "long"

    @property
    def state(self) -> str:
        if self.qty <= 0:
            return "closed"
        return "partial" if self.tp1_done else "open"

    def pnl_pct(self, price: float) -> float:
        """价格变动幅度（顺方向为正），与杠杆无关。"""
        if not self.entry:
            return 0.0
        raw = (price - self.entry) / self.entry * 100
        return raw if self.long else -raw

    def roe_pct(self, price: float) -> float:
        """保证金收益率 = 价格幅度 × 杠杆，这才是账户里看到的盈亏比例。"""
        return self.pnl_pct(price) * max(1, self.leverage)

    def coins(self, qty: float | None = None) -> float:
        """张数 → 币数。现货 ct_val=1 时等价。"""
        return (self.qty if qty is None else qty) * (self.ct_val or 1)

    def float_pnl(self, price: float) -> float:
        d = (price - self.entry) * self.coins()
        return d if self.long else -d

    def to_dict(self, price: float | None = None) -> dict:
        d = {
            "symbol": self.symbol, "side": self.side, "tf": self.tf,
            "entry": self.entry, "qty": self.qty, "init_qty": self.init_qty,
            "coin_qty": round(self.coins(), 8), "ct_val": self.ct_val,
            "stop": self.stop, "leverage": self.leverage, "entry_ts": self.entry_ts,
            "order_id": self.order_id, "profile": self.profile, "tp1_done": self.tp1_done,
            "breakeven": self.breakeven, "realized": round(self.realized, 4),
            "state": self.state, "events": self.events[-20:],
        }
        if price:
            d["price"] = price
            d["pnl_pct"] = round(self.pnl_pct(price), 2)
            d["roe_pct"] = round(self.roe_pct(price), 2)
            d["float_pnl"] = round(self.float_pnl(price), 4)
        return d

    def log(self, kind: str, msg: str, **extra):
        self.events.append({"kind": kind, "msg": msg, "ts": int(time.time() * 1000), **extra})


def initial_stop(sig: dict, rules: ExitRules) -> float:
    """开仓时的初始止损。

    默认用信号自带的超趋线 —— SuperTrend 的轨道本来就是跟踪止损位，
    比固定百分比更贴合这套策略（价格穿越它 = 趋势翻转，本来也该走）。
    超趋线方向不对时（多头止损高于开仓价）退回百分比止损，避免开仓即触发。
    """
    entry = float(sig["price"])
    is_long = sig["type"] == "buy"
    pct_stop = entry * (1 - rules.sl_pct / 100) if is_long else entry * (1 + rules.sl_pct / 100)

    if rules.sl_mode == "pct":
        return round(pct_stop, 8)
    line = sig.get("line")
    if line is None:
        return round(pct_stop, 8)
    line = float(line)
    if (is_long and line >= entry) or (not is_long and line <= entry):
        return round(pct_stop, 8)
    return line


def hit_stop(pos: Position, price: float) -> bool:
    return price <= pos.stop if pos.long else price >= pos.stop


def check(pos: Position, price: float, rules: ExitRules) -> dict | None:
    """按当前价判断该不该动作。返回 None 表示不动。"""
    if not rules.enabled or pos.qty <= 0:
        return None

    # 止损优先于止盈：同一 tick 同时满足时，风控优先
    if hit_stop(pos, price):
        return {
            "action": "stop", "ratio": 100.0,
            "reason": (f"触及保本止损 {pos.stop}" if pos.breakeven
                       else f"触及止损 {pos.stop}"),
        }

    if not pos.tp1_done and pos.pnl_pct(price) >= rules.tp1_pct:
        return {
            "action": "tp1", "ratio": rules.tp1_ratio,
            "reason": (f"浮盈 {pos.pnl_pct(price):.2f}%（{pos.leverage}x → 保证金 "
                       f"{pos.roe_pct(price):.1f}%）≥ {rules.tp1_pct}%，"
                       f"止盈 {rules.tp1_ratio:.0f}% 仓位"),
        }
    return None


def apply_tp1(pos: Position, price: float, closed_qty: float, rules: ExitRules):
    """执行完第一档止盈后更新仓位状态。closed_qty 单位与 pos.qty 一致（张/币）。"""
    d = (price - pos.entry) * pos.coins(closed_qty)
    pos.realized += d if pos.long else -d
    # round 掉浮点残渣（0.8999999999999999 这类），否则会带进后续下单量
    pos.qty = max(0.0, round(pos.qty - closed_qty, 10))
    pos.tp1_done = True
    pos.log("tp1", f"止盈 {closed_qty} @ {price}（{rules.tp1_ratio:.0f}% 仓位）",
            price=price, qty=closed_qty)

    if rules.move_sl_to_entry and pos.qty > 0:
        pos.stop = pos.entry
        pos.breakeven = True
        pos.log("breakeven", f"止损移至开仓价 {pos.entry}，剩余仓位无风险持有")


def apply_close(pos: Position, price: float, qty: float, reason: str):
    d = (price - pos.entry) * pos.coins(qty)
    pos.realized += d if pos.long else -d
    pos.qty = max(0.0, round(pos.qty - qty, 10))
    pos.log("close", f"平仓 {qty} @ {price} — {reason}", price=price, qty=qty)


def trail(pos: Position, st_line: float | None, rules: ExitRules) -> bool:
    """随超趋线移动止损。只朝有利方向移，返回是否有变化。"""
    if not rules.trail_with_st or st_line is None or pos.qty <= 0:
        return False
    line = float(st_line)
    better = line > pos.stop if pos.long else line < pos.stop
    if better:
        old = pos.stop
        pos.stop = line
        pos.log("trail", f"止损移动 {old} → {line}（跟随超趋线）")
        return True
    return False
