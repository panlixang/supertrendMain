# -*- coding: utf-8 -*-
"""
多资产历史 K 线抓取 —— 灌进 backend/candle_data.db
==============================================================================

为什么需要它
------------
前面的实验已经证明：**BTC 单资产上，任何参数组合都做不出统计上可判定的结论。**
最好的格子只有 45~70 笔交易、有效广度 1~5 笔，95% 置信区间全部跨过 0。
瓶颈不是模型，是**样本量（广度）**。而广度只能靠「更多独立资产」来补。

本脚本负责把 OKX 的历史 K 线抓下来存进 `candle_data.db`（与项目现有表结构一致），
之后 `sl2_portfolio.py` 就能在同一套前向口径下做多资产组合评估。

⚠️ 本脚本需要外网访问（www.okx.com）。如果你的环境像某些沙箱一样禁止直连，
   请在本机自己跑一遍；跑完我就能在本地做分析。

用法
----
    cd backend
    # 抓 8 个主流币的 4h + 1d（1d 历史短也别落下，做长周期验证要用）
    python sl2_fetch_multi.py --symbols BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT,XRP-USDT,DOGE-USDT,LINK-USDT,AVAX-USDT --tfs 4H,1D

    # 只补某个周期
    python sl2_fetch_multi.py --symbols SOL-USDT --tfs 1H

    # 看看库里现在有什么
    python sl2_fetch_multi.py --list

注意
----
- OKX 的 `history-candles` 用 `after=<ts>` 向更早翻页，每页最多 100 根。
- 单页缺失时不重试太多，避免被限频；默认每次请求间隔 0.25 秒。
- 已存在的 (symbol, tf) 会**增量补齐**：从库里最早的一根继续往更早抓，不会重复写。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DB_PATH = Path("candle_data.db")
BASE = "https://www.okx.com"
PAGE_LIMIT = 100


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.execute("""create table if not exists candles (
        symbol text not null, tf text not null, ts integer not null,
        o real, h real, l real, c real, vol real,
        primary key (symbol, tf, ts))""")
    return con


def get(url: str, proxy: str | None, timeout: float = 20.0) -> dict:
    if proxy == "none":
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    elif proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        opener = urllib.request.build_opener()
    req = urllib.request.Request(url, headers={"User-Agent": "supertrendMain/fetch"})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def oldest_ts(con, symbol: str, tf: str):
    row = con.execute("select min(ts) from candles where symbol=? and tf=?",
                      (symbol, tf)).fetchone()
    return row[0] if row and row[0] else None


def fetch(symbol: str, tf: str, target: int, proxy: str | None) -> list[list]:
    """向更早翻页抓取，直到没有数据或达到 target 根。"""
    out: list[list] = []
    cur = oldest_ts(_db(), symbol, tf)          # 从库里最早的一根继续往更早
    while len(out) < target:
        q = {"instId": symbol, "bar": tf, "limit": str(PAGE_LIMIT)}
        if cur:
            q["after"] = str(cur)
        url = f"{BASE}/api/v5/market/history-candles?" + urllib.parse.urlencode(q)
        try:
            doc = get(url, proxy)
        except Exception as e:
            print(f"    [网络错误] {e}")
            break
        data = doc.get("data") or []
        if not data:
            break
        batch = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
                 for r in data]
        out.extend(batch)
        nxt = min(b[0] for b in batch)
        if cur is not None and nxt >= cur:
            break
        cur = nxt
        print(f"    ... 已取 {len(out)} 根，最早 {time.strftime('%Y-%m-%d', time.gmtime(cur/1000))}")
        time.sleep(0.25)
    return out


def save(con, symbol: str, tf: str, rows: list[list]) -> int:
    if not rows:
        return 0
    before = con.execute("select count(*) from candles where symbol=? and tf=?",
                         (symbol, tf)).fetchone()[0]
    con.executemany(
        "insert or replace into candles(symbol,tf,ts,o,h,l,c,vol) values (?,?,?,?,?,?,?,?)",
        [(symbol, tf, r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows])
    con.commit()
    after = con.execute("select count(*) from candles where symbol=? and tf=?",
                        (symbol, tf)).fetchone()[0]
    return after - before


def cmd_list():
    con = _db()
    rows = con.execute("""select symbol, tf, count(*), min(ts), max(ts) from candles
                          group by symbol, tf order by symbol, tf""").fetchall()
    if not rows:
        print("(空)")
        return
    print(f"{'symbol':<14}{'tf':<5}{'bars':>8}  range")
    for s, tf, n, lo, hi in rows:
        print(f"{s:<14}{tf:<5}{n:>8}  "
              f"{time.strftime('%Y-%m-%d', time.gmtime(lo/1000))} -> "
              f"{time.strftime('%Y-%m-%d', time.gmtime(hi/1000))}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="抓多资产历史 K 线到 candle_data.db")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--tfs", default="4H,1D")
    ap.add_argument("--target", type=int, default=100000,
                    help="每个 (symbol,tf) 的目标根数上限")
    ap.add_argument("--proxy", default="", help="http://127.0.0.1:port；填 none 表示直连（忽略系统代理）")
    ap.add_argument("--list", action="store_true", help="只列出库里现有数据")
    a = ap.parse_args(argv)

    if a.list:
        cmd_list()
        return 0
    if not a.symbols:
        print("请用 --symbols 指定资产，或用 --list 查看现有数据")
        return 1

    con = _db()
    for sym in [s.strip() for s in a.symbols.split(",") if s.strip()]:
        for tf in [t.strip() for t in a.tfs.split(",") if t.strip()]:
            print(f"\n[{sym} {tf}] 开始")
            rows = fetch(sym, tf, a.target, (a.proxy or None))
            added = save(con, sym, tf, rows)
            total = con.execute("select count(*) from candles where symbol=? and tf=?",
                                (sym, tf)).fetchone()[0]
            print(f"[{sym} {tf}] 新增 {added} 根，库内合计 {total} 根")
    print()
    cmd_list()
    return 0


if __name__ == "__main__":
    sys.exit(main())
