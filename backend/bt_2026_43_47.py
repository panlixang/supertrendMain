# -*- coding: utf-8 -*-
"""43 / 47 形态识别页「当前配置参数」2026 年回测（每笔固定 100U 名义、1x、不复利）。

口径与线上形态识别页逐条对齐：
  信号：1h SuperTrend(10, 3.0, change_atr) 翻转 + 收盘确认（用翻转那根的收盘价成交）
  过滤：filter_v3=true → V3 趋势打分闸门；block_4h 按页面全局开关（当前两台都是 false）
  出场：品种配置覆盖优先、否则回落全局档（rules_for_symbol 语义）
        tp1_pct/tp1_ratio 单档止盈 + move_sl_to_entry 保本
        sl_mode=pct → 固定 sl_pct%；sl_mode=st → SuperTrend 轨道（无效时 2% 兜底）
        trail_with_st → 止损只朝有利方向跟随 ST 轨道
        下一个反向翻转收盘 → 平剩余仓位
  仓位：单持仓不重叠（与线上 store.position 一致），固定每笔名义 100U、1x
  成本：单边 taker 0.05%

输出：笔数 / 胜率 / 盈亏比(PF=总盈/总亏、均盈/均亏) / 收益(U，固定100U本金) / 最大回撤

用法：
    python bt_2026_43_47.py                 # 全部品种，读已缓存的 43/47 配置
    python bt_2026_43_47.py --refresh-cfg   # 重新拉取两台服务器的配置
    python bt_2026_43_47.py --sym NVDA CL   # 只跑指定品种（名称片段匹配）
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bt_pattern_page import build_signals          # noqa: E402

DATA_DIR = os.path.join(HERE, "_bt2026_data")
HOSTS = {"43": "http://43.108.10.84:5174", "47": "http://47.84.106.154:5174"}

NOTIONAL = 100.0      # 每笔固定名义（U）
LEV = 1               # 1x，不复利
FEE = 0.05 / 100      # 单边 taker
SL_FALLBACK = 2.0     # sl_mode=st 且轨道无效时的兜底 %

UTC = dt.timezone.utc
Y2026 = int(dt.datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)

ORDER = ["NVDA", "CL", "MU", "SNDK", "SKHYNIX", "TSLA", "ETH",
         "INTC", "AMD", "SPCX"]


def fmt(ms):
    return dt.datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


# ── 配置 ────────────────────────────────────────────────────────
def fetch_cfg(refresh: bool) -> dict:
    out = {}
    for tag, host in HOSTS.items():
        p = os.path.join(HERE, f"_cfg_{tag}.json")
        if refresh or not os.path.exists(p):
            try:
                d = json.loads(urllib.request.urlopen(
                    host + "/api/pattern/trade/config", timeout=20).read())
                json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                print(f"[cfg] {tag} 已重新拉取")
            except Exception as e:
                print(f"[cfg] {tag} 拉取失败 {e}（用旧缓存）")
        out[tag] = json.load(open(p, encoding="utf-8"))
    return out


def pick(cfgs: dict, name: str):
    """在 43 / 47 里找该品种的配置行。"""
    for tag in ("43", "47"):
        for s in cfgs[tag].get("symbols") or []:
            if s["symbol"].upper().startswith(name.upper() + "-"):
                return tag, s, cfgs[tag].get("cfg") or {}
    return None, None, None


def merged_rules(sym_row: dict, gcfg: dict) -> dict:
    """品种覆盖优先、否则回落全局默认档（pattern_trade.rules_for_symbol 语义）。"""
    def v(k):
        x = sym_row.get(k)
        return gcfg.get(k) if x is None else x

    sl_mode = v("sl_mode") or "st"
    sl_pct = v("sl_pct")
    if sl_pct is None:
        sl_pct = SL_FALLBACK
    return dict(
        tf=(sym_row.get("allow_tfs") or ["1h"])[0],
        filter_v3=bool(v("filter_v3")),
        tp1_pct=v("tp1_pct") or 1.5,
        tp1_ratio=v("tp1_ratio") if v("tp1_ratio") is not None else 70.0,
        sl_mode=sl_mode,
        sl_pct=float(sl_pct),
        move_sl_to_entry=v("move_sl_to_entry") if v("move_sl_to_entry") is not None else True,
        trail_with_st=v("trail_with_st") if v("trail_with_st") is not None else True,
        reverse_close=bool(v("reverse_close")) if v("reverse_close") is not None else False,
        block_4h=bool(gcfg.get("block_4h", False)),
    )


# ── 数据 ────────────────────────────────────────────────────────
def load_data(sym: str):
    p = os.path.join(DATA_DIR, sym.replace("-", "_") + ".json")
    if not os.path.exists(p):
        return None, None
    d = json.load(open(p, encoding="utf-8"))
    base, h4 = d.get("1h"), d.get("4h")
    if not base or not h4:
        return None, None
    return base, h4


# ── 回测（固定名义 / 1x / 不复利）────────────────────────────────
def backtest(sigs, highs, lows, closes, up_plot, dn_plot, flip_idx, r: dict):
    tp1_r = r["tp1_ratio"] / 100.0
    trades = []
    last_exit = -1
    for s in sigs:
        i, long = s["i"], s["dir"] > 0
        if i <= last_exit:                 # 单持仓，不重叠
            continue
        entry = closes[i]
        coins = NOTIONAL * LEV / entry     # 固定 100U 名义
        if r["sl_mode"] == "st":
            stp0 = up_plot[i] if long else dn_plot[i]
            if stp0 is None or (long and stp0 >= entry) or (not long and stp0 <= entry):
                stp0 = entry * (1 - r["sl_pct"] / 100) if long else entry * (1 + r["sl_pct"] / 100)
        else:
            stp0 = entry * (1 - r["sl_pct"] / 100) if long else entry * (1 + r["sl_pct"] / 100)
        stop = stp0
        tp1p = entry * (1 + r["tp1_pct"] / 100) if long else entry * (1 - r["tp1_pct"] / 100)
        tp1 = False
        pnl = -entry * coins * FEE
        fee = entry * coins * FEE
        reason, ex, ex_i, closed = "未平(末根)", closes[-1], len(closes) - 1, False

        for j in range(i + 1, len(closes)):
            if not r["reverse_close"]:
                if r["trail_with_st"]:
                    nl = up_plot[j] if long else dn_plot[j]
                    if nl is not None:
                        if long and nl > stop:
                            stop = nl
                        elif (not long) and nl < stop:
                            stop = nl
                if long and lows[j] <= stop:
                    px, rest = stop, 1 - (tp1_r if tp1 else 0)
                    pnl += (px - entry) * coins * rest - px * coins * rest * FEE
                    fee += px * coins * rest * FEE
                    reason, ex, ex_i, closed = "止损", px, j, True
                    break
                if (not long) and highs[j] >= stop:
                    px, rest = stop, 1 - (tp1_r if tp1 else 0)
                    pnl += (entry - px) * coins * rest - px * coins * rest * FEE
                    fee += px * coins * rest * FEE
                    reason, ex, ex_i, closed = "止损", px, j, True
                    break
                if not tp1:
                    if long and highs[j] >= tp1p:
                        pnl += (tp1p - entry) * coins * tp1_r - tp1p * coins * tp1_r * FEE
                        fee += tp1p * coins * tp1_r * FEE
                        tp1 = True
                        stop = entry if r["move_sl_to_entry"] else stop
                    elif (not long) and lows[j] <= tp1p:
                        pnl += (entry - tp1p) * coins * tp1_r - tp1p * coins * tp1_r * FEE
                        fee += tp1p * coins * tp1_r * FEE
                        tp1 = True
                        stop = entry if r["move_sl_to_entry"] else stop
            if j in flip_idx:                        # 反向翻转收盘 → 平剩余
                px = closes[j]
                rest = 1 - (tp1_r if tp1 else 0)
                pnl += ((px - entry) if long else (entry - px)) * coins * rest \
                    - px * coins * rest * FEE
                fee += px * coins * rest * FEE
                reason, ex, ex_i, closed = "反向信号", px, j, True
                break
        if not closed:
            px = closes[-1]
            rest = 1 - (tp1_r if tp1 else 0)
            pnl += ((px - entry) if long else (entry - px)) * coins * rest \
                - px * coins * rest * FEE
            fee += px * coins * rest * FEE
        trades.append({"ts": s["ts"], "dir": s["dir"], "entry": entry, "exit": ex,
                       "i": i, "j": ex_i, "pnl": pnl, "fee": fee, "gross": pnl + fee,
                       "tp1": tp1, "reason": reason, "ret_pct": pnl / NOTIONAL * 100})
        last_exit = ex_i
    return trades


def metrics(tr):
    n = len(tr)
    if not n:
        return dict(n=0, wr=0.0, pf=0.0, payoff=0.0, tot=0.0, dd=0.0, hold=0.0,
                    gross=0.0, fee=0.0, tp1=0, longs=0, shorts=0, avg=0.0)
    wins = [t for t in tr if t["pnl"] > 0]
    loss = [t for t in tr if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = sum(-t["pnl"] for t in loss)
    aw = gw / len(wins) if wins else 0.0
    al = gl / len(loss) if loss else 0.0
    pf = gw / gl if gl > 0 else float("inf")
    payoff = aw / al if al > 0 else float("inf")
    tot = sum(t["pnl"] for t in tr)
    eq = peak = 0.0
    dd = 0.0
    for t in tr:
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dict(n=n, wr=len(wins) / n * 100, pf=pf, payoff=payoff, tot=tot, dd=dd,
                hold=sum(t["j"] - t["i"] for t in tr) / n, gross=sum(t["gross"] for t in tr),
                fee=sum(t["fee"] for t in tr), tp1=sum(1 for t in tr if t["tp1"]),
                longs=sum(1 for t in tr if t["dir"] > 0), shorts=sum(1 for t in tr if t["dir"] < 0),
                avg=tot / n)


# ── 主流程 ──────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-cfg", action="store_true")
    ap.add_argument("--sym", nargs="*", default=None)
    ap.add_argument("--csv", default="_bt2026_43_47_trades.csv")
    a = ap.parse_args()

    cfgs = fetch_cfg(a.refresh_cfg)
    names = a.sym or ORDER

    rows = []
    all_trades = []
    print("\n=== 43 / 47 形态识别页当前配置 · 2026 年回测（每笔固定 100U 名义、1x、不复利）===")
    print("数据：OKX 1h/4h，截止 %s（UTC）" % dt.datetime.now(UTC).strftime("%Y-%m-%d %H:%M"))

    for name in names:
        tag, srow, gcfg = pick(cfgs, name)
        if srow is None:
            print(f"\n!! {name}: 43/47 形态识别页都没有该品种，跳过")
            continue
        sym = srow["symbol"]
        base, h4 = load_data(sym)
        if not base:
            print(f"\n!! {name}: 无行情缓存，先跑 python bt2026_fetch.py")
            continue
        r = merged_rules(srow, gcfg)

        sigs, opens, highs, lows, closes, up_plot, dn_plot, flip_idx = build_signals(base, h4)
        pool = [s for s in sigs if s.get("v3_execute")] if r["filter_v3"] else sigs
        if r["block_4h"]:
            pool = [s for s in pool if s.get("pass_4h")]
        win = [s for s in pool if s["ts"] >= Y2026]

        if not win:
            print(f"\n## {name} ({tag}) 2026 年无放行信号")
            rows.append(dict(sym=name, tag=tag, n=0))
            continue

        tr = backtest(win, highs, lows, closes, up_plot, dn_plot, flip_idx, r)
        m = metrics(tr)
        for t in tr:
            all_trades.append(dict(sym=name, tag=tag, **{k: t[k] for k in
                                   ("ts", "dir", "entry", "exit", "pnl", "ret_pct", "reason")}))

        sl_desc = f"{r['sl_mode']}/{r['sl_pct']:.0f}%" if r["sl_mode"] == "pct" \
            else f"st(兜底{r['sl_pct']:.0f}%)"
        print(f"\n## {name:<8} [{tag}页] {sym}  tf={r['tf']}  "
              f"tp1={r['tp1_pct']:.1f}%/{r['tp1_ratio']:.0f}%  sl={sl_desc}  "
              f"trail={'on' if r['trail_with_st'] else 'off'}  "
              f"保本={'on' if r['move_sl_to_entry'] else 'off'}  v3={'on' if r['filter_v3'] else 'off'}")
        print(f"   1h {fmt(base[0]['ts'])} ~ {fmt(base[-1]['ts'])}  n={len(base)}  "
              f"2026 放行信号 {len(win)}（全部信号 {len(sigs)}）")
        pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
        po = "inf" if m["payoff"] == float("inf") else f"{m['payoff']:.2f}"
        print(f"   笔数 {m['n']:>3}   胜率 {m['wr']:>5.1f}%   盈亏比 PF {pf:>5} "
              f"(均盈/均亏 {po:>5})   收益 {m['tot']:+7.2f} U   最大回撤 {m['dd']:.2f} U")
        print(f"   毛利 {m['gross']:+.2f} U / 手续费 {m['fee']:.2f} U   单笔均值 {m['avg']:+.3f}%   "
              f"TP1 命中 {m['tp1']}/{m['n']}   多 {m['longs']} 空 {m['shorts']}   "
              f"平均持仓 {m['hold']:.0f} 根")

        rows.append(dict(sym=name, tag=tag, tf=r["tf"], n=m["n"], wr=m["wr"], pf=m["pf"],
                         payoff=m["payoff"], tot=m["tot"], dd=m["dd"], avg=m["avg"],
                         gross=m["gross"], fee=m["fee"], hold=m["hold"],
                         tp1=m["tp1"], longs=m["longs"], shorts=m["shorts"],
                         params=f"tp1 {r['tp1_pct']:.1f}/{r['tp1_ratio']:.0f} "
                                f"sl {sl_desc} trail={'on' if r['trail_with_st'] else 'off'}"))

    # 汇总表
    print("\n" + "=" * 108)
    print("=== 汇总（2026 年，每笔固定 100U 名义 / 1x / 不复利）===")
    hdr = (f"{'品种':<8}{'页':<4}{'笔数':>5}{'胜率':>8}{'PF':>7}{'均盈/均亏':>10}"
           f"{'收益(U)':>10}{'收益率%':>9}{'最大回撤U':>10}{'单笔均值%':>10}  参数")
    print(hdr)
    print("-" * len(hdr))
    for x in rows:
        if not x.get("n"):
            print(f"{x['sym']:<8}{x.get('tag',''):<4}{'—':>5}   2026 年无放行信号")
            continue
        pf = "inf" if x["pf"] == float("inf") else f"{x['pf']:.2f}"
        po = "inf" if x["payoff"] == float("inf") else f"{x['payoff']:.2f}"
        print(f"{x['sym']:<8}{x['tag']:<4}{x['n']:>5}{x['wr']:>7.1f}%{pf:>7}{po:>10}"
              f"{x['tot']:>+10.2f}{x['tot']:>+9.2f}{x['dd']:>10.2f}{x['avg']:>+10.3f}"
              f"  {x['params']}")
    tot_n = sum(x.get("n", 0) for x in rows)
    tot_u = sum(x.get("tot", 0.0) for x in rows)
    tot_g = sum(x.get("gross", 0.0) for x in rows)
    print("-" * len(hdr))
    print(f"{'合计':<8}{'':<4}{tot_n:>5}{'':>8}{'':>7}{'':>10}{tot_u:>+10.2f}"
          f"{tot_u:>+9.2f}{'':>10}{'':>10}  （毛利 {tot_g:+.2f} U）")
    print("\n注：收益(U)=Σ单笔盈亏，本金每笔固定 100U，故收益率%与收益(U)同值；"
          "PF=总盈/总亏，均盈/均亏=平均盈利单笔/平均亏损单笔。")

    if a.csv and all_trades:
        p = os.path.join(HERE, a.csv)
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(all_trades[0].keys()))
            w.writeheader()
            for t in all_trades:
                t = dict(t)
                t["ts"] = fmt(t["ts"])
                w.writerow(t)
        print(f"逐笔明细已写 {p}")


if __name__ == "__main__":
    main()
