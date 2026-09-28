import asyncio, datetime as dt
from history import fetch_candles
from indicators import super_trend

SYM, BASE_TF, LIMIT = "BTC-USDT", "4h", 40000


async def main():
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, fetch_candles, BASE_TF, LIMIT, SYM)
    bc = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c} for c in raw]
    st = super_trend([x["o"] for x in bc], [x["h"] for x in bc],
                     [x["l"] for x in bc], [x["c"] for x in bc],
                     periods=10, multiplier=3.0, change_atr=True)
    flips = sorted(st["flips"], key=lambda f: f["i"])
    t0 = bc[flips[0]["i"]]["ts"] / 1000
    t1 = bc[flips[-1]["i"]]["ts"] / 1000
    years = (t1 - t0) / (365.25 * 86400)
    n = len(flips)
    # 持仓根数 = 到下一翻转
    hold = [flips[i + 1]["i"] - flips[i]["i"] for i in range(n - 1)]
    import numpy as np
    hold = np.array(hold)
    span_days = (bc[-1]["ts"] - bc[0]["ts"]) / 86400 / 1000
    occ = hold.sum() * 4 / 24 / span_days * 100  # 资金占用率(近似)
    print(f"信号数={n}  首末信号跨度={years:.2f}年")
    print(f"年笔数 ≈ {n/years:.1f} 笔/年")
    print(f"平均持仓 = {hold.mean():.1f} 根4h = {hold.mean()*4/24:.2f} 天")
    print(f"中位持仓 = {np.median(hold):.1f} 根4h = {np.median(hold)*4/24:.2f} 天")
    print(f"P90持仓  = {np.percentile(hold,90):.1f} 根4h = {np.percentile(hold,90)*4/24:.2f} 天")
    print(f"估算资金占用率 ≈ {occ:.0f}% (常年持仓)")


asyncio.run(main())
