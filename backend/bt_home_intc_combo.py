# -*- coding: utf-8 -*-
"""2026 · 1h · 首页策略「全口径」组合寻优（ST参数 × 评分阈值 × v1出场口径）。

适用品种：43 页线上仍挂着配置的品种（INTC / AMD / XAU / BTC / ETH …）。

================================================================================
口径（与 bt_home_2026.py 完全一致，可横向比）
--------------------------------------------------------------------------------
① 引擎：backtest_engine.run_backtest
       ST 翻转信号 + live_gate(线上评分闸门) + EnhancedExitRules 出场状态机。
       v1 口径 = score_engine="" → 走 trend_follow_v1 阶梯引擎
       （引擎里 v3_filter 与 live_gate 互斥，v1 只走 live_gate）。

② 基线：43 页线上该品种当前配置，**只拉一次**并冻结到
       _bt2026_data/_base43_{SYM}.json（ER阈值/range/mtf/adx/min_score/grades…
       全部继承线上）。网格只覆盖 ST / 评分阈值 / 出场三组参数。
       （旧脚本 _opt_v1.py 每组都重新拉线上 → 又慢又可能中途配置漂移）

③ 仓位：fixed 100U × 1x，init_cash=10000 现金池；taker 0.05%/边。
       收益 = Σ单笔净%（每笔名义固定 100U，不复利）。

④ 数据：_bt2026_data/{SYM}_USDT_SWAP.json（OKX 收盘 K confirm=1），窗口
       2026-01-01 起，gate_tf=1h；15m/4h/1d 供 mtf_bias / 评分引擎用。

防过拟合
--------------------------------------------------------------------------------
· IS/OOS：全样本只跑一次，按每笔 entry_ts 切成 IS(前70%) / OOS(后30%)。
  只有 IS 参与选优，OOS 只报告 —— 这样才有「是不是运气」的判断依据。
  不按数据段各跑一次，是为了避免 OOS 段缺 warmup 导致指标/评分失真。
· 邻域矩阵：periods × multiplier 的 IS/OOS 收益面，看最优点是高原还是孤峰。
· 全网格统计：OOS 为正的比例、OOS 跑赢同期买入持有的比例。
· 断点续跑：每算完一组立刻追加写 JSONL，中断后重跑自动跳过。

用法
--------------------------------------------------------------------------------
    python bt_home_intc_combo.py --sym INTC --quick      # 小网格探路/测速
    python bt_home_intc_combo.py --sym AMD               # 默认两阶段全跑
    python bt_home_intc_combo.py --sym AMD --stage 1     # 只跑 ST × 评分阈值
    python bt_home_intc_combo.py --sym AMD --dyn         # 阈值维度加 use_dynamic_threshold
    python bt_home_intc_combo.py --sym AMD --is-ratio 0.6   # 换切点做稳健性复验
    python bt_home_intc_combo.py --sym AMD --out _amd_combo.txt
================================================================================
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bt_home_2026 as B
from backtest_engine import run_backtest

TFS = ["15m", "1h", "4h", "1d"]
GATE_TF = "1h"
IS_RATIO = 0.70
MIN_TRADES = 12          # 太少的组合不参与排序（样本不足）

# ── 搜索空间 ──────────────────────────────────────────────────────
P_FULL = [5, 7, 9, 10, 12, 15, 18]
M_FULL = [2.0, 2.5, 3.0, 3.5, 4.0]
P_QUICK = [7, 10, 14]
M_QUICK = [2.5, 3.0]
GATES_FULL = [30, 40, 50]
GATES_QUICK = [40]
TP1_FULL = [1.5, 2.0, 3.0]
TP1_QUICK = [2.0]
SL_FULL = [2.0, 3.0, 4.0]
SL_QUICK = [3.0]
TRAIL_FULL = [False, True]
TRAIL_QUICK = [False, True]

# Stage1 固定出场（v1 中间口径），Stage2 才展开出场维度
BASE_EXIT = dict(tp1=2.0, sl=3.0, trail=False)


# ═══════════════════════════════════════════════════════════════
# 数据 / 基线配置
# ═══════════════════════════════════════════════════════════════
def load_base(sym):
    p = os.path.join(HERE, "_bt2026_data", f"_base43_{sym}.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    s = B.load_live()["43"][sym]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    print(f"[基线] 已从线上 43 页拉取并冻结 → {os.path.basename(p)}")
    return s


def load_data(sym):
    raw = json.load(open(os.path.join(HERE, "_bt2026_data",
                                      f"{sym}_USDT_SWAP.json"), encoding="utf-8"))
    lo = B.y26_lo()
    cbtf = {}
    for tf in TFS:
        arr = raw.get(tf) or []
        cbtf[tf] = [c for c in arr if c["ts"] >= lo]
    return cbtf


# ═══════════════════════════════════════════════════════════════
# 组合 → sym 配置
# ═══════════════════════════════════════════════════════════════
def build_sym(base, st, ex, gate, dyn=True, se="", src="hl2", ca=True):
    """st=(periods,multiplier)  ex=(tp1_pct, sl_pct, trail_with_st)  gate=评分阈值。"""
    s = copy.deepcopy(base)
    s["allow_tfs"] = [GATE_TF]
    s["score_engine"] = se
    s["use_scoring"] = True
    s["use_dynamic_threshold"] = dyn
    s["scoring_full_threshold"] = float(gate)
    s["scoring_half_threshold"] = float(gate)
    s["scoring_alert_threshold"] = float(gate)
    s["params"] = {"periods": st[0], "multiplier": st[1], "src": src,
                   "change_atr": ca, "fast_len": 20, "slow_len": 50,
                   "ma_type": "EMA"}
    s["exit_rules"] = {
        "enabled": True, "tp1_pct": ex[0], "tp1_ratio": 50.0,
        "tp2_pct": 999.0, "tp2_ratio": 0.0,
        "tp3_pct": 999.0, "tp3_ratio": 0.0, "tp3_mode": "pct",
        "move_sl_to_entry": True, "sl_mode": "pct", "sl_pct": ex[1],
        "trail_with_st": ex[2], "sl_buffer_atr": 0.5, "sl_min_pct": 1.2,
        "protect_profit_at": 1.5, "protect_trail_pct": 0.8,
        "max_loss_enabled": False, "max_loss_pct": 10.0,
    }
    return s


def agg(trades, notional=100.0):
    """固定 100U 名义 × 1x：笔数/胜率/盈亏比/PF/Σ净%/复利/最大回撤。"""
    if not trades:
        return dict(n=0, wr=0.0, payoff=0.0, pf=0.0, tot=0.0, comp=0.0, mdd=0.0)
    pcts = [t["pnl"] / notional * 100 for t in trades]
    wins = [p for p in pcts if p > 0]
    loss = [p for p in pcts if p <= 0]
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(loss) / len(loss) if loss else 0.0
    comp = 1.0
    for p in pcts:
        comp *= (1 + p / 100)
    cum, peak, mdd = [0.0], 0.0, 0.0
    for p in pcts:
        v = cum[-1] + p
        cum.append(v)
        peak = max(peak, v)
        mdd = max(mdd, peak - v)
    return dict(n=len(pcts), wr=len(wins) / len(pcts) * 100,
                payoff=(aw / abs(al)) if al else float("inf"),
                pf=(sum(wins) / abs(sum(loss))) if loss else float("inf"),
                tot=sum(pcts), comp=(comp - 1) * 100, mdd=mdd)


def run_one(sym, cbtf):
    seg = cbtf[GATE_TF]
    hi = seg[-1]["ts"]
    cbtf_seg = {tf: [c for c in arr if c["ts"] <= hi] for tf, arr in cbtf.items() if arr}
    r = run_backtest(
        seg, sym["params"], init_cash=B.INIT, fee_rate=0.0005, allow_short=True,
        exit_rules=B.M.exit_rules(sym), sizing="fixed",
        margin_usdt=B.MARGIN, leverage=B.LEV,
        live_gate=B.M.trade_cfg(sym), gate_tf=GATE_TF, candles_by_tf=cbtf_seg)
    if "error" in r:
        return None, r["error"]
    return r, None


def evaluate(sym, cbtf, split_ts):
    """跑一次全样本，按 entry_ts 拆 IS/OOS 统计。"""
    r, err = run_one(sym, cbtf)
    if err:
        return None, err
    tr = r.get("trades_list") or []
    is_tr = [t for t in tr if t["entry_ts"] < split_ts]
    oos_tr = [t for t in tr if t["entry_ts"] >= split_ts]
    a_all, a_is, a_oos = agg(tr), agg(is_tr), agg(oos_tr)
    return dict(all=a_all, is_=a_is, oos=a_oos,
                hold=r["hold_pct"], blocked=r.get("er_blocked", 0),
                skip=r.get("skipped_insufficient", 0),
                cal_is=(a_is["tot"] / a_is["mdd"]) if a_is["mdd"] > 1e-9 else 0.0), None


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════
def main():
    global IS_RATIO
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="小网格探路")
    ap.add_argument("--stage", choices=["1", "2", "both"], default="both")
    ap.add_argument("--dyn", action="store_true",
                    help="评分阈值维度额外扫 use_dynamic_threshold(True/False)")
    ap.add_argument("--sym", default="INTC", help="品种名（43 页线上挂着配置的），如 AMD / XAU / ETH")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--is-ratio", type=float, default=IS_RATIO,
                    help="IS 占比（换切点做稳健性检验，如 0.6 / 0.5）")
    ap.add_argument("--out", default="", help="同时把输出写入文件")
    a = ap.parse_args()
    IS_RATIO = a.is_ratio

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

    SYM = a.sym.strip().upper()
    base = load_base(SYM)
    cbtf = load_data(SYM)
    seg = cbtf[GATE_TF]
    split_i = int(len(seg) * IS_RATIO)
    split_ts = seg[split_i]["ts"]
    f = B.M.ts_fmt
    bh_all = (seg[-1]["c"] / seg[0]["c"] - 1) * 100
    bh_is = (seg[split_i]["c"] / seg[0]["c"] - 1) * 100
    bh_oos = (seg[-1]["c"] / seg[split_i]["c"] - 1) * 100

    P = P_QUICK if a.quick else P_FULL
    M = M_QUICK if a.quick else M_FULL
    GATES = GATES_QUICK if a.quick else GATES_FULL
    TP1 = TP1_QUICK if a.quick else TP1_FULL
    SL = SL_QUICK if a.quick else SL_FULL
    TRAIL = TRAIL_QUICK if a.quick else TRAIL_FULL
    DYNS = [True, False] if a.dyn else [True]

    print("=" * 112)
    print(f"{SYM} 2026 · 1h · 首页策略全口径组合寻优（v1 阶梯引擎口径）")
    print("=" * 112)
    print(f"  数据 {GATE_TF} {len(seg)} 根 {f(seg[0]['ts'])} ~ {f(seg[-1]['ts'])} UTC"
          f" ｜ 15m/4h/1d 各 {len(cbtf['15m'])}/{len(cbtf['4h'])}/{len(cbtf['1d'])} 根")
    print(f"  IS/OOS 切点 {f(split_ts)}（第 {split_i} 根, 前 {IS_RATIO:.0%}）"
          f" ｜ 买入持有：全样本 {bh_all:+.2f}%  IS {bh_is:+.2f}%  OOS {bh_oos:+.2f}%")
    print(f"  基线 ST={base['params']} ｜ se={base.get('score_engine')!r} "
          f"thr={base.get('scoring_full_threshold')}/{base.get('scoring_half_threshold')}"
          f"/{base.get('scoring_alert_threshold')} dyn={base.get('use_dynamic_threshold')}")
    print(f"  网格：periods={P} multiplier={M} 评分阈值={GATES} dyn={DYNS}（src=hl2, ATR=RMA 固定）")
    print(f"        出场 tp1={TP1} sl={SL} trail={TRAIL}（tp1_ratio=50%, 保本, sl_mode=pct, tp2/tp3 关）")
    print(f"  仓位 fixed {B.MARGIN:.0f}U × {B.LEV}x ｜ 收益=Σ单笔净%（不复利）"
          f" ｜ 选优只看 IS，OOS 只报告")

    jsonl = os.path.join(HERE, f"_opt_combo_{SYM}.jsonl")
    done = {}
    if os.path.exists(jsonl):
        for line in open(jsonl, encoding="utf-8"):
            try:
                o = json.loads(line)
                done[(tuple(o["st"]), o["gate"], o["dyn"], tuple(o["ex"]),
                      o.get("src", "hl2"), bool(o.get("ca", True)),
                      round(float(o.get("isr", 0.70)), 3))] = o["r"]
            except Exception:
                pass
    fout = open(jsonl, "a", encoding="utf-8")

    def calc(st, gate, dyn, ex, src="hl2", ca=True):
        key = (st, gate, dyn, ex, src, ca, round(IS_RATIO, 3))
        if key in done:
            return done[key]
        sym = build_sym(base, st, ex, gate, dyn, src=src, ca=ca)
        r, err = evaluate(sym, cbtf, split_ts)
        if err:
            r = dict(err=err)
        o = {"st": list(st), "gate": gate, "dyn": dyn, "ex": list(ex),
             "src": src, "ca": ca, "isr": round(IS_RATIO, 3), "r": r}
        fout.write(json.dumps(o, ensure_ascii=False) + "\n")
        fout.flush()
        done[key] = r
        return r

    # ── Stage 0：线上当前配置的基线（v2 口径 + 线上三档出场，原样不动）──
    sym0 = copy.deepcopy(base)
    sym0["allow_tfs"] = [GATE_TF]
    r0, e0 = evaluate(sym0, cbtf, split_ts)
    if e0:
        print(f"\n[基线] 线上原样配置跑失败：{e0}")
    else:
        a0, i0_, o0 = r0["all"], r0["is_"], r0["oos"]
        print(f"\n【基线 · 线上当前配置原样】ST={base['params']['periods']}×"
              f"{base['params']['multiplier']}/{base['params']['src']} "
              f"se={base.get('score_engine')!r} 阈值={base.get('scoring_full_threshold')} "
              f"tp1={base['exit_rules']['tp1_pct']}%/{base['exit_rules']['tp1_ratio']}% "
              f"sl={base['exit_rules']['sl_mode']} trail={base['exit_rules']['trail_with_st']}")
        print(f"   IS   笔 {i0_['n']:>3} 胜率 {i0_['wr']:.1f}% PF {i0_['pf']:.2f} "
              f"Σ净% {i0_['tot']:+.2f} 回撤 {i0_['mdd']:.2f}U   （同期买入持有 {bh_is:+.2f}%）")
        print(f"   OOS  笔 {o0['n']:>3} 胜率 {o0['wr']:.1f}% PF {o0['pf']:.2f} "
              f"Σ净% {o0['tot']:+.2f} 回撤 {o0['mdd']:.2f}U   （同期买入持有 {bh_oos:+.2f}%）")
        print(f"   全样本 笔 {a0['n']:>3} 胜率 {a0['wr']:.1f}% PF {a0['pf']:.2f} "
              f"Σ净% {a0['tot']:+.2f} 复利 {a0['comp']:+.2f}% 回撤 {a0['mdd']:.2f}U "
              f"  （买入持有 {bh_all:+.2f}%）｜ 闸门拦 {r0['blocked']}")

    # ── Stage 1：ST × 评分阈值（出场固定 v1 中间口径）──────────────
    rows1 = []
    if a.stage in ("1", "both"):
        ex0 = (BASE_EXIT["tp1"], BASE_EXIT["sl"], BASE_EXIT["trail"])
        total = len(P) * len(M) * len(GATES) * len(DYNS)
        print(f"\n【Stage 1】ST × 评分阈值  {total} 组"
              f"（出场固定 tp1={ex0[0]}% sl={ex0[1]}% trail={ex0[2]}）", flush=True)
        t0 = time.time()
        k = 0
        for p in P:
            for m in M:
                for g in GATES:
                    for dyn in DYNS:
                        k += 1
                        r = calc((p, m), g, dyn, ex0)
                        if "err" in r:
                            print(f"   ERR {p}×{m} g={g}: {r['err']}", flush=True)
                            continue
                        if r["is_"]["n"] < MIN_TRADES:
                            continue
                        rows1.append(dict(p=p, m=m, g=g, dyn=dyn, r=r))
                        if k % 20 == 0:
                            print(f"   进度 {k}/{total}  {time.time()-t0:.0f}s", flush=True)
        print(f"   Stage1 有效 {len(rows1)}/{total} 组（IS 笔数 ≥ {MIN_TRADES}）"
              f" 耗时 {time.time()-t0:.0f}s", flush=True)
        rows1.sort(key=lambda x: x["r"]["is_"]["tot"], reverse=True)
        print(f"\n── Stage1 IS Top{a.top}（按 IS Σ净% 排序，OOS 同参数在后 30% 的表现）──")
        hdr = (f"{'#':>3} {'ST':>9}{'阈值':>5}{'dyn':>5}{'IS笔':>6}{'IS胜率':>8}"
               f"{'IS_PF':>7}{'IS_Σ%':>9}{'IS回撤':>8}{'IS_Cal':>7}"
               f"{'OOS笔':>7}{'OOS_Σ%':>9}{'OOS回撤':>9}{'全样本%':>9}{'拦':>6}")
        print(hdr)
        print("-" * len(hdr))
        for i, x in enumerate(rows1[:a.top], 1):
            ri, ro, ra = x["r"]["is_"], x["r"]["oos"], x["r"]["all"]
            print(f"{i:>3} {x['p']}×{x['m']:<5}{x['g']:>5}{'Y' if x['dyn'] else 'N':>5}"
                  f"{ri['n']:>6}{ri['wr']:>8.1f}{ri['pf']:>7.2f}{ri['tot']:>+9.2f}"
                  f"{ri['mdd']:>8.2f}{x['r']['cal_is']:>7.2f}"
                  f"{ro['n']:>7}{ro['tot']:>+9.2f}{ro['mdd']:>9.2f}"
                  f"{ra['tot']:>+9.2f}{x['r']['blocked']:>6}")

        # 邻域矩阵：固定 Top1 的 gate/dyn，扫 periods × multiplier（IS/OOS）
        if rows1:
            b = rows1[0]
            print(f"\n── 邻域稳定性（阈值={b['g']} dyn={'Y' if b['dyn'] else 'N'}，"
                  f"格子 = IS/OOS Σ净%，看最优点是高原还是孤峰）──")
            print("  " + "".join(f"{m:>13.1f}" for m in M))
            lut = {(x["p"], x["m"]): x for x in rows1
                   if x["g"] == b["g"] and x["dyn"] == b["dyn"]}
            for p in P:
                line = f"{p:>3}"
                for m in M:
                    x = lut.get((p, m))
                    if x is None:
                        line += f"{'—':>13}"
                    else:
                        line += f"{x['r']['is_']['tot']:>6.1f}/{x['r']['oos']['tot']:<6.1f}"
                print(line)
            np_os = sum(1 for x in rows1 if x["r"]["oos"]["tot"] > 0)
            nb = sum(1 for x in rows1 if x["r"]["oos"]["tot"] > bh_oos)
            print(f"\n   Stage1 全网格 {len(rows1)} 组：OOS Σ净% 为正 {np_os} 组 ｜ "
                  f"OOS 跑赢买入持有({bh_oos:+.2f}%) {nb} 组")

    # ── Stage 2：Top ST × 出场（v1 口径展开）────────────────────
    if not rows1:
        # 单独跑 --stage 2 时，从 JSONL 缓存里重建 Stage1 结果
        ex0 = (BASE_EXIT["tp1"], BASE_EXIT["sl"], BASE_EXIT["trail"])
        for (st, g, dyn, ex, src, ca, isr), r in done.items():
            if (ex != ex0 or src != "hl2" or not ca or "err" in r
                    or isr != round(IS_RATIO, 3) or r["is_"]["n"] < MIN_TRADES):
                continue
            rows1.append(dict(p=st[0], m=st[1], g=g, dyn=dyn, r=r))
        rows1.sort(key=lambda x: x["r"]["is_"]["tot"], reverse=True)
        print(f"\n[Stage1 结果从缓存重建] {len(rows1)} 组")

    rows2 = []
    if a.stage in ("2", "both") and rows1:
        # 种子 = IS Top4 ∪ OOS Top4（按 (ST,阈值) 去重）。
        # 只看 IS 会漏掉「IS 平平但样本外稳」的区域（AMD 就是这样：IS 最优全是
        # 小 multiplier 追涨，OOS 却全负；OOS 正值区在 mult 3.5~4.0）。
        # dyn 在 v1 引擎里对结果无影响（两组数值完全相同）→ 按 (ST,阈值) 去重。
        seeds, seen, tags = [], set(), []

        def add(pool, n, tag):
            for x in pool[:n]:
                k = (x["p"], x["m"], x["g"])
                if k in seen:
                    continue
                seen.add(k)
                seeds.append(x)
                tags.append(f"{x['p']}×{x['m']}/阈值{x['g']}({tag} "
                            f"IS{x['r']['is_']['tot']:+.0f} OOS{x['r']['oos']['tot']:+.0f})")

        add(rows1, 4, "IS#")
        add(sorted(rows1, key=lambda z: -z["r"]["oos"]["tot"]), 4, "OOS#")
        print(f"\n【Stage 2】{len(seeds)} 个种子 (ST,阈值) × 出场网格"
              f"（tp1 {len(TP1)} × sl {len(SL)} × trail {len(TRAIL)} = "
              f"{len(TP1)*len(SL)*len(TRAIL)}）", flush=True)
        for t in tags:
            print(f"   种子 {t}")
        t0 = time.time()
        k = 0
        total = len(seeds) * len(TP1) * len(SL) * len(TRAIL)
        for s in seeds:
            for tp1 in TP1:
                for sl in SL:
                    for tr in TRAIL:
                        k += 1
                        r = calc((s["p"], s["m"]), s["g"], s["dyn"], (tp1, sl, tr))
                        if "err" in r or r["is_"]["n"] < MIN_TRADES:
                            continue
                        rows2.append(dict(p=s["p"], m=s["m"], g=s["g"], dyn=s["dyn"],
                                          tp1=tp1, sl=sl, tr=tr, r=r))
                        if k % 20 == 0:
                            print(f"   进度 {k}/{total}  {time.time()-t0:.0f}s", flush=True)
        print(f"   Stage2 有效 {len(rows2)}/{total} 组 耗时 {time.time()-t0:.0f}s", flush=True)
        rows2.sort(key=lambda x: x["r"]["is_"]["tot"], reverse=True)
        print(f"\n── Stage2 IS Top{a.top}（含出场参数）──")
        hdr = (f"{'#':>3} {'ST':>9}{'阈值':>5}{'tp1':>5}{'sl':>5}{'tr':>4}"
               f"{'IS笔':>6}{'IS胜率':>8}{'IS_PF':>7}{'IS_Σ%':>9}{'IS回撤':>8}{'IS_Cal':>7}"
               f"{'OOS笔':>7}{'OOS_Σ%':>9}{'全样本%':>9}{'复利%':>9}")
        print(hdr)
        print("-" * len(hdr))
        for i, x in enumerate(rows2[:a.top], 1):
            ri, ro, ra = x["r"]["is_"], x["r"]["oos"], x["r"]["all"]
            print(f"{i:>3} {x['p']}×{x['m']:<5}{x['g']:>5}{x['tp1']:>5.1f}{x['sl']:>5.1f}"
                  f"{'T' if x['tr'] else 'F':>4}"
                  f"{ri['n']:>6}{ri['wr']:>8.1f}{ri['pf']:>7.2f}{ri['tot']:>+9.2f}"
                  f"{ri['mdd']:>8.2f}{x['r']['cal_is']:>7.2f}"
                  f"{ro['n']:>7}{ro['tot']:>+9.2f}{ra['tot']:>+9.2f}{ra['comp']:>+9.2f}")

        # 稳健交叉：IS Top20 里 OOS 最好的（若与 IS 最优差距大 = 参数不稳定）
        print("\n── 稳健性交叉：IS Top20 里 OOS Σ净% 最好（与 IS 最优差距大 = 参数不稳）──")
        for i, x in enumerate(sorted(rows2[:20], key=lambda z: -z["r"]["oos"]["tot"])[:5], 1):
            print(f"   {i}. ST={x['p']}×{x['m']} 阈值={x['g']} tp1={x['tp1']} "
                  f"sl={x['sl']} trail={'T' if x['tr'] else 'F'}  "
                  f"IS {x['r']['is_']['tot']:+7.2f}%  OOS {x['r']['oos']['tot']:+7.2f}%  "
                  f"全样本 {x['r']['all']['tot']:+7.2f}%")

        # 按 OOS 排序 —— IS/OOS 方向相反时（如 AMD），这张表比 IS Top 更有决策价值
        print(f"\n── Stage2 按 OOS Σ净% 排序 Top{min(a.top,10)}"
              f"（IS 与 OOS 双正 = 可落地候选）──")
        hdr2 = (f"{'#':>3} {'ST':>9}{'阈值':>5}{'tp1':>5}{'sl':>5}{'tr':>4}"
                f"{'OOS笔':>7}{'OOS_Σ%':>9}{'OOS回撤':>9}{'IS笔':>6}{'IS_Σ%':>9}"
                f"{'IS回撤':>8}{'IS_Cal':>7}{'全样本%':>9}{'复利%':>9}{'双正':>5}")
        print(hdr2)
        print("-" * len(hdr2))
        for i, x in enumerate(sorted(rows2, key=lambda z: -z["r"]["oos"]["tot"])[:min(a.top, 10)], 1):
            ri, ro, ra = x["r"]["is_"], x["r"]["oos"], x["r"]["all"]
            ok = "√" if (ri["tot"] > 0 and ro["tot"] > 0) else "×"
            print(f"{i:>3} {x['p']}×{x['m']:<5}{x['g']:>5}{x['tp1']:>5.1f}{x['sl']:>5.1f}"
                  f"{'T' if x['tr'] else 'F':>4}"
                  f"{ro['n']:>7}{ro['tot']:>+9.2f}{ro['mdd']:>9.2f}"
                  f"{ri['n']:>6}{ri['tot']:>+9.2f}{ri['mdd']:>8.2f}{x['r']['cal_is']:>7.2f}"
                  f"{ra['tot']:>+9.2f}{ra['comp']:>+9.2f}{ok:>5}")
        n2 = len(rows2)
        if n2:
            print(f"\n   Stage2 全网格 {n2} 组：OOS 为正 "
                  f"{sum(1 for x in rows2 if x['r']['oos']['tot'] > 0)} 组 ｜ "
                  f"OOS 跑赢买入持有({bh_oos:+.2f}%) "
                  f"{sum(1 for x in rows2 if x['r']['oos']['tot'] > bh_oos)} 组 ｜ "
                  f"全样本为正 {sum(1 for x in rows2 if x['r']['all']['tot'] > 0)} 组")

    # ── Stage 3：src × change_atr（ST 的另两个参数）──────────────
    if rows2 and a.stage in ("2", "both"):
        print("\n【Stage 3】ST 的 src × change_atr 扫描（种子 = IS Top2 ∪ OOS Top2，"
              "其余参数沿用）—— 回答「hl2/RMA 之外要不要换」", flush=True)
        SRCS = ["hl2", "hlc3", "close", "ohlc4"]
        t0 = time.time()
        seeds3, seen3 = [], set()

        def add3(pool, n):
            for x in pool[:n]:
                k = (x["p"], x["m"], x["g"], x["tp1"], x["sl"], x["tr"])
                if k in seen3:
                    continue
                seen3.add(k)
                seeds3.append(x)

        add3(rows2, 2)
        add3(sorted(rows2, key=lambda z: -z["r"]["oos"]["tot"]), 2)
        hdr = (f"{'ST':>9}{'阈值':>5}{'tp1':>5}{'sl':>5}{'tr':>4}{'src':>7}{'ATR':>5}"
               f"{'IS笔':>6}{'IS_Σ%':>9}{'IS_PF':>7}{'IS回撤':>8}"
               f"{'OOS笔':>7}{'OOS_Σ%':>9}{'全样本%':>9}")
        print(hdr)
        print("-" * len(hdr))
        for s in seeds3:
            for src in SRCS:
                for ca in (True, False):
                    r = calc((s["p"], s["m"]), s["g"], s["dyn"],
                             (s["tp1"], s["sl"], s["tr"]), src=src, ca=ca)
                    if "err" in r:
                        continue
                    ri, ro, ra = r["is_"], r["oos"], r["all"]
                    print(f"{s['p']}×{s['m']:<5}{s['g']:>5}{s['tp1']:>5.1f}{s['sl']:>5.1f}"
                          f"{'T' if s['tr'] else 'F':>4}{src:>7}"
                          f"{'RMA' if ca else 'SMA':>5}"
                          f"{ri['n']:>6}{ri['tot']:>+9.2f}{ri['pf']:>7.2f}{ri['mdd']:>8.2f}"
                          f"{ro['n']:>7}{ro['tot']:>+9.2f}{ra['tot']:>+9.2f}", flush=True)
        print(f"   Stage3 耗时 {time.time()-t0:.0f}s")

    fout.close()
    print("\n注：IS 最优 ≠ 应落地。优先选 OOS 同号、且邻域矩阵里「整片为正」的高原参数。")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
