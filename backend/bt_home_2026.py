# -*- coding: utf-8 -*-
"""首页策略 · 2026 年全年回测（43 / 47 两页线上当前配置，固定 100U 名义 × 1x）

================================================================================
口径
--------------------------------------------------------------------------------
① 策略 = 首页策略：backtest_engine.run_backtest
       ST 翻转信号 + 线上评分闸门(score_engine) + position_enhanced 出场状态机，
       与 backtest_mu_home / backtest_stock_47 完全同一套。

② 参数 = 各品种在 43 页(43.108.10.84) / 47 页(47.84.106.154) 上的**当前**配置，
       实时拉 /api/trade/symbols，含 params(ST) / exit_rules / 闸门阈值 / 评分引擎。
       不用任何本地旧快照。

③ 仓位 = **固定 100U 名义 × 1x**（消除各品种 margin/leverage 不同的干扰）：
       sizing="fixed"  margin_usdt=100  leverage=1  → 每笔名义 = 100 × 1 = 100U
       init_cash 只作现金池(10000)，保证永远不会因"权益<保证金"被 skip。

④ 收益 = Σ 单笔净收益%。每笔名义固定 100U，故
       单笔净% = pnl(U) / 100U × 100 = pnl(U)（数值相等），
       总收益% = Σ pnl(U)。**不复利、不缩仓**，正是"固定 100×1x"的算术含义。
       盈亏比 = 平均盈利% / |平均亏损%|（净，含手续费）。

⑤ 数据 = OKX 收盘 K(confirm=1) 实时拉，与实盘判信号口径一致；
       窗口 2026-01-01 ~ 数据末尾；gate_tf 取配置的 allow_tfs[0]（都是 1h）。

用法：
    python bt_home_2026.py                 # 全部品种（43+47 两页）
    python bt_home_2026.py --sym MU        # 单品种冒烟
    python bt_home_2026.py --no-15m        # 不拉 15m（快很多）
================================================================================
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backtest_mu_home as M          # fetch_candles / trade_cfg / exit_rules
from backtest_engine import run_backtest

LIVE43 = "http://43.108.10.84:5174/api/trade/symbols"
LIVE47 = "http://47.84.106.154:5174/api/trade/symbols"

# 待跑品种（用户指定）
WANT = ["NVDA", "CL", "MU", "SNDK", "SKHYNIX", "TSLA",
        "BTC", "ETH", "INTC", "AMD", "XAU"]

# 2026 全年所需根数（1h 约 7300 根/年）
BARS26 = {"15m": 29000, "1h": 7300, "4h": 1900, "1d": 320}
TFS_NO15 = ["1h", "4h", "1d"]
TFS_ALL = ["15m", "1h", "4h", "1d"]

MARGIN = 100.0     # 每笔固定保证金 → 名义 = 100 × 1
LEV = 1            # 1x 无杠杆
INIT = 10_000.0    # 现金池，避免 fixed sizing 的 skip


def y26_lo():
    return int(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _get(url, timeout=25):
    req = M.urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with M.urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def load_live():
    """拉两页线上当前配置，返回 {页: {品种名: sym}}。"""
    out = {}
    for tag, url in (("43", LIVE43), ("47", LIVE47)):
        try:
            d = _get(url)
        except Exception as e:
            print(f"!! {tag} 页拉取失败: {e}")
            out[tag] = {}
            continue
        m = {}
        for s in d["symbols"]:
            base = s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
            m[base] = s
        out[tag] = m
    return out


def load_snapshot():
    """读本地旧快照 _cfg_43.json / _cfg_47.json。

    这两个文件是两页早前导出的配置（当时还挂着 TSLA/INTC/AMD/XAU，现线上已下架）。
    快照只有出场参数，**没有 params(ST) 和闸门阈值**，所以这里：
      - ST 参数补 v1 全项目基线 10×3.0/hl2（router.py 的固定口径）
      - 闸门全部关闭（use_scoring=False, score_engine='', range/mtf/atr/adx 全 False）
    结果因此不可与线上品种直接横向比，只在汇总里单独标注来源。
    """
    out = {"43": {}, "47": {}}
    for tag, fn in (("43", "_cfg_43.json"), ("47", "_cfg_47.json")):
        p = os.path.join(HERE, fn)
        if not os.path.exists(p):
            continue
        j = json.load(open(p, encoding="utf-8"))
        for raw in j.get("symbols", []):
            base = raw["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
            sl_pct = raw.get("sl_pct")
            sym = dict(
                symbol=raw["symbol"], enabled=True,
                margin_usdt=MARGIN, leverage=LEV, allow_tfs=raw.get("allow_tfs") or ["1h"],
                params={"periods": 10, "multiplier": 3.0, "src": "hl2",
                        "change_atr": True, "fast_len": 20, "slow_len": 50,
                        "ma_type": "EMA"},
                er_hide_below=0.0, er_weak_min=0.0, er_min=0.0, er_trend=0.0,
                quick_enabled=False, allow_grades=["A", "B", "C"], min_score=0,
                cooldown_sec=300, atr_filter_enabled=False, atr_vol_min=0.7,
                range_filter_enabled=False, range_size_max=0.15, range_touches_min=3,
                mtf_filter_enabled=False, mtf_consistency_min=0.6, mtf_flip_max=5,
                adx_filter_enabled=False, adx_min=20.0, adx_period=14,
                use_scoring=False, scoring_full_threshold=0.0,
                scoring_half_threshold=0.0, scoring_alert_threshold=0.0,
                use_dynamic_threshold=False, score_engine="",
                exit_rules=dict(
                    enabled=True,
                    tp1_pct=raw.get("tp1_pct") or 1.5,
                    tp1_ratio=raw.get("tp1_ratio") or 70.0,
                    tp2_pct=999.0, tp2_ratio=0.0,
                    tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct",
                    move_sl_to_entry=True if raw.get("move_sl_to_entry") is None
                    else bool(raw.get("move_sl_to_entry")),
                    sl_mode=raw.get("sl_mode") or "pct",
                    sl_pct=3.0 if sl_pct is None else float(sl_pct),
                    trail_with_st=False if raw.get("trail_with_st") is None
                    else bool(raw.get("trail_with_st")),
                    max_loss_enabled=False, max_loss_pct=10.0))
            out[tag][base] = sym
    return out


def pick(want, live, snap):
    """为每个品种挑配置来源：43 页优先，其次 47 页；两页都有则都跑。
    两页线上都没有 → 退回本地旧快照（tag 加 * 号标记）。"""
    plan = []
    for w in want:
        hits = [(tag, live.get(tag, {}).get(w)) for tag in ("43", "47")]
        hits = [(t, s, False) for t, s in hits if s]
        if not hits:
            hits = [(tag, snap.get(tag, {}).get(w), True) for tag in ("43", "47")]
            hits = [(t, s, sn) for t, s, sn in hits if s]
        if not hits:
            plan.append((w, None, None, False))
        else:
            plan.extend((w, t, s, sn) for t, s, sn in hits)
    return plan


def stats(r):
    """按固定 100U 名义 × 1x 折算：笔数 / 盈亏比 / 胜率 / Σ单笔净%。"""
    tr = r.get("trades_list") or []
    n = len(tr)
    if not n:
        return dict(n=0, payoff=0.0, wr=0.0, tot=0.0, aw=0.0, al=0.0)
    notional = MARGIN * LEV            # 100U
    pcts = [t["pnl"] / notional * 100 for t in tr]   # 每笔净%
    wins = [p for p in pcts if p > 0]
    loss = [p for p in pcts if p <= 0]
    aw = sum(wins) / len(wins) if wins else 0.0
    al = (sum(loss) / len(loss)) if loss else 0.0
    payoff = (aw / abs(al)) if al else float("inf")
    # 复利口径：账户 100U、每笔满仓 100U 名义 1x，逐笔滚存
    comp = 1.0
    for p in pcts:
        comp *= (1 + p / 100)
    # Profit Factor（总盈/总亏）
    pf = (sum(wins) / abs(sum(loss))) if loss else float("inf")
    # 固定 100U 本金的累计收益回撤（mdd_u 数值上 = mdd%）
    cum = [0.0]
    for p in pcts:
        cum.append(cum[-1] + p)
    peak = cum[0]
    mdd = 0.0
    for v in cum:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > mdd:
            mdd = dd
    return dict(n=n, payoff=payoff, wr=len(wins) / n * 100,
                tot=sum(pcts), comp=(comp - 1) * 100, pf=pf, mdd_u=mdd,
                aw=aw, al=al, wins=len(wins), loss=len(loss))


def run_one(name, tag, sym, tfs, quiet=False):
    gate_tf = sym["allow_tfs"][0]
    lo = y26_lo()
    cbtf = {}
    for tf in tfs:
        arr = M.fetch_candles(sym["symbol"], tf, BARS26[tf])
        arr = [c for c in arr if c["ts"] >= lo]
        cbtf[tf] = arr
        if not quiet:
            if arr:
                print(f"    {tf}: {len(arr)} 根 "
                      f"{M.ts_fmt(arr[0]['ts'])} ~ {M.ts_fmt(arr[-1]['ts'])}", flush=True)
            else:
                print(f"    {tf}: 0 根（拉取失败）", flush=True)
    seg = cbtf.get(gate_tf) or []
    if len(seg) < 100:
        return dict(err=f"{gate_tf} 2026 年 K 线不足 ({len(seg)})")
    hi = seg[-1]["ts"]
    cbtf_seg = {tf: [c for c in arr if lo <= c["ts"] <= hi]
                for tf, arr in cbtf.items() if arr}
    ex = M.exit_rules(sym)
    cfg = M.trade_cfg(sym)
    r = run_backtest(
        seg, sym["params"], init_cash=INIT, fee_rate=0.0005, allow_short=True,
        exit_rules=ex, sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
        live_gate=cfg, gate_tf=gate_tf, candles_by_tf=cbtf_seg)
    if "error" in r:
        return dict(err=r["error"])
    s = stats(r)
    s.update(dict(sym=sym["symbol"], tag=tag, gate_tf=gate_tf,
                  params=sym["params"], hold=r["hold_pct"],
                  blocked=r.get("er_blocked", 0),
                  skipped=r.get("skipped_insufficient", 0),
                  start=M.ts_fmt(seg[0]["ts"]), end=M.ts_fmt(hi),
                  bars=len(seg)))
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym", default="", help="只跑该品种（逗号分隔）")
    ap.add_argument("--no-15m", action="store_true", help="不拉 15m（快）")
    ap.add_argument("--out", default="", help="落盘")
    a = ap.parse_args()

    if a.out:
        import io
        import atexit
        buf = io.StringIO()
        real = sys.stdout

        class Tee:
            def write(self, s):
                real.write(s)
                buf.write(s)

            def flush(self):
                real.flush()
        sys.stdout = Tee()

        @atexit.register
        def _dump():
            sys.stdout = real
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(buf.getvalue())
            real.write(f"\n[输出已写入 {a.out}]\n")

    tfs = TFS_NO15 if a.no_15m else TFS_ALL
    want = [x.strip().upper() for x in a.sym.split(",") if x.strip()] or WANT
    live = load_live()

    print("=" * 108)
    print("首页策略 · 2026 年全年回测 —— 43/47 两页线上当前配置 · 固定 100U 名义 × 1x")
    print("=" * 108)
    print(f"  窗口 2026-01-01 ~ 数据末尾 ｜ 周期 {tfs} ｜ gate_tf 取配置 allow_tfs[0]")
    print(f"  仓位 sizing=fixed margin={MARGIN:.0f}U lev={LEV}x → 每笔名义 {MARGIN*LEV:.0f}U"
          f" ｜ 现金池 {INIT:.0f}U（防 skip）")
    print(f"  收益 = Σ单笔净%（每笔固定 100U 名义，不复利）｜ 盈亏比 = 平均盈/平均亏（净，含手续费）")

    snap = load_snapshot()
    plan = pick(want, live, snap)

    rows, missing = [], []
    for name, tag, sym, is_snap in plan:
        if sym is None:
            missing.append(name)
            continue
        shown = f"{tag}*" if is_snap else tag
        print(f"\n────── {name} [{shown} 页] {sym['symbol']}"
              f"{'  ⚠ 线上已下架，用本地旧快照(ST取v1基线10×3.0,闸门全关)' if is_snap else ''} ──────")
        print(f"    params={json.dumps(sym['params'], ensure_ascii=False)}")
        er = sym.get("exit_rules") or {}
        print(f"    se={sym.get('score_engine')!r} thr="
              f"{sym.get('scoring_full_threshold')}/{sym.get('scoring_half_threshold')} "
              f"range={sym.get('range_filter_enabled')} "
              f"exit: tp1={er.get('tp1_pct')}%/{er.get('tp1_ratio')}% "
              f"sl={er.get('sl_pct')}%({er.get('sl_mode')}) "
              f"trail={er.get('trail_with_st')} maxloss={er.get('max_loss_enabled')}",
              flush=True)
        t0 = time.time()
        try:
            s = run_one(name, shown, sym, tfs)
        except Exception as e:
            s = dict(err=f"{type(e).__name__}: {e}")
        if "err" in s:
            print(f"    !! {s['err']}", flush=True)
            rows.append(dict(name=name, tag=shown, snap=is_snap, err=s["err"]))
            continue
        print(f"    笔数={s['n']} 盈亏比={s['payoff']:.2f} 胜率={s['wr']:.1f}% "
              f"Σ收益={s['tot']:+.2f}% 复利={s['comp']:+.2f}% PF={s['pf']:.2f} "
              f"最大回撤={s['mdd_u']:.2f}U  均盈={s['aw']:+.2f}% 均亏={s['al']:+.2f}% "
              f"｜ 买入持有={s['hold']:+.2f}% 拦={s['blocked']} skip={s['skipped']} "
              f"({time.time()-t0:.0f}s)", flush=True)
        s["name"] = name
        s["snap"] = is_snap
        rows.append(s)

    print("\n" + "=" * 108)
    print("【汇总】首页策略 2026 年（固定 100U × 1x，Σ单笔净%）")
    print("=" * 108)
    print("  注：页号带 * = 该品种线上两页当前都已下架，用的是本地旧快照 "
          "(ST 取 v1 基线 10×3.0、闸门全关)，口径与线上品种不同，不可直接横比")
    hdr = (f"{'品种':<10}{'页':<5}{'ST参数':<16}{'笔数':>5}{'胜率%':>8}{'盈亏比':>8}"
           f"{'PF':>7}{'Σ收益%':>10}{'复利%':>9}{'最大回撤U':>10}{'买入持有%':>11}{'闸门拦':>7}{'K线':>7}  区间")
    print(hdr)
    print("-" * len(hdr))
    for s in rows:
        if "err" in s:
            print(f"{s['name']:<10}{s['tag']:<5}{'—':<16}{s['err']}")
            continue
        p = s["params"]
        st = f"{p.get('periods')}×{p.get('multiplier')}/{p.get('src')}"
        print(f"{s['name']:<10}{s['tag']:<5}{st:<16}{s['n']:>5}{s['wr']:>8.1f}"
              f"{s['payoff']:>8.2f}{s['pf']:>7.2f}{s['tot']:>10.2f}{s['comp']:>9.2f}"
              f"{s['mdd_u']:>10.2f}{s['hold']:>11.2f}{s['blocked']:>7}{s['bars']:>7}  "
              f"{s['start']}~{s['end']}")
    if missing:
        print(f"\n!! 线上 43/47 两页当前都没有配置的品种：{', '.join(missing)}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
