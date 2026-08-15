"""
SuperTrend 策略回测

对应 Pine 的：
    if buySignal  and window() → strategy.entry('BUY',  strategy.long)
    if sellSignal and window() → strategy.entry('SELL', strategy.short)
strategy.entry 反向进场会自动平掉原仓，所以这里就是「永远满仓、多空反手」。

在原脚本基础上补的开关（Pine 版没有，实盘要用）：
  allow_short=False   只做多，翻空时平仓观望
  bias_filter         用 MTF Bias（fast/slow MA）过滤：只接受与偏向一致的开仓，
                      逆向信号只平仓不反手 —— 把 Bias 表从「看」变成「用」
  er_min              震荡闸门，同 regime.evaluate：ER 低于阈值的信号只提醒不开仓
  exit_rules          止盈止损，直接复用 position.py 那套实盘状态机
  sizing              equity=满仓复利1x（原行为）/ fixed=固定保证金×杠杆（对齐实盘）

后三个默认关闭 —— 不传就是原来的「翻转即反手、永远满仓」行为。

⚠️ 闸门只拦【开仓】，不拦【平反向仓】—— 严格还原 executor.on_signal：
   反向信号先无条件平掉手上的仓，再判闸门决定要不要开新仓。不这样还原，
   被拦的信号会让仓位一直挂着，结果差很多。

⚠️ 盘中止盈止损只有 OHLC、没有 tick，用极值近似：止损按最低/最高价判定但
   【按止损价成交】，同一根同时触及止损和止盈时算止损（保守，也和
   position.check 自身的优先级一致）。实盘是秒级 tick 检查，会有偏差。

成交价用翻转当根收盘（信号收盘确认），手续费按成交额双边计。
"""

from __future__ import annotations

import position
from indicators import ma, super_trend
from position import ExitRules
from regime import classify, efficiency_ratio

# 不启用止盈止损时用的哨兵：check() 见 enabled=False 直接返回 None，
# trail_with_st 也关掉，确保走的是原来那条「只靠翻转进出」的路径。
_OFF = ExitRules(enabled=False, trail_with_st=False)


def _bias_series(candles: list[dict], p: dict) -> list[int | None]:
    """逐根的 fast MA vs slow MA 偏向，用于回测中按时间对齐过滤。"""
    closes = [c["c"] for c in candles]
    fast = ma(closes, p.get("fast_len", 20), p.get("ma_type", "EMA"))
    slow = ma(closes, p.get("slow_len", 50), p.get("ma_type", "EMA"))
    out = []
    for f, s in zip(fast, slow):
        out.append(None if f is None or s is None else (1 if f >= s else -1))
    return out


def run_backtest(
    candles: list[dict],
    p: dict,
    init_cash: float = 10000.0,
    fee_rate: float = 0.0005,
    allow_short: bool = True,
    bias_filter: bool = False,
    er_min: float | None = None,
    exit_rules: ExitRules | None = None,
    sizing: str = "equity",
    margin_usdt: float = 10.0,
    leverage: int = 1,
    er_weak_min: float | None = None,
    exit_rules_quick: ExitRules | None = None,
) -> dict:
    periods = p.get("periods", 15)
    if len(candles) < periods + 5:
        return {"error": "K线不足，无法回测"}

    st = super_trend(
        [c["o"] for c in candles], [c["h"] for c in candles],
        [c["l"] for c in candles], [c["c"] for c in candles],
        periods=periods, multiplier=p.get("multiplier", 9.1),
        src=p.get("src", "hl2"), change_atr=p.get("change_atr", True),
    )
    if not st.get("flips"):
        return {"error": "该区间内无翻转信号（可调小 ATR 倍数或加长区间）"}

    biases = _bias_series(candles, p) if bias_filter else []
    flip_at = {f["i"]: f["type"] for f in st["flips"]}
    trend, up, dn = st["trend"], st["up"], st["dn"]

    # 两档出场规则。弱档三个条件都齐才算启用（有闸门、有弱档下界、有弱档规则），
    # 缺任何一个都退回「低于 er_min 一律拦」的老行为
    rules_normal = exit_rules if (exit_rules and exit_rules.enabled) else _OFF
    quick_on = (er_min is not None and er_weak_min is not None
                and exit_rules_quick is not None and exit_rules_quick.enabled)
    rules_by = {"normal": rules_normal,
                "quick": exit_rules_quick if quick_on else _OFF}
    lev = max(1, int(leverage))
    fixed = sizing == "fixed"

    equity = init_cash       # 已实现权益（现金）
    pos: position.Position | None = None
    cur: dict | None = None  # 当前这笔的累计记录（一笔可能分多次离场）
    trades: list[dict] = []
    curve: list[dict] = []
    peak = init_cash
    max_dd = 0.0
    n_block = n_tp1 = n_stop = n_rev = n_liq = n_skip = 0

    def st_line(i: int) -> float | None:
        """该根的超趋线值，同 feed._st_line：多头取 up、空头取 dn。"""
        if trend[i] is None:
            return None
        return up[i] if trend[i] == 1 else dn[i]

    def unrealized(price: float) -> float:
        return pos.float_pnl(price) if pos else 0.0

    def liq_price() -> float | None:
        """爆仓价近似 —— 不含维持保证金率，是乐观估计。仅 fixed + 杠杆>1 时有意义。"""
        if not fixed or lev <= 1 or not pos:
            return None
        return pos.entry * (1 - 1 / lev) if pos.long else pos.entry * (1 + 1 / lev)

    def close_part(i: int, price: float, qty: float, reason: str):
        """部分/全部离场。qty 单位同 pos.qty（ct_val=1，即币数）。"""
        nonlocal equity, pos, cur
        gross = qty * (price - pos.entry) * (1 if pos.long else -1)
        fee = qty * price * fee_rate
        equity += gross - fee
        cur["pnl"] += gross - fee
        cur["gross"] += gross
        cur["exits"].append({
            "px": round(price, 6), "qty": qty,
            "reason": reason, "ts": candles[i]["ts"],
        })
        position.apply_close(pos, price, qty, reason)
        if pos.qty <= 0:
            finalize(i)

    def finalize(i: int):
        nonlocal pos, cur
        ex = cur["exits"]
        tot_q = sum(e["qty"] for e in ex) or 1.0
        avg_exit = sum(e["px"] * e["qty"] for e in ex) / tot_q
        base = cur["qty0"] * cur["entry"]
        trades.append({
            "side":     cur["side"],
            "entry_ts": cur["entry_ts"], "exit_ts": ex[-1]["ts"],
            "entry":    round(cur["entry"], 6), "exit": round(avg_exit, 6),
            "pnl_pct":  round(cur["gross"] / base * 100, 2) if base else 0.0,
            "pnl":      round(cur["pnl"], 2),
            "bars":     i - cur["entry_i"],
            "exits":    ex,
            "reason":   ex[-1]["reason"],
            "profile":  cur.get("profile", "normal"),
        })
        pos, cur = None, None

    def open_pos(i: int, price: float, typ: str, profile: str = "normal"):
        nonlocal equity, pos, cur, n_skip
        if fixed:
            if equity < margin_usdt:
                n_skip += 1
                return
            notional = margin_usdt * lev
            qty = notional / price
            equity -= qty * price * fee_rate
        else:
            # 这三行的写法要和改造前逐字一致，否则浮点结果会有微小漂移
            qty = equity / price
            equity -= qty * price * fee_rate
            qty = equity / price

        sig = {"price": price, "type": typ, "line": st_line(i)}
        pos = position.Position(
            symbol="BT", side="long" if typ == "buy" else "short", tf="",
            entry=price, qty=qty, init_qty=qty,
            stop=position.initial_stop(sig, rules_by[profile]),
            leverage=lev, entry_ts=candles[i]["ts"], ct_val=1.0, profile=profile,
        )
        cur = {
            "side": pos.side, "entry": price, "entry_ts": candles[i]["ts"],
            "entry_i": i, "qty0": qty, "pnl": 0.0, "gross": 0.0, "exits": [],
            "profile": profile,
        }

    for i, c in enumerate(candles):
        rules = rules_by[pos.profile] if pos else rules_normal
        # ── 1) 盘中止盈止损（先止损后止盈，同一根同时满足时算止损） ──
        if pos and rules.enabled:
            adverse = c["l"] if pos.long else c["h"]
            liq = liq_price()
            hit_s = position.hit_stop(pos, adverse)
            hit_l = liq is not None and (adverse <= liq if pos.long else adverse >= liq)
            if hit_s or hit_l:
                if hit_s and hit_l:
                    # 价格朝不利方向走，先碰到的是离开仓价更近的那个
                    px = max(pos.stop, liq) if pos.long else min(pos.stop, liq)
                    was_liq = px == liq
                else:
                    px, was_liq = (liq, True) if hit_l else (pos.stop, False)
                # 跳空：开盘就已在止损之外时，只能成交在开盘价附近，
                # 仍按止损价记账会系统性高估。取更不利的一边。
                px = min(px, c["o"]) if pos.long else max(px, c["o"])
                if was_liq:
                    n_liq += 1
                else:
                    n_stop += 1
                close_part(i, px, pos.qty, "爆仓" if was_liq else
                           ("保本止损" if pos.breakeven else "止损"))
            else:
                favorable = c["h"] if pos.long else c["l"]
                act = position.check(pos, favorable, rules)
                if act and act["action"] == "tp1":
                    # 按触发价成交，不是按最高价 —— 挂单挂在 tp1_pct 那个位置
                    trig = pos.entry * (1 + rules.tp1_pct / 100) if pos.long \
                        else pos.entry * (1 - rules.tp1_pct / 100)
                    q = pos.qty * act["ratio"] / 100
                    gross = q * (trig - pos.entry) * (1 if pos.long else -1)
                    fee = q * trig * fee_rate
                    equity += gross - fee
                    cur["pnl"] += gross - fee
                    cur["gross"] += gross
                    cur["exits"].append({"px": round(trig, 6), "qty": q,
                                         "reason": "止盈", "ts": c["ts"]})
                    position.apply_tp1(pos, trig, q, rules)
                    n_tp1 += 1
                    if pos.qty <= 0:
                        finalize(i)

        # ── 2) 翻转信号 ──
        typ = flip_at.get(i)
        if typ:
            price = c["c"]
            # 反向持仓先平 —— 闸门管不着这一步（还原 executor.on_signal）
            if pos and ((pos.long and typ == "sell") or (not pos.long and typ == "buy")):
                n_rev += 1
                close_part(i, price, pos.qty, "反向信号")

            if pos is None and not (typ == "sell" and not allow_short):
                ok = True
                profile = "normal"
                if bias_filter:
                    b = biases[i] if i < len(biases) else None
                    ok = (b == 1 and typ == "buy") or (b == -1 and typ == "sell")
                if ok and er_min is not None:
                    # 和实时路径同一套分档判定（regime.classify），别另写一份。
                    # er_trend 只影响 label，trend/weak 都是 normal 档，传什么都一样
                    er = efficiency_ratio(candles[:i + 1])
                    reg = classify(er, er_min,
                                   er_weak_min=er_weak_min if quick_on else None,
                                   quick_enabled=quick_on)
                    if reg["tradable"]:
                        profile = reg["profile"] or "normal"
                    else:
                        ok = False
                        n_block += 1
                if ok:
                    open_pos(i, price, typ, profile)

        # ── 3) 收盘后随超趋线移动止损（对应 feed.on_st_line） ──
        # 注意重取规则：本根刚开的仓，循环顶部的 rules 还是开仓前的旧值
        if pos:
            r3 = rules_by[pos.profile]
            if r3.enabled:
                position.trail(pos, st_line(i), r3)

        eq = equity + unrealized(c["c"])
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100)
        curve.append({"ts": c["ts"], "equity": round(eq, 2)})

    # 末尾未平仓按最后收盘结算（标记为持仓中）
    if pos:
        last_i = len(candles) - 1
        close_part(last_i, candles[last_i]["c"], pos.qty, "区间结束")
        trades[-1]["open"] = True

    final = curve[-1]["equity"] if curve else init_cash
    wins   = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    ret  = (final - init_cash) / init_cash * 100
    hold = (candles[-1]["c"] - candles[0]["c"]) / candles[0]["c"] * 100
    gain = sum(t["pnl"] for t in wins)
    loss = abs(sum(t["pnl"] for t in losses))

    return {
        "init_cash":     init_cash,
        "final":         round(final, 2),
        "return_pct":    round(ret, 2),
        "hold_pct":      round(hold, 2),        # 同期买入持有基准
        "alpha_pct":     round(ret - hold, 2),
        "max_dd_pct":    round(max_dd, 2),
        "trades":        len(trades),
        "wins":          len(wins),
        "win_rate":      round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "avg_win":       round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss":      round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else 0.0,
        "profit_factor": round(gain / loss, 2) if loss > 0 else None,
        "avg_bars":      round(sum(t["bars"] for t in trades) / len(trades), 1) if trades else 0,
        "bars":          len(candles),
        "start_ts":      candles[0]["ts"],
        "end_ts":        candles[-1]["ts"],
        # ── 闸门 / 离场统计 ──
        "er_blocked":    n_block,
        "tp1_count":     n_tp1,
        "stop_count":    n_stop,
        "reverse_count": n_rev,
        "liq_count":     n_liq,
        "skipped_insufficient": n_skip,
        "quick_trades":  sum(1 for t in trades if t.get("profile") == "quick"),
        "trade_list":    trades[-80:],
        "equity":        curve[:: max(1, len(curve) // 300)],   # 抽稀到 ~300 点
    }


def sweep(candles: list[dict], base: dict, periods: list[int], mults: list[float],
          fee_rate: float = 0.0005, allow_short: bool = True,
          bias_filter: bool = False, **kw) -> list[dict]:
    """参数寻优：网格跑 ATR Period × Multiplier，按收益率降序。

    脚本默认 15 / 9.1 是作者在某个品种上调出来的，换品种/周期基本要重调，
    所以这个网格是本项目的必要补充。kw 透传 er_min / exit_rules / sizing 等。
    """
    out = []
    for pe in periods:
        for m in mults:
            r = run_backtest(
                candles, {**base, "periods": pe, "multiplier": m},
                fee_rate=fee_rate, allow_short=allow_short, bias_filter=bias_filter, **kw,
            )
            if "error" in r:
                continue
            out.append({
                "periods": pe, "multiplier": round(m, 2),
                "return_pct": r["return_pct"], "max_dd_pct": r["max_dd_pct"],
                "trades": r["trades"], "win_rate": r["win_rate"],
                "profit_factor": r["profit_factor"],
            })
    out.sort(key=lambda r: r["return_pct"], reverse=True)
    return out


def sweep_er(candles: list[dict], p: dict, er_list: list[float],
             fee_rate: float = 0.0005, allow_short: bool = True,
             bias_filter: bool = False, **kw) -> list[dict]:
    """扫 er_min 闸门阈值。第一行是「无闸门」基准，其余按传入顺序（不排序 ——
    这张表要看的是阈值升高时各指标的走势，重排就看不出单调性了）。"""
    out = []
    for em in [None] + list(er_list):
        r = run_backtest(candles, p, fee_rate=fee_rate, allow_short=allow_short,
                         bias_filter=bias_filter, er_min=em, **kw)
        if "error" in r:
            continue
        out.append({
            "er_min": em, "return_pct": r["return_pct"], "max_dd_pct": r["max_dd_pct"],
            "trades": r["trades"], "blocked": r["er_blocked"],
            "win_rate": r["win_rate"], "profit_factor": r["profit_factor"],
            "alpha_pct": r["alpha_pct"],
        })
    return out
