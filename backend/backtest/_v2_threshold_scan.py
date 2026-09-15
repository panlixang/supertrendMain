# -*- coding: utf-8 -*-
"""V2 阈值扫描 + Walk-Forward / OOS 验证。

设计：
- 闸门：total_score >= scoring_half_threshold 才下单。
  扫描用 full=half=alert=t 的干净二元闸门（线上配置本就 full==half，
  档位差为空），聚焦“入场阈值”本身；逐笔 Expectancy 不受影响。
- 训练窗 2026-03~06 选阈值；验证窗 2026-07~09 完全不调参做 OOS 校验。
- 选“稳定区间”而非单点最优点：train/OOS 两边均 E>0 且 PF>1 且样本够
  的连续阈值段，取中位为 t*。
- 最终对比 V1 / V2旧阈值 / V2新阈值(t*) 在全周期 + OOS 的 Expectancy。

输出 _v2_threshold_scan.json（含完整扫描曲线与各品种三方对比）。
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.request
from dataclasses import replace
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402
from _live_cfg_backtest import exit_rules, trade_cfg  # noqa: E402

SYMS = ["MU", "ETH", "SPCX", "SNDK", "BTC"]
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
LIVE = [
    "http://43.108.10.84:5174/api/trade/symbols",
    "http://47.84.106.154:5174/api/trade/symbols",
]

TRAIN_LO = int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
TRAIN_HI = int(datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
OOS_LO = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
OOS_HI = int(datetime(2026, 12, 31, tzinfo=timezone.utc).timestamp() * 1000)

THRESHOLDS = list(range(28, 84, 4))  # 28..80


def _get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_symbols():
    last = None
    for url in LIVE:
        try:
            d = _get_json(url)
            if d and d.get("symbols"):
                return d["symbols"]
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"无法获取线上配置: {last}")


def slice_cbtf(cbtf, lo, hi):
    return {tf: [c for c in arr if lo <= c["ts"] <= hi]
            for tf, arr in cbtf.items() if isinstance(arr, list) and arr}


def metrics(r):
    if "error" in r:
        return {"t": 0, "pnl": 0.0, "e": 0.0, "pf": 0.0, "dd": 0.0, "wr": 0.0,
                "error": r["error"]}
    wr = r.get("win_rate") or 0.0
    aw = r.get("avg_win") or 0.0
    al = r.get("avg_loss") or 0.0
    exp = ((wr / 100.0) * aw - (1 - wr / 100.0) * abs(al)) if r.get("trades") else 0.0
    pf = r.get("profit_factor") or 0.0
    return {
        "t": r.get("trades", 0),
        "pnl": round((r.get("final", 100) - 100), 2),
        "e": round(exp, 3),
        "pf": round(pf, 2),
        "dd": round(r.get("max_dd_pct", 0), 1),
        "wr": round(wr, 1),
    }


def run(s, cbtf, cfg, ex):
    gate_tf = s["allow_tfs"][0]
    candles = cbtf.get(gate_tf)
    if not candles:
        return {"error": "no candles"}
    common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                  exit_rules=ex, sizing="fixed",
                  margin_usdt=s["margin_usdt"], leverage=s["leverage"],
                  gate_tf=gate_tf, candles_by_tf=cbtf)
    return run_backtest(candles, s["params"], live_gate=cfg, **common)


def max_contiguous(vals, step=4):
    vals = sorted(vals)
    best = cur = []
    for v in vals:
        if not cur or v - cur[-1] <= step:
            cur.append(v)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [v]
    if len(cur) > len(best):
        best = cur
    return best


def main():
    t0 = time.time()
    syms = fetch_symbols()
    sym_map = {x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): x
               for x in syms}
    out = {}

    for name in SYMS:
        if name not in sym_map:
            print(f"!! {name} 不在线上配置，跳过", flush=True)
            continue
        s = sym_map[name]
        p = s["params"]
        ex = exit_rules(s)
        gate_tf = s["allow_tfs"][0]
        cache_path = os.path.join(DATA, f"{name}.json")
        if not os.path.exists(cache_path):
            print(f"!! {name} 缓存缺失 {cache_path}", flush=True)
            continue
        with open(cache_path, encoding="utf-8") as f:
            cbtf_full = {k: v for k, v in json.load(f).items()
                         if isinstance(v, list) and v}
        candles_full = cbtf_full.get(gate_tf)
        if not candles_full or len(candles_full) < 300:
            print(f"!! {name} 数据不足 ({gate_tf} n={len(candles_full or [])})", flush=True)
            continue

        print(f"\n=== {name} {gate_tf} 数据 "
              f"[{candles_full[0]['ts']}~{candles_full[-1]['ts']}] ===", flush=True)

        cbtf_train = slice_cbtf(cbtf_full, TRAIN_LO, TRAIN_HI)
        cbtf_oos = slice_cbtf(cbtf_full, OOS_LO, OOS_HI)
        tr_lo = cbtf_train[gate_tf][0]["ts"] if cbtf_train.get(gate_tf) else None
        oos_lo = cbtf_oos[gate_tf][0]["ts"] if cbtf_oos.get(gate_tf) else None

        scan = []
        for t in THRESHOLDS:
            cfg = replace(trade_cfg(s), score_engine="v2",
                          scoring_full_threshold=float(t),
                          scoring_half_threshold=float(t),
                          scoring_alert_threshold=float(t))
            m_tr = metrics(run(s, cbtf_train, cfg, ex))
            m_oo = metrics(run(s, cbtf_oos, cfg, ex))
            scan.append({"t": t, "train": m_tr, "oos": m_oo})
            print(f"  t={t:>3}: TR E={m_tr['e']:>6.2f} T={m_tr['t']:>3} "
                  f"PF={m_tr['pf']:>5.2f} | OOS E={m_oo['e']:>6.2f} "
                  f"T={m_oo['t']:>3} PF={m_oo['pf']:>5.2f}", flush=True)

        # ---- 稳定区间选择 ----
        stable = [row for row in scan
                  if row["train"]["t"] >= 8 and row["train"]["e"] > 0
                  and row["train"]["pf"] > 1
                  and row["oos"]["t"] >= 4 and row["oos"]["e"] > 0
                  and row["oos"]["pf"] > 1]
        if stable:
            stable_ts = [row["t"] for row in stable]
            seg = max_contiguous(stable_ts)
            t_star = seg[len(seg) // 2]
            stable_range = [seg[0], seg[-1]]
        else:
            cand = [row for row in scan if row["oos"]["e"] > 0 and row["train"]["t"] >= 8]
            if cand:
                t_star = max(cand, key=lambda r: r["train"]["e"])["t"]
            else:
                t_star = max(scan, key=lambda r: r["train"]["e"])["t"]
            stable_range = [t_star, t_star]
        print(f"  >> 稳定区间={stable_range}  t*={t_star}", flush=True)

        # ---- 最终三方对比（全周期 + OOS）----
        def mk(engine, thr=None):
            kw = {}
            if thr is not None:
                kw = dict(scoring_full_threshold=float(thr),
                          scoring_half_threshold=float(thr),
                          scoring_alert_threshold=float(thr))
            return replace(trade_cfg(s), score_engine=engine, **kw)

        v1_full = metrics(run(s, cbtf_full, mk(""), ex))
        v2old_full = metrics(run(s, cbtf_full, mk("v2"), ex))
        v2new_full = metrics(run(s, cbtf_full, mk("v2", t_star), ex))
        v2new_oos = metrics(run(s, cbtf_oos, mk("v2", t_star), ex))
        v1_oos = metrics(run(s, cbtf_oos, mk(""), ex))
        v2old_oos = metrics(run(s, cbtf_oos, mk("v2"), ex))

        out[name] = {
            "windows": {"train": tr_lo, "oos": oos_lo},
            "stable_range": stable_range,
            "t_star": t_star,
            "scan": scan,
            "compare": {
                "V1_full": v1_full, "V2old_full": v2old_full,
                "V2new_full": v2new_full,
                "V1_oos": v1_oos, "V2old_oos": v2old_oos, "V2new_oos": v2new_oos,
            },
        }
        print(f"  V1 full  E={v1_full['e']:>6.2f} T={v1_full['t']:>3} | "
              f"V2old full  E={v2old_full['e']:>6.2f} T={v2old_full['t']:>3} | "
              f"V2new(t*={t_star}) full E={v2new_full['e']:>6.2f} T={v2new_full['t']:>3}",
              flush=True)
        print(f"  OOS: V1 E={v1_oos['e']:>6.2f} | V2old E={v2old_oos['e']:>6.2f} | "
              f"V2new E={v2new_oos['e']:>6.2f} T={v2new_oos['t']:>3}", flush=True)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_v2_threshold_scan.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out_path} | 总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
