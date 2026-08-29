"""全品种月度滚动寻优（walk-forward 参数漂移监控）。

对线上全部已配置品种 + QQQ/SKHYNIX 模板，跑「方案A口径」(er_min=0.12, quick=off)
ST 参数寻优，与上一轮最优 + 当前线上配置对比，输出参数漂移报告。

用法:
  python _roll_reopt.py                # 全品种（默认「每月28日 00:00」门控：未到则跳过）
  python _roll_reopt.py --force        # 忽略门控，强制重跑
  python _roll_reopt.py --symbols BTC,QQQ   # 只跑指定品种（逗号分隔 instId 或简称）

输出:
  - 控制台：每个品种 Top5 + 漂移对照汇总表
  - _roll_reopt_history.json：历史最优存档（供下次对比）
  - 自动 POST 最近一轮结果到线上 /api/trade/reopt-results，供前端「最新寻优」展示
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from _btc_score_opt import score_only_cfg, score_row
from _live_cfg_backtest import LIVE_URL, _get, exit_rules
from _qqq_reopt import QQQ_SYM
from _skhynix_reopt import SKHYNIX_SYM

GATE_TF = "1h"
DATA_DIR = Path(__file__).parent / "_live_data"
HIST_PATH = Path(__file__).parent / "_roll_reopt_history.json"
ER_MIN = 0.12
REOPT_DAY = 28          # 每月 28 日 00:00 触发寻优
MIN_TRADES = 8

# 统一网格：覆盖线上全部品种当前参数（MU 22×3.0、SNDK 23×2.5 都能落进网格）
PERIODS = list(range(5, 25))                              # 5..24
MULTS = [round(x / 10, 1) for x in range(15, 61, 5)]      # 1.5..6.0 步0.5
SCORES = list(range(35, 80, 5))                           # 35..75

# 未上线但已拉数据的模板品种
TEMPLATES = {QQQ_SYM["symbol"]: QQQ_SYM, SKHYNIX_SYM["symbol"]: SKHYNIX_SYM}


def data_path(symbol: str) -> Path:
    return DATA_DIR / (symbol.split("-")[0] + ".json")


def load_cbtf(symbol: str) -> dict | None:
    p = data_path(symbol)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {GATE_TF: data}
    return {k: v for k, v in data.items() if isinstance(v, list) and k in ("15m", "1h", "4h", "1d")}


def collect_symbols(args) -> list[dict]:
    """线上已配置品种（有本地数据）+ 模板品种（有本地数据）。"""
    live = _get(LIVE_URL)
    syms = [copy.deepcopy(s) for s in live["symbols"] if "USDT-SWAP" in s.get("symbol", "")]
    seen = {s["symbol"] for s in syms}
    for sym in TEMPLATES.values():
        if sym["symbol"] not in seen:
            syms.append(copy.deepcopy(sym))
    if args.symbols:
        want = {s.strip().upper() for s in args.symbols.split(",")}
        syms = [s for s in syms
                if any(w in s["symbol"] or w == s["symbol"].split("-")[0] for w in want)]
    # 只保留本地有数据的
    return [s for s in syms if data_path(s["symbol"]).exists()]


def run_grid(sym: dict, cbtf: dict, verbose: bool = True) -> list[dict]:
    candles = cbtf[GATE_TF]
    cur_p, cur_m = sym["params"]["periods"], sym["params"]["multiplier"]
    cur_thr = sym.get("scoring_full_threshold", 45)
    rows = []
    total = len(PERIODS) * len(MULTS) * len(SCORES)
    n = 0
    t0 = time.time()
    for pe in PERIODS:
        p = {**sym["params"], "periods": pe, "multiplier": float(MULTS[0])}
        for m in MULTS:
            p = {**sym["params"], "periods": pe, "multiplier": float(m)}
            cfg = score_only_cfg(GATE_TF)
            cfg.er_min = ER_MIN
            cfg.er_weak_min = 0.12
            cfg.quick_enabled = False
            cfg.use_dynamic_threshold = sym.get("use_dynamic_threshold", True)
            cfg.er_hide_below = sym.get("er_hide_below", 0.0)
            for s in SCORES:
                n += 1
                r = run_backtest(
                    candles, p,
                    init_cash=100.0, fee_rate=0.0005, allow_short=True,
                    exit_rules=exit_rules(sym),
                    sizing="fixed", margin_usdt=sym["margin_usdt"],
                    leverage=sym["leverage"],
                    live_gate=cfg, gate_tf=GATE_TF, candles_by_tf=cbtf,
                    score_only_gate=True, min_total_score=float(s),
                )
                if "error" in r:
                    continue
                pnl = round(r["final"] - 100, 2)
                rows.append({
                    "periods": pe,
                    "multiplier": float(m),
                    "min_score_100": s,
                    "pnl_u": pnl,
                    "max_dd_pct": r["max_dd_pct"],
                    "trades": r["trades"],
                    "win_rate": r["win_rate"],
                    "profit_factor": r["profit_factor"],
                    "score": round(score_row(pnl, r["max_dd_pct"], r["trades"]), 3),
                    "is_current_st": pe == cur_p and float(m) == float(cur_m) and s == cur_thr,
                })
            if verbose and n % 300 == 0:
                print(f"    {n}/{total} …", flush=True)
    if verbose:
        print(f"    done {len(rows)} combos in {time.time()-t0:.0f}s", flush=True)
    ok = [r for r in rows if r["trades"] >= MIN_TRADES]
    ok.sort(key=lambda x: (x["score"], x["pnl_u"]), reverse=True)
    return ok


def fmt(r: dict | None) -> str:
    if not r:
        return "—"
    pf = "inf" if r["profit_factor"] is None else f"{r['profit_factor']:.2f}"
    return (f"{r['periods']}×{r['multiplier']} s{r['min_score_100']} "
            f"{r['pnl_u']}U dd{r['max_dd_pct']}% {r['trades']}笔 wr{r['win_rate']}% PF{pf}")


def drift_note(prev: dict | None, cur: dict | None) -> str:
    if not prev or not cur:
        return "首次"
    d = []
    if prev["periods"] != cur["periods"]:
        d.append(f"periods {prev['periods']}→{cur['periods']}")
    if prev["multiplier"] != cur["multiplier"]:
        d.append(f"mult {prev['multiplier']}→{cur['multiplier']}")
    if prev["min_score_100"] != cur["min_score_100"]:
        d.append(f"score {prev['min_score_100']}→{cur['min_score_100']}")
    if not d:
        return "参数稳定"
    return "漂移: " + ", ".join(d)


def _month_ts(dt: datetime, day: int) -> int:
    """dt 所在月份 day 日 00:00 的时间戳(ms)。"""
    return int(dt.replace(day=day, hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def next_run_ts(dt: datetime | None = None) -> int:
    """下一个「每月 {REOPT_DAY} 日 00:00」时间戳(ms)。

    本月该日已过（含今天已是该日零点后）→ 返回下月该日。
    """
    now = dt or datetime.now()
    cur = _month_ts(now, REOPT_DAY)
    if cur > int(now.timestamp() * 1000):
        return cur
    nxt_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
    return _month_ts(nxt_month, REOPT_DAY)


def upload_reopt(payload: dict) -> dict:
    """上传最近一轮寻优结果到线上，供前端「最新寻优」展示。"""
    base = LIVE_URL.rsplit("/api/", 1)[0]
    url = base + "/api/trade/reopt-results"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "supertrend-bt/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略「每月28日」门控")
    ap.add_argument("--symbols", default="", help="只跑指定品种，逗号分隔")
    args = ap.parse_args()

    # 每月 {REOPT_DAY} 日 00:00 门控：未到则跳过（自动化每天触发也没关系）
    hist = {}
    if HIST_PATH.exists():
        with open(HIST_PATH, "r", encoding="utf-8") as f:
            hist = json.load(f)
    nxt_ts = hist.get("next_run_ts") or next_run_ts()
    if not args.force and int(time.time() * 1000) < nxt_ts:
        nxt_dt = datetime.fromtimestamp(nxt_ts / 1000).strftime("%Y-%m-%d %H:%M")
        print(f"[skip] 未到寻优时间（下次 {nxt_dt}），本次跳过。如需提前跑加 --force", flush=True)
        return 0

    syms = collect_symbols(args)
    if not syms:
        print("[error] 没有可跑的品种（线上无配置且本地无模板数据）", flush=True)
        return 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"===== {today} 全品种滚动寻优（er_min={ER_MIN}, quick=off）=====", flush=True)
    print(f"网格: periods {PERIODS[0]}..{PERIODS[-1]} × mult {MULTS[0]}..{MULTS[-1]} × score {SCORES[0]}..{SCORES[-1]}",
          flush=True)
    print(f"品种: {', '.join(s['symbol'] for s in syms)}", flush=True)

    prev_best = (hist.get("runs") or [{}])[-1].get("best", {}) if hist.get("runs") else {}
    cur_best: dict[str, dict] = {}
    run_rec = {"date": today, "bars": {}, "best": cur_best}
    t_all = time.time()

    for i, sym in enumerate(syms, 1):
        symbol = sym["symbol"]
        cbtf = load_cbtf(symbol)
        if not cbtf or not cbtf.get(GATE_TF):
            print(f"[{i}/{len(syms)}] {symbol}: 无数据，跳过", flush=True)
            continue
        candles = cbtf[GATE_TF]
        print(f"\n[{i}/{len(syms)}] {symbol}  {len(candles)} 根", flush=True)
        ok = run_grid(sym, cbtf)
        run_rec["bars"][symbol] = len(candles)
        if not ok:
            print("    无满足样本(≥8笔)的组合", flush=True)
            continue
        best = ok[0]
        cur_best[symbol] = best
        cur = next((r for r in ok if r.get("is_current_st")), None)
        prev = prev_best.get(symbol)
        print(f"    Top5:", flush=True)
        for r in ok[:5]:
            mark = "  ← 线上当前" if r.get("is_current_st") else ""
            print(f"      {fmt(r)}{mark}", flush=True)
        print(f"    线上当前: {fmt(cur)}", flush=True)
        print(f"    上次最优: {fmt(prev)}", flush=True)
        print(f"    本次最优: {fmt(best)}  →  {drift_note(prev, best)}", flush=True)

    # 汇总表
    print("\n" + "=" * 100, flush=True)
    print(f"{'品种':<16}{'数据':>7}  {'上次最优':<34}{'本次最优':<34}{'漂移判断'}", flush=True)
    print("-" * 100, flush=True)
    for sym in syms:
        symbol = sym["symbol"]
        if symbol not in cur_best:
            continue
        prev = prev_best.get(symbol)
        best = cur_best[symbol]
        name = symbol.replace("-USDT-SWAP", "")
        print(f"{name:<16}{run_rec['bars'][symbol]:>6}根  {fmt(prev):<34}{fmt(best):<34}{drift_note(prev, best)}",
              flush=True)
    print(f"\n总耗时 {time.time()-t_all:.0f}s", flush=True)
    print("说明: 参数连续 2~3 轮稳定 = 可信；连续乱跳 = 噪声拟合，该收敛仓位而非加仓。", flush=True)

    # 存档（只保留最近 12 轮）+ 记录下次运行时间（每月28日 00:00）
    runs = hist.get("runs", []) if isinstance(hist.get("runs"), list) else []
    runs.append(run_rec)
    runs = runs[-12:]
    hist["last_run_ts"] = int(time.time() * 1000)
    hist["last_run_date"] = today
    hist["next_run_ts"] = next_run_ts()
    hist["runs"] = runs
    with open(HIST_PATH, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {HIST_PATH}", flush=True)

    # 上传最近一轮结果到线上，供前端「最新寻优」展示（手动确认后才更新参数）
    up_fields = ("periods", "multiplier", "min_score_100", "pnl_u",
                 "max_dd_pct", "trades", "win_rate", "profit_factor")
    up = {
        "date": today,
        "bars": run_rec["bars"],
        "best": {sym: {k: r[k] for k in up_fields} for sym, r in cur_best.items()},
        "prev": {sym: {k: prev_best[sym].get(k) for k in up_fields}
                 for sym in cur_best if sym in prev_best},
        "drift": {sym: drift_note(prev_best.get(sym), cur_best[sym]) for sym in cur_best},
    }
    r_up = upload_reopt(up)
    if r_up.get("ok"):
        print("已上传寻优结果到线上（前端挂单页「最新寻优」可见）", flush=True)
    else:
        print(f"[warn] 上传寻优结果失败: {r_up.get('error')}（本地已存档，不影响下次）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
