# -*- coding: utf-8 -*-
"""2026-02-26~10-11 双口径汇总（43/47 当前配置 · 本地 K 线 · 固定 100U×1x）。

① 首页口径(live_gate=评分闸门)：与线上首页实盘同口径；
② 形态识别页口径(v3_filter=True)：同 ST/同出场，仅把入场闸门换成 V3 过滤。

输出侧栏对比两张表的 Σ收益% / 最大回撤U，方便直接挑。
"""
import json
import os
import sys
import datetime as dt
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from backtest_engine import run_backtest
from backtest_mu_home import trade_cfg, exit_rules

LIVE43 = "http://43.108.10.84:5174/api/trade/symbols"
LIVE47 = "http://47.84.106.154:5174/api/trade/symbols"

PAGE_BY_SYM = {
    "NVDA": "43", "CL": "43", "MU": "43", "SNDK": "43", "SKHYNIX": "43",
    "TSLA": "43", "BTC": "43", "ETH": "43", "INTC": "43",
    "AMD": "47", "XAU": "47"
}
SYMS = list(PAGE_BY_SYM.keys())

MARGIN = 100.0
LEV = 1
INIT = 10_000.0
FEE = 0.0005
TFS = ["15m", "1h", "4h", "1d"]
WIN_START = dt.datetime(2026, 2, 26, tzinfo=dt.timezone.utc)
WIN_END = dt.datetime(2026, 10, 11, tzinfo=dt.timezone.utc)
DATA_DIR = os.path.join(HERE, "_bt2026_data")


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def load_live():
    merged = {}
    for url in (LIVE43, LIVE47):
        try:
            d = _get(url)
        except Exception as e:
            print(f"!! {url} 拉取失败: {e}")
            continue
        for s in d["symbols"]:
            base = s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
            merged[base] = s
    return merged


def load_local(sym):
    p = os.path.join(DATA_DIR, f"{sym}_USDT_SWAP.json")
    if not os.path.exists(p):
        return None
    raw = json.load(open(p, encoding="utf-8"))
    return {tf: [{"ts": c["ts"], "o": c["o"], "h": c["h"], "l": c["l"],
                  "c": c["c"], "vol": c.get("vol", 0)} for c in (raw.get(tf) or [])]
            for tf in TFS}


def stats(r):
    tr = r.get("trades_list") or []
    n = len(tr)
    if not n:
        return dict(n=0, payoff=0.0, wr=0.0, tot=0.0, comp=0.0, pf=0.0,
                    mdd_u=0.0, aw=0.0, al=0.0)
    notional = MARGIN * LEV
    pcts = [t["pnl"] / notional * 100 for t in tr]
    wins = [p for p in pcts if p > 0]
    loss = [p for p in pcts if p <= 0]
    aw = sum(wins) / len(wins) if wins else 0.0
    al = (sum(loss) / len(loss)) if loss else 0.0
    payoff = (aw / abs(al)) if al else float("inf")
    comp = 1.0
    for p in pcts:
        comp *= (1 + p / 100)
    pf = (sum(wins) / abs(sum(loss))) if loss else float("inf")
    cum = [0.0]
    for p in pcts:
        cum.append(cum[-1] + p)
    peak = 0.0
    mdd = 0.0
    for v in cum:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > mdd:
            mdd = dd
    return dict(n=n, payoff=payoff, wr=len(wins) / n * 100,
                tot=sum(pcts), comp=(comp - 1) * 100, pf=pf, mdd_u=mdd,
                aw=aw, al=al)


def run_one(name, sym, mode):
    """mode='home'(live_gate) 或 'v3'(v3_filter)。"""
    gate_tf = sym.get("allow_tfs", ["1h"])[0]
    lo = int(WIN_START.timestamp() * 1000)
    hi = int(WIN_END.timestamp() * 1000)
    cbtf = load_local(name)
    if not cbtf:
        return dict(err="无本地 K 线")
    seg = [c for c in cbtf.get(gate_tf, []) if lo <= c["ts"] <= hi]
    if len(seg) < 100:
        return dict(err=f"{gate_tf} 窗口内 K 线不足 ({len(seg)})")
    cbtf_seg = {tf: [c for c in arr if lo <= c["ts"] <= hi]
                for tf, arr in cbtf.items() if arr}
    ex = exit_rules(sym)
    cfg = trade_cfg(sym)
    if mode == "home":
        r = run_backtest(seg, sym["params"], init_cash=INIT, fee_rate=FEE,
                         allow_short=True, exit_rules=ex, sizing="fixed",
                         margin_usdt=MARGIN, leverage=LEV, live_gate=cfg,
                         gate_tf=gate_tf, candles_by_tf=cbtf_seg)
    else:  # v3 形态识别页口径：同 ST/同出场，仅切 V3 过滤
        r = run_backtest(seg, sym["params"], init_cash=INIT, fee_rate=FEE,
                         allow_short=True, exit_rules=ex, sizing="fixed",
                         margin_usdt=MARGIN, leverage=LEV, v3_filter=True,
                         gate_tf=gate_tf, candles_by_tf=cbtf_seg)
    if "error" in r:
        return dict(err=r["error"])
    s = stats(r)
    s.update(dict(hold=r.get("hold_pct", 0.0), blocked=r.get("er_blocked", 0),
                  start=dt.datetime.fromtimestamp(seg[0]["ts"] / 1000,
                                                  dt.timezone.utc).strftime("%Y-%m-%d"),
                  end=dt.datetime.fromtimestamp(seg[-1]["ts"] / 1000,
                                                dt.timezone.utc).strftime("%Y-%m-%d"),
                  bars=len(seg)))
    return s


def fmt_params(sym):
    p = sym["params"]
    er = sym.get("exit_rules") or {}
    parts = [f"ST{p['periods']}×{p['multiplier']}"]
    if er.get("tp1_pct") is not None:
        parts.append(f"tp1 {er['tp1_pct']}/{er['tp1_ratio']}")
    if er.get("sl_pct") is not None:
        parts.append(f"sl {er['sl_mode']}{er['sl_pct']}")
    parts.append(f"trail={'on' if er.get('trail_with_st') else 'off'}")
    return " · ".join(parts)


def main():
    live = load_live()
    rows = []
    for name in SYMS:
        sym = live.get(name)
        if not sym:
            print(f"{name}: 线上两页均无配置，跳过")
            continue
        tag = PAGE_BY_SYM[name]
        print(f"\n──── {name} [页{tag}] {sym['symbol']} ──── {fmt_params(sym)}")
        home = run_one(name, sym, "home")
        v3 = run_one(name, sym, "v3")
        rec = {"name": name, "tag": tag, "params": fmt_params(sym)}
        for k, m in (("home", home), ("v3", v3)):
            if "err" in m:
                print(f"    [{k}] !! {m['err']}")
                rec[k] = None
            else:
                rec[k] = m
                print(f"    [{k}] 笔={m['n']} 胜率={m['wr']:.1f}% 盈亏比={m['payoff']:.2f} "
                      f"PF={m['pf']:.2f} Σ收益={m['tot']:+.2f}% 复利={m['comp']:+.2f}% "
                      f"回撤={m['mdd_u']:.2f}U 买入持有={m['hold']:+.2f}% 拦={m['blocked']} "
                      f"({m['start']}~{m['end']})")
        rows.append(rec)

    print("\n" + "=" * 96)
    print("【首页(live_gate) vs 形态识别页(V3) · 2026-02-26~10-11 · 固定 100U×1x · Σ单笔净%】")
    print("=" * 96)
    hdr = (f"{'品种':<9}{'页':<4}{'当前参数':<26}{'口径':<8}{'笔':>4}{'胜率%':>7}"
           f"{'盈亏比':>7}{'PF':>6}{'Σ收益%':>9}{'回撤U':>8}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        if not r.get("home") and not r.get("v3"):
            print(f"{r['name']:<9}{r['tag']:<4}{r['params']:<26}  (无数据)")
            continue
        for k in ("home", "v3"):
            m = r.get(k)
            if not m:
                continue
            lbl = "首页" if k == "home" else "形态页"
            print(f"{r['name']:<9}{r['tag']:<4}{r['params']:<26}{lbl:<8}{m['n']:>4}"
                  f"{m['wr']:>7.1f}{m['payoff']:>7.2f}{m['pf']:>6.2f}{m['tot']:>9.2f}"
                  f"{m['mdd_u']:>8.2f}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
