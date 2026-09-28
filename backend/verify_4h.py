import asyncio, numpy as np
from indicators import super_trend
import strategy_learning as sl

SYM = "BTC-USDT"
LIMIT = 40000


async def go():
    ds = await sl.build_dataset(SYM, "4h", limit=LIMIT, label_mode="tpsl",
                                horizon=30, tp_pct=2.5, sl_pct=2.0)
    if ds is None:
        print("build_dataset None")
        return
    rows = ds["rows"]
    bc = sl._CANDLE_CACHE[f"{SYM}|4h|{LIMIT}"]
    print(f"4h 信号数={len(rows)}  蜡烛数={len(bc)}")
    c = [x["c"] for x in bc]
    n = len(c)

    # ---- C-1 AUC 时间外 ----
    print("=== C-1 时间外 AUC (4h) ===")
    for name, f in [("全量", lambda r: True),
                    ("trend_end", lambda r: r["regime6"] == "trend_end"),
                    ("choppy_disorder", lambda r: r["regime6"] == "choppy_disorder")]:
        sub = [r for r in rows if f(r)]
        if len(sub) >= 40:
            m = sl.train_lightgbm(sub, "cls", time_split=True)["metrics"]
            print(f"  {name}: auc={m['auc']:.3f} acc={m['accuracy']:.3f} (n_test={m['n_test']})")
        else:
            print(f"  {name}: n={len(sub)}<40 跳过")

    # ---- C-2 drift ----
    print("=== C-2 漂移检验 (4h) ===")
    st = super_trend([x["o"] for x in bc], [x["h"] for x in bc],
                     [x["l"] for x in bc], c, periods=10, multiplier=3.0,
                     change_atr=True)
    flips = sorted(st["flips"], key=lambda f: f["i"])

    def blk(rs):
        a = np.array(rs)
        me = a.mean()
        se = a.std(ddof=1) / np.sqrt(len(a))
        t = me / se if se > 0 else 0
        print(f"    n={len(a):4d} mean={me*100:+.3f}% t={t:+.2f} correct_dir={np.mean(a>0)*100:.1f}%")

    for H in [4, 12, 24, 48, 96, 192]:
        rb = [c[f["i"] + H] / c[f["i"]] - 1 for f in flips
              if f["type"] == "buy" and f["i"] + H < n]
        rs = [c[f["i"] + H] / c[f["i"]] - 1 for f in flips
              if f["type"] == "sell" and f["i"] + H < n]
        ra = rb + rs
        print(f"  --- 后续{H}根 ---")
        print("    全部:"); blk(ra)
        print("    buy:"); blk(rb)
        print("    sell:"); blk(rs)

    # ---- E 趋势跟踪 ----
    print("=== E 趋势跟踪 (4h, 翻转开仓->下一反向翻转平仓) ===")
    trades = []
    for a, b in zip(flips[:-1], flips[1:]):
        i, j = a["i"], b["i"]
        if j - i < 1:
            continue
        side = 1.0 if a["type"] == "buy" else -1.0
        trades.append(side * (c[j] / c[i] - 1.0))
    tr = np.array(trades)

    def rep(fee):
        net = tr - fee
        eq = np.cumprod(1 + net)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        pf = np.sum(net[net > 0]) / (-np.sum(net[net < 0]) + 1e-9)
        print(f"    单边费={fee/2*100:.3f}%(共{fee*100:.2f}%): 笔数={len(net)} "
              f"总={net.sum()*100:+.1f}% 均={net.mean()*100:+.3f}% "
              f"胜率={np.mean(net>0)*100:.1f}% pf={pf:.2f} 回撤={dd.min()*100:.1f}%")

    print(f"  翻转总数={len(flips)}")
    rep(0.0)
    rep(0.0010)
    rep(0.0015)
    rep(0.0020)


asyncio.run(go())
