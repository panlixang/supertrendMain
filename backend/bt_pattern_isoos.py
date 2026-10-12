# -*- coding: utf-8 -*-
"""形态识别页（/pattern，43页）ST+V3 策略 —— INTC / AMD 回测 + IS/OOS 验证。

ST 固定 10×3.0 / change_atr=True（bt_pattern_page.py 页面忠实逻辑）。
信号：1h ST 翻转 + 4h 形态方向对齐 + V3 过滤(Strategy C)。
出场：position.ExitRules 默认档 TP1 1.5% 平70% + 保本 + 跟随ST轨道 + 2%兜底。
数据：本地 _bt2026_data/{INTC,AMD}_USDT_SWAP.json（1h + 4h）。
"""
import sys, os, json, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_pattern_page as P

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "_bt2026_data")
SYMS = {"INTC": "INTC_USDT_SWAP.json", "AMD": "AMD_USDT_SWAP.json"}
WIN_START = dt.datetime(2026, 2, 26, tzinfo=dt.timezone.utc)
WIN_END = dt.datetime(2026, 10, 11, tzinfo=dt.timezone.utc)


def load_local(fname):
    d = json.load(open(os.path.join(DATA, fname), encoding="utf-8"))
    base = [{"ts": c["ts"], "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "vol": c["vol"]}
            for c in d["1h"]]
    h4 = [{"ts": c["ts"], "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "vol": c["vol"]}
          for c in d["4h"]]
    return base, h4


def ms(x): return int(x.timestamp() * 1000)


def pf(tr):
    w = sum(t["pnl"] for t in tr if t["pnl"] > 0)
    l = -sum(t["pnl"] for t in tr if t["pnl"] <= 0)
    return w / l if l else float("inf")


def report(label, tr):
    m = P.metrics(tr)
    n = m["n"]
    print(f"  {label}: 笔={n} 胜率={m['wr']:.1f}% PF={pf(tr):.2f} "
          f"均盈/均亏={m['payoff']:.2f} 收益={m['tot']:+.2f}U({m['ret']:+.2f}%) "
          f"回撤={m['max_dd']:.2f}%(={m['max_dd']/100*P.NOTIONAL:.2f}U) 单笔={m['avg']:+.3f}%")


def run_one(name, fname):
    # 形态识别页 ST 固定 10×3.0
    P.ST_PERIODS, P.ST_MULT = 10, 3.0
    base, h4 = load_local(fname)
    s, e = ms(WIN_START), ms(WIN_END)
    sigs, opens, highs, lows, closes, up, dn, flip = P.build_signals(base, h4)
    win = [x for x in sigs if s <= x["ts"] < e]
    c = [x for x in win if x["v3_execute"]]          # Strategy C：V3 过滤
    print(f"\n########## {name}  形态识别页 ST10×3.0+V3  窗口信号{len(win)}/V3放行{len(c)} ##########")
    tr = P.backtest(c, highs, lows, closes, up, dn, flip)
    report("全样本 02-26~10-11（对照你贴的 +58.79U/63笔/65.1%/PF2.09/回撤10.59U）", tr)

    c.sort(key=lambda x: x["ts"])
    cut = c[int(len(c) * 0.7)]["ts"]
    cd = dt.datetime.fromtimestamp(cut / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    is_s = [x for x in c if x["ts"] < cut]
    oos_s = [x for x in c if x["ts"] >= cut]
    print(f"  IS/OOS 切点={cd}（前70% IS / 后30% OOS）")
    report(f"IS(~{cd})", P.backtest(is_s, highs, lows, closes, up, dn, flip))
    report(f"OOS({cd}~)", P.backtest(oos_s, highs, lows, closes, up, dn, flip))


if __name__ == "__main__":
    for name, f in SYMS.items():
        run_one(name, f)
