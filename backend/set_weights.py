# -*- coding: utf-8 -*-
"""按指定权重分配各品种保证金（自动归一化 + OKX 最小张数下界校验）。

用法: python set_weights.py [--apply]
不带 --apply 只做试算，不写入。
"""
import os
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HOSTS = {"43": "http://43.108.10.84:5174", "47": "http://47.84.106.154:5174"}
OKX = "https://www.okx.com/api/v5"

# 用户指定权重（百分比，合计可不为 100，脚本内部归一化）
WEIGHTS = {
    "47": {"SNDK": 25, "MU": 25, "CL": 25, "NVDA": 15, "SKHYNIX": 15},
    "43": {"INTC": 25, "BTC": 25, "AMD": 25, "TSLA": 15, "ETH": 15},
}

UA = {"User-Agent": "supertrend-monitor/1.0"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def http(host, method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(host + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode())


_specs = {}


def spec_of(inst):
    """取合约规格；返回 (ct_val*ct_mult, min_sz, lot_sz)"""
    if not _specs:
        d = get(f"{OKX}/public/instruments?instType=SWAP")
        for r in d.get("data", []):
            _specs[r["instId"]] = r
    s = _specs.get(inst)
    if not s:
        return None
    ct = float(s.get("ctVal") or 1) * float(s.get("ctMult") or 1)
    return ct, float(s.get("minSz") or 1), float(s.get("lotSz") or 1)


def last_px(inst):
    d = get(f"{OKX}/market/ticker?instId={inst}")
    row = (d.get("data") or [{}])[0]
    return float(row.get("last") or 0)


def main():
    apply = "--apply" in sys.argv
    print(f"模式: {'APPLY(真实写入)' if apply else 'DRY-RUN(仅试算)'}")
    print("=" * 104)

    for tag, wmap in WEIGHTS.items():
        host = HOSTS[tag]
        m = {s["symbol"]: s for s in http(host, "GET", "/api/trade/symbols")["symbols"]}
        syms = {n: f"{n}-USDT-SWAP" for n in wmap}
        missing = [n for n, s in syms.items() if s not in m]
        if missing:
            print(f"[{tag}] 缺失品种 {missing}")
            continue

        pool = sum(m[s]["margin_usdt"] for s in syms.values())
        wsum = sum(wmap.values())
        factor = pool / wsum
        print(f"\n[{tag}] 现有保证金池 {pool:.1f}U   权重合计 {wsum}%  ->  每 1% = {factor:.4f}U")

        plan, notes = {}, []
        for n, w in wmap.items():
            sym = syms[n]
            cur = m[sym]
            lev = cur["leverage"]
            target = round(w * factor, 2)
            info = spec_of(sym)
            px = last_px(sym)
            min_margin = 0.0
            if info and px > 0:
                ct, min_sz, lot_sz = info
                min_notional = min_sz * ct * px          # 最小一张对应的名义价值
                min_margin = min_notional / lev
                if target < min_margin:
                    notes.append(f"{n}: 目标 {target:.2f}U < 最小 {min_margin:.2f}U -> 抬到 {min_margin:.2f}U")
                    target = round(min_margin + 0.01, 2)
            plan[sym] = target
            print(f"    {n:<8} w={w:>2}%  {cur['margin_usdt']:>6.1f}U -> {target:>6.2f}U"
                  f"   (现价 {px:.4g} | 最小保证金 {min_margin:.2f}U @ {lev}x)")

        for nline in notes:
            print(f"    [下界修正] {nline}")

        if not apply:
            continue

        for sym, target in plan.items():
            r = http(host, "POST", "/api/trade/symbols", {"symbol": sym, "margin_usdt": target})
            got = None
            if r.get("ok"):
                for s in r.get("symbols", []):
                    if s["symbol"] == sym:
                        got = s["margin_usdt"]
            print(f"    [{'OK' if got == target else 'FAILED'}] {sym} margin -> {got}"
                  + ("" if got == target else f"  {r.get('error')}"))

        mm = {s["symbol"]: s for s in http(host, "GET", "/api/trade/symbols")["symbols"]}
        tot = sum(mm[s]["margin_usdt"] for s in syms.values())
        print(f"\n    [回读校验] 目标总 {pool:.1f}U -> 实际总 {tot:.2f}U")
        for n in wmap:
            sym = syms[n]
            v = mm[sym]["margin_usdt"]
            print(f"      {n:<8} {v:>6.2f}U   实际占比 {v/tot*100:>5.2f}%")


if __name__ == "__main__":
    main()
