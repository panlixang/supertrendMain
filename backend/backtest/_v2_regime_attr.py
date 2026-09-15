# -*- coding: utf-8 -*-
"""V1 vs V2 市场状态归因实验（Regime Attribution）。

设计原则（严格无前视）：
- V1 / V2 策略参数完全不动；只新增“每笔交易产生时记录 Market Regime”这一种信息。
- 状态判定只用信号 entry 那一根及其之前的数据（candles[:i+1]），绝不看未来。
- 第一版只做 4 个最基础状态：STRONG_TREND / WEAK_TREND / RANGE / TRANSITION。
- 输出 4 张表：
    表1 总体        Symbol | V1 E/PF/WR | V2 E/PF/WR
    表2 状态归因    Symbol | Regime | V1 E | V2 E | dE | Winner
    表3 跨品种      Regime | MU ETH SPCX SNDK BTC | Majority
    表4 跨时间      Regime | Train dE | OOS dE | Stable
- 鲁棒性：去掉每(品种,引擎)最大盈利 5 笔后重算，看结论是否翻转。

V1 = 线上原样（score_engine 回落 v1）；V2 = score_engine=v2 + 候选阈值
（MU40/ETH44/SPCX44/SNDK56/BTC36）。全程只在历史数据上回测。
"""
from __future__ import annotations
import bisect
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators import ma                                  # noqa: E402
from regime import adx_latest, efficiency_ratio            # noqa: E402
from _v2_threshold_scan import (fetch_symbols, slice_cbtf, run,
                                trade_cfg, exit_rules, SYMS, DATA)

TS = lambda y, m, d, h=0, mi=0: int(
    datetime(y, m, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)
TRAIN_HI = TS(2026, 6, 30, 23, 59)
CAND = {"MU": 40.0, "ETH": 44.0, "SPCX": 44.0, "SNDK": 56.0, "BTC": 36.0}
REGIMES = ["STRONG_TREND", "WEAK_TREND", "RANGE", "TRANSITION"]
ENGINES = [("v1", None), ("v2", "cand")]


def last_valid(lst):
    for v in reversed(lst):
        if v is not None:
            return v
    return None


def classify_regime(slice_candles):
    """只用 slice 内数据（无前视）判定 4 状态。数据不足返回 UNKNOWN。"""
    if len(slice_candles) < 120:
        return "UNKNOWN"
    adx = adx_latest(slice_candles, 14)
    if adx is None:
        return "UNKNOWN"
    closes = [c["c"] for c in slice_candles]
    e20 = last_valid(ma(closes, 20, "EMA"))
    e60 = last_valid(ma(closes, 60, "EMA"))
    e120 = last_valid(ma(closes, 120, "EMA"))
    if e20 is None or e60 is None or e120 is None:
        return "UNKNOWN"
    e20s = [v for v in ma(closes, 20, "EMA") if v is not None]
    if len(e20s) < 2:
        return "UNKNOWN"
    j = max(0, len(e20s) - 6)
    slope = (e20s[-1] - e20s[j]) / e20s[j] * 100.0
    er = efficiency_ratio(slice_candles, 20) or 0.0
    struct = (e20 > e60 > e120 and slope > 0) or (e20 < e60 < e120 and slope < 0)
    if adx >= 25:
        return "STRONG_TREND" if struct else "TRANSITION"
    if 18 <= adx < 25:
        return "WEAK_TREND" if struct else "TRANSITION"
    return "RANGE"


def group_metrics(pnls):
    if not pnls:
        return {"T": 0, "E": 0.0, "PF": 0.0, "WR": 0.0}
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    wr = len(wins) / len(pnls) * 100.0
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(losses) / len(losses) if losses else 0.0
    e = (wr / 100.0) * aw - (1 - wr / 100.0) * abs(al)
    pf = (sum(wins) / abs(sum(losses))) if losses else (sum(wins) if wins else 0.0)
    return {"T": len(pnls), "E": round(e, 3), "PF": round(pf, 2),
            "WR": round(wr, 1)}


def agg(records, **filt):
    pnls = [r["pnl"] for r in records
            if all(r.get(k) == v for k, v in filt.items())]
    return group_metrics(pnls)


def de_map(records):
    """返回 {(sym,regime): {v1:metrics, v2:metrics, dE, winner, T1, T2}}。"""
    out = {}
    for sym in SYMS:
        for reg in REGIMES:
            v1 = agg(records, sym=sym, engine="v1", regime=reg)
            v2 = agg(records, sym=sym, engine="v2", regime=reg)
            if v1["T"] == 0 and v2["T"] == 0:
                continue
            de = round(v2["E"] - v1["E"], 3)
            out[(sym, reg)] = {"v1": v1, "v2": v2, "dE": de,
                               "winner": "V2" if de > 0 else "V1",
                               "T1": v1["T"], "T2": v2["T"]}
    return out


def main():
    t0 = time.time()
    syms = fetch_symbols()
    sym_map = {x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): x
               for x in syms}

    records = []  # {sym, engine, regime, period, pnl}
    per_se_eps = {}  # (sym,engine) -> list pnl for robustness drop-top-5
    for name in SYMS:
        if name not in sym_map:
            print(f"{name}: 不在线上配置", flush=True)
            continue
        s = sym_map[name]
        ex = exit_rules(s)
        gate_tf = s["allow_tfs"][0]
        cache = os.path.join(DATA, f"{name}.json")
        if not os.path.exists(cache):
            print(f"{name}: 缓存缺失", flush=True)
            continue
        cbtf_full = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                     if isinstance(v, list) and v}
        candles = cbtf_full.get(gate_tf)
        ts_list = [c["ts"] for c in candles]

        for engine, kind in ENGINES:
            cfg = trade_cfg(s)
            cfg = replace(cfg, score_engine=engine)
            if kind == "cand":
                t = CAND[name]
                cfg = replace(cfg, scoring_full_threshold=t,
                              scoring_half_threshold=t, scoring_alert_threshold=t)
            r = run(s, cbtf_full, cfg, ex)
            tl = r.get("trades_list") or []
            for tr in tl:
                et = tr.get("entry_ts")
                if et is None:
                    continue
                pos = bisect.bisect_left(ts_list, et)
                if pos < len(ts_list) and ts_list[pos] == et:
                    idx = pos
                elif pos > 0 and ts_list[pos - 1] == et:
                    idx = pos - 1
                else:
                    continue
                reg = classify_regime(candles[:idx + 1])
                period = "train" if et <= TRAIN_HI else "oos"
                pnl = tr.get("pnl_pct", 0.0)
                rec = {"sym": name, "engine": engine, "regime": reg,
                       "period": period, "pnl": pnl}
                records.append(rec)
                per_se_eps.setdefault((name, engine), []).append(pnl)
        print(f"{name}: 已记录 {sum(1 for r in records if r['sym']==name)} 笔",
              flush=True)

    # ---- 表1 总体 ----
    print("\n=== 表1 总体（全历史 03~09）===")
    print(f"{'SYM':5s} {'V1_E':>6s} {'V1_PF':>6s} {'V1_WR':>6s} {'V1_T':>4s} "
          f"{'V2_E':>6s} {'V2_PF':>6s} {'V2_WR':>6s} {'V2_T':>4s}", flush=True)
    for sym in SYMS:
        v1 = agg(records, sym=sym, engine="v1")
        v2 = agg(records, sym=sym, engine="v2")
        print(f"{sym:5s} {v1['E']:>6.2f} {v1['PF']:>6.2f} {v1['WR']:>6.1f} "
              f"{v1['T']:>4d} {v2['E']:>6.2f} {v2['PF']:>6.2f} {v2['WR']:>6.1f} "
              f"{v2['T']:>4d}", flush=True)

    # ---- 表2 状态归因 ----
    print("\n=== 表2 状态归因（Symbol | Regime | V1 E | V2 E | dE | Winner）===")
    dm = de_map(records)
    for sym in SYMS:
        for reg in REGIMES:
            k = (sym, reg)
            if k not in dm:
                continue
            d = dm[k]
            print(f"{sym:5s} {reg:13s} V1E={d['v1']['E']:>6.2f} "
                  f"V2E={d['v2']['E']:>6.2f} dE={d['dE']:>6.2f} "
                  f"-> {d['winner']} (T1={d['T1']},T2={d['T2']})", flush=True)

    # ---- 表3 跨品种 Majority ----
    print("\n=== 表3 跨品种 Majority（Regime 下各品种 Winner，取多数）===")
    for reg in REGIMES:
        row = {}
        for sym in SYMS:
            d = dm.get((sym, reg))
            row[sym] = d["winner"] if d else "-"
        vc = sum(1 for v in row.values() if v == "V2")
        mc = sum(1 for v in row.values() if v == "V1")
        majority = "V2" if vc > mc else ("V1" if mc > vc else "TIE")
        print(f"{reg:13s} " + " ".join(f"{s}={row[s][:1]}" for s in SYMS)
              + f"  | V2={vc} V1={mc} -> {majority}", flush=True)

    # ---- 表4 跨时间 Stable ----
    print("\n=== 表4 跨时间（Regime | Train dE | OOS dE | Stable）===")
    for reg in REGIMES:
        tr_de = round(agg(records, engine="v2", regime=reg, period="train")["E"]
                      - agg(records, engine="v1", regime=reg, period="train")["E"], 3)
        oo_de = round(agg(records, engine="v2", regime=reg, period="oos")["E"]
                      - agg(records, engine="v1", regime=reg, period="oos")["E"], 3)
        stable = (tr_de > 0 and oo_de > 0) or (tr_de < 0 and oo_de < 0)
        print(f"{reg:13s} Train dE={tr_de:>6.2f}  OOS dE={oo_de:>6.2f}  "
              f"-> {'STABLE' if stable else 'MIXED'}", flush=True)

    # ---- 鲁棒性：去掉每(品种,引擎)最大盈利 5 笔 ----
    print("\n=== 鲁棒性：去掉每(品种,引擎)最大盈利 5 笔后重算 dE ===")
    robust = []
    for r in records:
        eps = per_se_eps.get((r["sym"], r["engine"]), [])
        # 复制列表按 pnl 降序，标记前 5 大盈利为剔除
    # 用 (sym,engine) 维度剔除
    drop_ids = set()
    for key, lst in per_se_eps.items():
        order = sorted(range(len(lst)), key=lambda i: lst[i], reverse=True)
        for i in order[:5]:
            drop_ids.add((key, i))
    # 重建带本地索引的 records
    counter = {}
    for r in records:
        k = (r["sym"], r["engine"])
        idx = counter.get(k, 0)
        counter[k] = idx + 1
        if (k, idx) in drop_ids:
            continue
        robust.append(r)
    dm_r = de_map(robust)
    flips = 0
    for k, d in dm.items():
        dr = dm_r.get(k)
        if dr and dr["winner"] != d["winner"]:
            flips += 1
            print(f"  FLIP {k[0]} {k[1]}: {d['winner']} -> {dr['winner']}", flush=True)
    print(f"  翻转数: {flips}/{len(dm)} （越少越稳）", flush=True)

    # ---- 验收计数 ----
    print("\n=== 验收：每状态样本量 ===")
    for reg in REGIMES:
        tot = sum(agg(records, engine=e, regime=reg)["T"] for e in ("v1", "v2"))
        per_sym = {s: agg(records, sym=s, regime=reg)["T"]
                   for s in SYMS}
        ok = tot >= 100 or all(v >= 50 for v in per_sym.values())
        print(f"{reg:13s} 总样本={tot:>4d} 每品种={per_sym} "
              f"-> {'达标' if ok else '不足'}", flush=True)

    out = {
        "records_count": len(records),
        "table1": {s: {"V1": agg(records, sym=s, engine="v1"),
                       "V2": agg(records, sym=s, engine="v2")} for s in SYMS},
        "table2": {f"{s}|{r}": dm.get((s, r)) for s in SYMS for r in REGIMES},
        "regime_majority": {r: {s: dm.get((s, r), {}).get("winner", "-") for s in SYMS}
                            for r in REGIMES},
        "crosstime": {r: {
            "train_dE": round(agg(records, engine="v2", regime=r, period="train")["E"]
                              - agg(records, engine="v1", regime=r, period="train")["E"], 3),
            "oos_dE": round(agg(records, engine="v2", regime=r, period="oos")["E"]
                            - agg(records, engine="v1", regime=r, period="oos")["E"], 3),
        } for r in REGIMES},
        "robust_flips": flips,
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_v2_regime_attr.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nWrote {path} | 耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
