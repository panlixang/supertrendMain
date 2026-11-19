# -*- coding: utf-8 -*-
"""
按统一仓位口径 100U × 10x = 名义 1000U，汇总 43 / 47 线上品种 2026 窗口回测收益。

- 收益百分比与名义无关，这里统一换算成绝对 U 数，便于横向比较。
- 手续费按名义收：taker 0.05%×2 = 0.1% → 1000U 名义每笔 1.0U
                   maker 0.02%×2 = 0.04% → 1000U 名义每笔 0.4U
- 只回测，不写入任何线上配置。

用法: python live_pnl_2026.py
"""

import os
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "backtest"))
from backtest.engine import run_core, _extract, _load_tf

HOSTS = [
    ("47", os.environ.get("HOST47", "http://47.84.106.154:5174")),
    ("43", os.environ.get("HOST43", "http://43.108.10.84:5174")),
]
TS_2026 = 1767225600000  # 2026-01-01 UTC

MARGIN = 100.0      # U
LEV = 10
NOTIONAL = MARGIN * LEV        # 1000U
FEE_TAKER_PCT = 0.1            # 占名义 0.1%  (0.05% * 2)
FEE_MAKER_PCT = 0.04           # 占名义 0.04% (0.02% * 2)
OUT = os.path.join(BACKEND, "_bt2026_data")


def allocate(rows, min_pct=5.0, max_pct=30.0):
    """按【风险调整后净收益】分配仓位百分比。

    score_i = max(净U_i, 0) / DD_i      —— 每承受 1% 回撤换来的净收益
    比例 = score_i / Σscore，并把单品种限制在 [min_pct, max_pct]% 之间后重新归一化
    （迭代 clamp + 归一化直至收敛，保证合计 100%）。
    """
    sc = [max(x["net_t"], 0.0) / max(x["dd"], 1.0) for x in rows]
    tot = sum(sc)
    if tot <= 0:
        return [100.0 / len(rows)] * len(rows)
    w = [v / tot * 100.0 for v in sc]
    for _ in range(50):
        w = [min(max(v, min_pct), max_pct) for v in w]
        s = sum(w)
        w = [v / s * 100.0 for v in w]
    return w


def load(host):
    with urllib.request.urlopen(host + "/api/trade/symbols", timeout=25) as r:
        return json.loads(r.read().decode())["symbols"]


def dbname(sym):
    return sym.replace("-USDT-SWAP", "-USDT")


def candles(ds):
    out = {}
    for tf in ("15m", "1h", "4h"):
        rows = [c for c in _load_tf(ds, tf) if c["ts"] >= TS_2026]
        if rows:
            out[tf] = rows
    return out


def main():
    print(f"口径: {MARGIN:.0f}U × {LEV}x = 名义 {NOTIONAL:.0f}U"
          f"   |  taker 费 {NOTIONAL*FEE_TAKER_PCT/100:.1f}U/笔"
          f"   |  maker 费 {NOTIONAL*FEE_MAKER_PCT/100:.1f}U/笔")
    print("=" * 118)

    all_rows, logs = [], []
    for tag, host in HOSTS:
        print(f"\n########## 服务 {tag}  ({host}) ##########")
        try:
            live = load(host)
        except Exception as ex:
            print(f"[错误] 拉取失败: {ex}")
            continue

        rows = []
        for s in live:
            sym, ds = s["symbol"], dbname(s["symbol"])
            cd = candles(ds)
            if "1h" not in cd:
                logs.append(f"[跳过] {tag} {sym}: 本地无 1h 数据")
                continue
            try:
                sc, pr, er = _extract(s)
                r = run_core(ds, sc, pr, er, tf="1h", candles=cd)
            except Exception as ex:
                logs.append(f"[失败] {tag} {sym}: {ex}")
                continue
            a, i, o = r["all"], r["is"], r["oos"]
            if not a:
                logs.append(f"[无交易] {tag} {sym}: 线上参数下 0 笔")
                continue

            n, ret = a["n"], a["ret_pct"]
            gross = NOTIONAL * ret / 100.0
            fee_t = n * NOTIONAL * FEE_TAKER_PCT / 100.0
            fee_m = n * NOTIONAL * FEE_MAKER_PCT / 100.0
            p = s.get("params", {})
            rows.append({
                "srv": tag, "sym": sym.replace("-USDT-SWAP", ""),
                "st": f'{p.get("periods")}x{p.get("multiplier")} {p.get("src")}',
                "n": n, "ret": ret,
                "gross": gross, "fee_t": fee_t, "net_t": gross - fee_t,
                "net_pct_t": ret - n * FEE_TAKER_PCT,
                "fee_m": fee_m, "net_m": gross - fee_m,
                "dd": r["max_dd"],
                "mg": float(s.get("margin_usdt") or 0.0),
                "is": i["ret_pct"] if i else None,
                "oos": o["ret_pct"] if o else None,
            })

        rows.sort(key=lambda x: -x["net_t"])
        for x, w in zip(rows, allocate(rows)):
            x["alloc"] = w
        print(f'{"品种":<9}{"ST":<12}{"n":>4}{"全%":>8}{"毛U":>9}{"费U":>8}{"净U(taker)":>11}'
              f'{"净%":>8}{"净U(maker)":>11}{"DD%":>7}{"建议仓位%":>10}{"建议U":>9}')
        print("-" * 128)
        tot_mg = sum(x["mg"] for x in rows)
        for x in rows:
            print(f'{x["sym"]:<9}{x["st"]:<12}{x["n"]:>4}{x["ret"]:>8.2f}'
                  f'{x["gross"]:>9.1f}{x["fee_t"]:>8.1f}{x["net_t"]:>11.1f}'
                  f'{x["net_pct_t"]:>8.2f}{x["net_m"]:>11.1f}{x["dd"]:>7.2f}'
                  f'{x["alloc"]:>10.1f}{tot_mg*x["alloc"]/100:>9.1f}')
        sub_g = sum(x["gross"] for x in rows)
        sub_ft = sum(x["fee_t"] for x in rows)
        sub_fm = sum(x["fee_m"] for x in rows)
        print("-" * 128)
        print(f'{"小计":<9}{"":<12}{sum(x["n"] for x in rows):>4}{"":>8}'
              f'{sub_g:>9.1f}{sub_ft:>8.1f}{sub_g-sub_ft:>11.1f}{"":>8}{sub_g-sub_fm:>11.1f}'
              f'{sum(x["alloc"] for x in rows):>10.1f}{tot_mg:>9.1f}')
        print(f'  服务 {tag}: 毛 {sub_g:.1f}U / taker费 {sub_ft:.1f}U / '
              f'净 {sub_g-sub_ft:.1f}U   (maker 净 {sub_g-sub_fm:.1f}U)')
        print(f'  建议仓位依据: 净U / DD% (风险调整收益)，单品种限 5%~30%；'
              f'该台现有总保证金 {tot_mg:.0f}U，"建议U" 为按此总仓重新分配的结果')
        all_rows.extend(rows)

    tg = sum(x["gross"] for x in all_rows)
    tt = sum(x["fee_t"] for x in all_rows)
    tm = sum(x["fee_m"] for x in all_rows)
    tn = sum(x["n"] for x in all_rows)
    print("\n" + "=" * 118)
    print(f"总计（{len(all_rows)} 个品种，{tn} 笔，统一 {MARGIN:.0f}U×{LEV}x=名义{NOTIONAL:.0f}U）:")
    print(f"  毛收益        {tg:>10.1f} U")
    print(f"  手续费(taker) {tt:>10.1f} U   ({tn} 笔 × {NOTIONAL*FEE_TAKER_PCT/100:.1f}U)")
    print(f"  净收益(taker) {tg-tt:>10.1f} U   ({(tg-tt)/NOTIONAL*100:>6.2f}% 名义)")
    print(f"  净收益(maker) {tg-tm:>10.1f} U   ({(tg-tm)/NOTIONAL*100:>6.2f}% 名义)")
    print("=" * 118)

    if logs:
        print("\n跳过/无交易记录:")
        for m in logs:
            print("  " + m)

    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "live_pnl_2026.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2)
    print(f"\n[已保存] {p}")


if __name__ == "__main__":
    main()
