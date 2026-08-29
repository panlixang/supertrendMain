"""当前线上配置 vs 图片旧参数（当前代码版本，忽略旧过滤条件）。

本地统一数据：_live_data/{BTC,ETH,MU,SPCX,SNDK}.json（1h）。
旧参数来自图片：
  ETH  13×3  ER0.12
  SPCX  9×2  ER0.12
  BTC  15×5  ER0.15
  MU    7×3  ER0.12
  SNDK 13×3  ER0.15
当前线上配置动态拉取。
"""
import json, urllib.request
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest import run_backtest
from _btc_score_opt import score_only_cfg
from _live_cfg_backtest import exit_rules, ts_fmt

LIVE_URL = "http://43.108.10.84:5174/api/trade/symbols"
GATE_TF = "1h"
DATA_DIR = Path(__file__).parent / "_live_data"

OLD_PARAMS = {
    "ETH-USDT-SWAP":  {"periods": 13, "multiplier": 3.0, "er_min": 0.12, "quick": False},
    "SPCX-USDT-SWAP": {"periods": 9,  "multiplier": 2.0, "er_min": 0.12, "quick": False},
    "BTC-USDT-SWAP":  {"periods": 15, "multiplier": 5.0, "er_min": 0.15, "quick": False},
    "MU-USDT-SWAP":   {"periods": 7,  "multiplier": 3.0, "er_min": 0.12, "quick": False},
    "SNDK-USDT-SWAP": {"periods": 13, "multiplier": 3.0, "er_min": 0.15, "quick": True},
}


def _get(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"}), timeout=30) as r:
        return json.loads(r.read())


def load_cbtf(symbol: str) -> dict:
    tag = symbol.split("-")[0]
    with open(DATA_DIR / f"{tag}.json", "r") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {GATE_TF: data}
    return {k: v for k, v in data.items() if isinstance(v, list) and k in ("15m", "1h", "4h", "1d")}


def run(sym: dict, params: dict, er_min: float, quick: bool, thr: float) -> dict:
    cbtf = load_cbtf(sym["symbol"])
    cfg = score_only_cfg(GATE_TF)
    cfg.er_min = er_min
    cfg.er_weak_min = 0.12
    cfg.quick_enabled = quick
    cfg.use_dynamic_threshold = sym.get("use_dynamic_threshold", True)
    cfg.er_hide_below = sym.get("er_hide_below", 0.0)
    return run_backtest(
        cbtf[GATE_TF],
        params,
        init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=exit_rules(sym),
        sizing="fixed", margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
        live_gate=cfg, gate_tf=GATE_TF, candles_by_tf=cbtf,
        score_only_gate=True, min_total_score=float(thr),
    )


def line(label: str, r: dict):
    if "error" in r:
        print(f"  {label:28s} ERROR: {r['error']}", flush=True)
        return
    pnl = r["final"] - r["init_cash"]
    extra = f" quick={r.get('quick_trades', 0)}" if r.get("quick_trades") else ""
    print(f"  {label:28s} pnl={pnl:7.2f}U  trades={r['trades']:3d}{extra}  "
          f"wr={r['win_rate']:5.1f}%  PF={r['profit_factor']:5.2f}  dd={r['max_dd_pct']:5.1f}%", flush=True)


def main():
    symbols = {s["symbol"]: s for s in _get(LIVE_URL)["symbols"] if s["symbol"].endswith("-USDT-SWAP")}
    for sym_name in ["ETH-USDT-SWAP", "SPCX-USDT-SWAP", "BTC-USDT-SWAP", "MU-USDT-SWAP", "SNDK-USDT-SWAP"]:
        sym = symbols[sym_name]
        cur_p = sym["params"]
        cur_thr = sym["scoring_full_threshold"]
        print(f"\n=== {sym_name} ===", flush=True)
        print(f"  当前线上: {cur_p['periods']}×{cur_p['multiplier']}  ER{sym['er_min']}  score={cur_thr}  quick={sym['quick_enabled']}", flush=True)

        # 当前配置
        r_cur = run(sym, cur_p, sym["er_min"], sym["quick_enabled"], cur_thr)
        line("当前线上", r_cur)

        # 旧参数 + 当前分数
        old = OLD_PARAMS[sym_name]
        old_p = {**cur_p, "periods": old["periods"], "multiplier": old["multiplier"]}
        r_old_same_thr = run(sym, old_p, old["er_min"], old["quick"], cur_thr)
        line(f"旧参数 {old['periods']}×{old['multiplier']} score={cur_thr}", r_old_same_thr)

        # 旧参数扫分
        print(f"  --- 旧参数扫分 ---", flush=True)
        best = None
        for thr in range(30, 75, 5):
            r = run(sym, old_p, old["er_min"], old["quick"], thr)
            line(f"score={thr}", r)
            if best is None or (r["final"]-r["init_cash"]) > (best[1]["final"]-best[1]["init_cash"]):
                best = (thr, r)
        print(f"  旧参数最佳 score={best[0]}  pnl={best[1]['final']-best[1]['init_cash']:.2f}U", flush=True)


if __name__ == "__main__":
    main()
