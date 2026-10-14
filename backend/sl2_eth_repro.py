# -*- coding: utf-8 -*-
"""用【首页引擎 backtest/engine.py】复刻用户早跑：eth_config_opt_config.json
(ST 15x2.0, v2 评分 80/40, 无 V3)，按年切片 2025/2026。"""
from __future__ import annotations
import datetime as dt, json, sqlite3
from pathlib import Path
import sys, os
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "backtest"))
import engine

DB = HERE / "candle_data.db"
CFG = HERE / "backtest" / "ETH" / "eth_config_opt_config.json"

def load(tf, sym="ETH-USDT"):
    con = sqlite3.connect(str(DB))
    rs = con.execute("SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? ORDER BY ts",
                     (sym, tf)).fetchall()
    con.close()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]} for r in rs]

def slice_year(cs, y):
    a = int(dt.datetime(y,1,1,tzinfo=dt.UTC).timestamp()*1000)
    b = int(dt.datetime(y+1,1,1,tzinfo=dt.UTC).timestamp()*1000)
    return [c for c in cs if a <= c["ts"] < b]

def resample(candles, hours):
    if hours <= 1: return candles
    step = hours*3600_000; groups={}; order=[]
    for c in candles:
        k=c["ts"]//step; groups.setdefault(k,[]).append(c)
    out=[]
    for k in sorted(groups):
        g=groups[k]
        if len(g)<hours: continue
        out.append({"ts":k*step,"o":g[0]["o"],"h":max(x["h"] for x in g),
                    "l":min(x["l"] for x in g),"c":g[-1]["c"],"vol":sum(x.get("vol") or 0 for x in g)})
    return out

def main():
    C = json.loads(CFG.read_text(encoding="utf-8"))
    sc, pr, er = engine._extract(C)
    c1h = load("1h"); c15 = load("15m")
    c4h_res = resample(c1h, 4)          # 用 1h 重采样补齐 4h（覆盖 2025）
    print(f"[数据] 1h={len(c1h)} 15m(db)={len(c15)} 4h(重采样)={len(c4h_res)}  (db原生4h仅2026-01-31起)")
    for y in (2025, 2026):
        y1h = slice_year(c1h, y)
        candles = {"15m": c15, "1h": y1h, "4h": c4h_res}
        r = engine.run_core("ETH-USDT", sc, pr, er, tf="1h", candles=candles)
        a, i, o, dd = r["all"], r["is"], r["oos"], r["max_dd"]
        print("=" * 70)
        print(f"  {y}窗  1h根数={len(y1h)}")
        print(f"  全样本: 笔数={a['n']}  收益={a['ret_pct']:+.2f}%  PF={a['pf']:.2f}  "
              f"胜率={a['winrate']:.1f}%  盈亏={a['pnl']:+.2f}U")
        print(f"  IS(前70%): 笔数={i['n']}  收益={i['ret_pct']:+.2f}%  PF={i['pf']:.2f} 胜率={i['winrate']:.1f}%")
        print(f"  OOS(后30%): 笔数={o['n']}  收益={o['ret_pct']:+.2f}%  PF={o['pf']:.2f} 胜率={o['winrate']:.1f}%")
        print(f"  最大回撤: {dd:.2f}%")
    print("=" * 70)
    print("对照目标(用户早跑): 2025窗 108/+28.84%/PF1.98/DD18.11%/IS+16.26%/OOS+12.58%")
    print("                   2026窗 41 /+28.43%/PF5.55 /DD6.47% /IS+16.95%/OOS+11.47%")

if __name__ == "__main__":
    main()
