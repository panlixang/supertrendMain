# -*- coding: utf-8 -*-
"""
整合 43 / 47 两台服务线上品种的：当前真实参数 + 2026窗口统一口径回测数据。

口径与寻优一致：1h K线截断到 2026-01-01 起，margin×leverage 为名义本金。
只回测，不写入任何线上配置。

用法: python integrate_live_all.py
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
OUT = os.path.join(BACKEND, "_bt2026_data")


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


def build(tag, s):
    """返回 (rec, msg)"""
    sym = s["symbol"]
    ds = dbname(sym)
    cd = candles(ds)
    if "1h" not in cd:
        return None, f"[跳过] {tag} {sym}: 本地无 {ds} 1h 数据"
    try:
        sc, pr, er = _extract(s)
        r = run_core(ds, sc, pr, er, tf="1h", candles=cd)
    except Exception as ex:
        return None, f"[失败] {tag} {sym}: {ex}"
    a, i, o = r["all"], r["is"], r["oos"]
    if not a:
        return None, f"[无交易] {tag} {sym}"
    p = s.get("params", {})
    exr = s.get("exit_rules", {})
    opf = None
    if o:
        opf = "inf" if o["pf"] == float("inf") else round(o["pf"], 2)
    return {
        "srv": tag, "symbol": sym.replace("-USDT-SWAP", ""),
        "enabled": bool(s.get("enabled")),
        "notional": f'{s.get("margin_usdt")}x{s.get("leverage")}',
        "st": f'{p.get("periods")}x{p.get("multiplier")} {p.get("src")}',
        "gate": f'{s.get("scoring_full_threshold")}/{s.get("scoring_alert_threshold")}',
        "tp1": f'{exr.get("tp1_pct")}/{exr.get("tp1_ratio")}',
        "tp2": f'{exr.get("tp2_pct")}/{exr.get("tp2_ratio")}',
        "sl": f'{exr.get("sl_mode")}{exr.get("sl_pct")} tr={exr.get("trail_with_st")}',
        "er": f'{s.get("er_trend")}/{s.get("er_min")}',
        "n": a["n"], "ret": round(a["ret_pct"], 2),
        "pf": "inf" if a["pf"] == float("inf") else round(a["pf"], 2),
        "wr": round(a["winrate"], 1),
        "is": round(i["ret_pct"], 2) if i else None,
        "oos": round(o["ret_pct"], 2) if o else None,
        "oos_pf": opf,
        "dd": round(r["max_dd"], 2),
        "eff": round(a["ret_pct"] / max(a["n"], 1), 3),
        "miss15": "15m" not in cd,
    }, None


def fmt(v, w=8, d=2):
    if v is None:
        return " " * w
    if isinstance(v, str):
        return f"{v:>{w}}"
    if d == 3:
        return f"{v:>{w}.3f}"
    return f"{v:>{w}.{d}f}"


def table(rows):
    print(f'{"服":<3}{"品种":<8}{"开":<3}{"名义":<8}{"ST":<11}{"闸门":<8}'
          f'{"n":>4}{"全%":>8}{"PF":>7}{"胜率":>7}{"IS%":>8}{"OOS%":>8}{"OOSPF":>7}{"DD%":>7}{"每笔%":>8}')
    print("-" * 112)
    for x in rows:
        print(f'{x["srv"]:<3}{x["symbol"]:<8}{"Y" if x["enabled"] else "N":<3}'
              f'{x["notional"]:<8}{x["st"]:<11}{x["gate"]:<8}'
              f'{fmt(x["n"], 4, 0)}{fmt(x["ret"])}{fmt(x["pf"], 7)}{fmt(x["wr"], 7)}'
              f'{fmt(x["is"])}{fmt(x["oos"])}{fmt(x["oos_pf"], 7)}{fmt(x["dd"], 7)}'
              f'{fmt(x["eff"], 8, 3)}')


def main():
    all_rows, logs = [], []
    for tag, host in HOSTS:
        print(f"\n########## 服务 {tag}  ({host}) ##########")
        try:
            live = load(host)
        except Exception as ex:
            print(f"[错误] 拉取失败: {ex}")
            continue
        print(f"线上挂载 {len(live)} 个品种")
        rows = []
        for s in live:
            rec, msg = build(tag, s)
            if msg:
                logs.append(msg)
                continue
            rows.append(rec)
        rows.sort(key=lambda x: -x["ret"])
        table(rows)
        all_rows.extend(rows)

    print("\n" + "=" * 112)
    print("参数明细（服/品种: ST | 闸门 | tp1 | tp2 | sl | ER）")
    print("=" * 112)
    for x in sorted(all_rows, key=lambda x: (x["srv"], -x["ret"])):
        tail = "  [缺15m]" if x["miss15"] else ""
        print(f'  {x["srv"]} {x["symbol"]:<8} ST {x["st"]:<11} 闸门 {x["gate"]:<8} '
              f'tp1 {x["tp1"]:<9} tp2 {x["tp2"]:<9} sl {x["sl"]:<18} ER {x["er"]:<10}{tail}')

    if logs:
        print("\n跳过/失败记录:")
        for m in logs:
            print("  " + m)

    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "live_integrated_all.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2)
    print(f"\n[已保存] {p}   共 {len(all_rows)} 个品种")

    print("\n按全样本收益排序（跨服务器）:")
    for x in sorted(all_rows, key=lambda x: -x["ret"]):
        print(f'  {x["srv"]} {x["symbol"]:<8} 全={fmt(x["ret"],7)} PF={fmt(x["pf"],6)} '
              f'OOS={fmt(x["oos"],7)} DD={fmt(x["dd"],6)} n={x["n"]:<4} 每笔={fmt(x["eff"],7,3)}')


if __name__ == "__main__":
    main()
