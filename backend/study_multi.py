# -*- coding: utf-8 -*-
"""多品种稳健性验证：规则 ER<0.22 & atr>=1.0 是否普适

对每个有完整 15m/1h/4h 数据 + 配置的品种，分别跑：
  基线  = 该品种现有策略配置（extra_gate=None）
  闸门  = 同策略 + 「平坦ER(<0.22) 且 ATR波动率(>=1.0) 禁开仓」
对比 全/IS(0.7)/OOS(0.7)/DD，判定「IS 改善 且 OOS 不塌」是否复现。

若多个不同行情特征的品种都复现 -> 普适规律；若只有 BTC 行 -> 疑似过拟合。

运行：python study_multi.py
"""
import os, sys, json
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "backtest"))

from engine import run_core, _extract, _load_tf   # noqa: E402
from regime import efficiency_ratio, atr_volatility  # noqa: E402

# (展示名, db_symbol, 目录名, config文件名)
TARGETS = [
    ("BTC",     "BTC-USDT",     "BTC",     "btc_config.json"),
    ("ETH",     "ETH-USDT",     "ETH",     "eth_config.json"),
    ("CL",      "CL-USDT",      "CL",      "cl_config.json"),
    ("MU",      "MU-USDT",      "MU",      "mu_config.json"),
    ("NVDA",    "NVDA-USDT",    "NVDA",    "nvda_config.json"),
    ("SNDK",    "SNDK-USDT",    "SNDK",    "sndk_config.json"),
    ("SKHYNIX", "SKHYNIX-USDT", "SKHYNIX", "skhynix_config.json"),
    ("INTC",    "INTC-USDT",    "INTC",    "intc_config.json"),
    ("TSLA",    "TSLA-USDT",    "TSLA",    "tsla_config.json"),
    ("AMD",     "AMD-USDT",     "AMD",     "amd_config.json"),
    ("XAU",     "XAU-USDT",     "XAU",     "xau_config.json"),
]

ER_CAP, VOL_FLOOR = 0.22, 1.0
TF = "1h"


def make_gate(er_cap, vol_floor):
    def g(window, sig):
        e = efficiency_ratio(window)
        a = atr_volatility(window)
        if e is None or a is None:
            return True
        return not (e < er_cap and a >= vol_floor)
    return g


def run_one(db_symbol, cfg_path, gate):
    with open(cfg_path, encoding="utf-8") as f:
        C = json.load(f)
    sc, pr, er = _extract(C)
    candles = {"15m": _load_tf(db_symbol, "15m"),
               "1h": _load_tf(db_symbol, "1h"),
               "4h": _load_tf(db_symbol, "4h")}
    r = run_core(db_symbol, sc, pr, er, tf=TF, candles=candles, extra_gate=gate)
    tr = r["trades"]
    ordered = sorted([t for t in tr if t.get("entry_ts")], key=lambda t: t["entry_ts"])
    k = int(len(ordered) * 0.7)
    INIT = float(sc["margin_usdt"]) * int(sc["leverage"])
    is_r = sum(t["pnl"] for t in ordered[:k]) / INIT * 100 if INIT else 0
    oos_r = sum(t["pnl"] for t in ordered[k:]) / INIT * 100 if INIT else 0
    all_s = r["all"] or {}
    return {"full": all_s.get("ret_pct", 0.0), "is": is_r, "oos": oos_r,
            "dd": r["max_dd"], "n": len(tr), "N": r["n"], "INIT": INIT}


print(f"规则: ER<{ER_CAP} 且 ATR波动率>={VOL_FLOOR} 禁开仓   TF={TF}", flush=True)
print(f"{'品种':8s}{'基全%':>8s}{'基IS':>7s}{'基OOS':>7s}{'基DD':>6s}"
      f"{'闸全%':>8s}{'闸IS':>7s}{'闸OOS':>7s}{'闸DD':>6s}"
      f"{'ISΔ':>7s}{'OOSΔ':>7s}{'n基->闸':>11s}  判定")
print("-" * 122)

passed, total = 0, 0
for name, db_symbol, d, cfg in TARGETS:
    cfg_path = os.path.join(BACKEND, "backtest", d, cfg)
    if not os.path.exists(cfg_path):
        print(f"{name:8s}  (缺配置 {cfg}，跳过)", flush=True)
        continue
    try:
        base = run_one(db_symbol, cfg_path, None)
        gate = run_one(db_symbol, cfg_path, make_gate(ER_CAP, VOL_FLOOR))
    except Exception as e:
        print(f"{name:8s}  出错: {e}", flush=True)
        continue
    total += 1
    is_d = gate["is"] - base["is"]
    oos_d = gate["oos"] - base["oos"]
    ok = (is_d > 3.0) and (oos_d > -8.0)   # IS 明显改善 且 OOS 不塌
    if ok:
        passed += 1
    verdict = "复现YES" if ok else ("IS改善/OOS塌" if is_d > 3 else "无改善")
    print(f"{name:8s}{base['full']:+8.1f}{base['is']:+7.1f}{base['oos']:+7.1f}{base['dd']:6.1f}"
          f"{gate['full']:+8.1f}{gate['is']:+7.1f}{gate['oos']:+7.1f}{gate['dd']:6.1f}"
          f"{is_d:+7.1f}{oos_d:+7.1f}{base['n']:5d}->{gate['n']:<5d}  {verdict}", flush=True)

print(f"\n复现 {passed}/{total} 个品种（判据: IS改善>3pp 且 OOS降幅<8pp）")
print("说明: 各品种用各自策略配置; IS=前70% OOS=后30%; Δ=闸门-基线(百分点)")
print("      SKHYNIX/INTC 等样本短(1h<3000根), 噪声大, 结果仅供参考")
