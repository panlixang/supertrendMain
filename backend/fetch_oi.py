# -*- coding: utf-8 -*-
"""B方案：拉取 OKX 真实持仓量(OI)/资金费率历史，替换 vol proxy 做严格低相关因子验证。
注意：此前探测 /api/v3/... 返回 403，本次改用 v5 端点 + User-Agent 重试。
若仍 403 说明当前环境无外网，需在有 OKX 访问权限的环境运行本脚本。
拉到的数据存入 candle_data.db 新表 oi_history / funding_history，供后续 flow 因子接入。
"""
import os
import sys
import json
import sqlite3
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

SYM_MAP = {
    "SNDK-USDT": "SNDK-USDT-SWAP",
    "MU-USDT": "MU-USDT-SWAP",
    "SKHYNIX-USDT": "SKHYNIX-USDT-SWAP",
    "CL-USDT": "CL-USDT-SWAP",
}


def get(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    db = os.path.join(HERE, "candle_data.db")
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS oi_history (
        symbol TEXT, ts INTEGER, oi REAL, oi_ccy REAL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS funding_history (
        symbol TEXT, ts INTEGER, funding_rate REAL)""")

    for sym, inst in SYM_MAP.items():
        print(f"\n=== {sym} ({inst}) ===")
        # 资金费率历史（公开，约最近100条=33天，每8h）
        try:
            fr = get(f"https://www.okx.com/api/v5/public/funding-rate-history?instId={inst}&limit=100")
            data = fr.get("data") or []
            print(f"  funding-rate-history: {len(data)} 条")
            for d in data:
                # d: {instId, fundingRate, fundingTime, ...}
                try:
                    ts = int(d.get("fundingTime"))
                    rate = float(d.get("fundingRate"))
                    cur.execute("INSERT OR REPLACE INTO funding_history VALUES (?,?,?)",
                                (sym, ts, rate))
                except Exception:
                    pass
        except Exception as e:
            print(f"  funding-rate-history ERR: {e}")
        # 当前持仓量（公开快照；历史 OI 在 OKX 多需 rubik 权限，先取当前）
        try:
            oi = get(f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst}")
            data = oi.get("data") or []
            print(f"  open-interest(now): {data}")
            for d in data:
                try:
                    ts = int(d.get("ts"))
                    oival = float(d.get("oi"))
                    oi_ccy = float(d.get("oiCcy") or 0)
                    cur.execute("INSERT OR REPLACE INTO oi_history VALUES (?,?,?,?)",
                                (sym, ts, oival, oi_ccy))
                except Exception:
                    pass
        except Exception as e:
            print(f"  open-interest ERR: {e}")

    con.commit()
    con.close()
    print("\n完成。若以上均 ERR(403/超时)，说明当前环境无 OKX 外网，请在有访问权限的环境运行。")


if __name__ == "__main__":
    main()
