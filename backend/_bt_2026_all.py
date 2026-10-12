# -*- coding: utf-8 -*-
"""43 / 47 两服务器、首页 + 形态页全部已配置品种 · 当前线上配置 · 2026 至今回测汇总。

口径：
  首页：backtest_engine.run_backtest（ST翻转 + 线上评分闸门 + EnhancedExitRules），
        固定 100U 名义 × 1x 不复利，Σ净% = 每笔净收益率之和。IS/OOS 按 1h 序列 70% 切点。
  形态页：1h SuperTrend(10,3.0) 翻转 + 收盘确认 + V3 闸门 + 出场规则，
          固定 100U 名义 × 1x 不复利（与 bt_2026_43_47.py 完全一致）。

输出：每个品种的 2026 Σ净% / 复利% / 最大回撤 / 笔数 / 胜率 / PF / IS% / OOS%。
"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bt_home_intc_combo as H      # 首页引擎：evaluate / load_data / IS_RATIO
import bt_2026_43_47 as PP          # 形态页引擎：fetch_cfg / pick / build_signals / backtest / metrics
import bt_home_2026 as B            # 实时配置源：LIVE43 / LIVE47 / _get

HOSTS = {"43": B.LIVE43, "47": B.LIVE47}
HOME = [("43", "ETH"), ("43", "BTC"), ("43", "INTC"), ("43", "AMD"), ("43", "TSLA"),
        ("47", "CL"), ("47", "SKHYNIX"), ("47", "NVDA"), ("47", "MU"), ("47", "SNDK")]
PAT = ["CL", "SPCX"]


def home_sym(tag, base):
    """实时拉首页配置，把全局 params(ST) 合进每个品种。"""
    d = B._get(HOSTS[tag])
    gparams = d.get("params") or {}
    for s in d.get("symbols") or []:
        b = s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
        if b.upper() == base.upper():
            s = dict(s)
            if "params" not in s:
                s["params"] = gparams
            return s
    return None


def fmt_pf(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


def main():
    rows = []

    # ── 首页 ──
    for tag, base in HOME:
        sym = home_sym(tag, base)
        if not sym:
            rows.append((base, tag, "首页", None, "无配置/拉取失败")); continue
        cbtf = H.load_data(base)
        seg = cbtf.get("1h") or []
        if not seg:
            rows.append((base, tag, "首页", None, "无行情")); continue
        si = int(len(seg) * H.IS_RATIO)
        sts = seg[si]["ts"]
        r, err = H.evaluate(sym, cbtf, sts)
        if err:
            rows.append((base, tag, "首页", None, f"失败:{err}")); continue
        a = r["all"]
        rows.append((base, tag, "首页",
                     dict(n=a["n"], wr=a["wr"], pf=a["pf"], tot=a["tot"], comp=a["comp"],
                           mdd=a["mdd"], is_=r["is_"]["tot"], oos=r["oos"]["tot"]), None))

    # ── 形态页 ──
    cfgs = PP.fetch_cfg(refresh=True)
    for name in PAT:
        tag, srow, gcfg = PP.pick(cfgs, name)
        if srow is None:
            rows.append((name, tag or "-", "形态页", None, "无配置")); continue
        sym = srow["symbol"]
        base, h4 = PP.load_data(sym)
        if not base:
            rows.append((name, tag, "形态页", None, "无行情")); continue
        r = PP.merged_rules(srow, gcfg)
        sigs, _o, highs, lows, closes, up, dn, flip = PP.build_signals(base, h4)
        pool = [s for s in sigs if s.get("v3_execute")] if r["filter_v3"] else sigs
        if r["block_4h"]:
            pool = [s for s in pool if s.get("pass_4h")]
        win = [s for s in pool if s["ts"] >= PP.Y2026]
        if not win:
            rows.append((name, tag, "形态页", dict(n=0), "2026 无放行信号")); continue
        tr = PP.backtest(win, highs, lows, closes, up, dn, flip, r)
        m = PP.metrics(tr)
        split_ts = base[int(len(base) * H.IS_RATIO)]["ts"]
        is_p = sum(t["pnl"] for t in tr if t["ts"] < split_ts)
        oos_p = sum(t["pnl"] for t in tr if t["ts"] >= split_ts)
        rows.append((name, tag, "形态页",
                     dict(n=m["n"], wr=m["wr"], pf=m["pf"], tot=m["tot"], comp=None,
                           mdd=m["dd"], is_=is_p, oos=oos_p), None))

    # ── 汇总表 ──
    print("\n" + "=" * 118)
    print("=== 43 / 47 全部已配置品种 · 当前线上配置 · 2026 至今回测（每笔固定 100U 名义 / 1x / 不复利）===")
    print(f"{'品种':<9}{'服':<4}{'策略':<8}{'笔数':>5}{'胜率':>8}{'PF':>7}"
          f"{'2026Σ净%':>11}{'复利%':>9}{'最大回撤':>10}{'IS%':>9}{'OOS%':>9}")
    print("-" * 118)
    for row in rows:
        name, tag, page, d, note = row
        if d is None:
            print(f"{name:<9}{tag:<4}{page:<8}{'—':>5}   {note}")
            continue
        if d["n"] == 0:
            print(f"{name:<9}{tag:<4}{page:<8}{'0':>5}   {note}")
            continue
        comp = f"{d['comp']:+.2f}" if d["comp"] is not None else "  -  "
        print(f"{name:<9}{tag:<4}{page:<8}{d['n']:>5}{d['wr']:>7.1f}%{fmt_pf(d['pf']):>7}"
              f"{d['tot']:>+11.2f}{comp:>9}{d['mdd']:>10.2f}{d['is_']:>+9.2f}{d['oos']:>+9.2f}")

    # 合计（Σ净% 简单相加；不同品种间不相关，仅作量级参考）
    tot_all = sum(r[3]["tot"] for r in rows if isinstance(r[3], dict) and r[3].get("n"))
    print("-" * 118)
    print(f"{'合计Σ净%':<13}{'':<4}{'':<8}{'':>5}{'':>8}{'':>7}{tot_all:>+11.2f}")
    print("\n注：Σ净% = 每笔净收益率之和（每笔固定 100U 名义，不复利）；最大回撤为回测权益曲线峰谷差（U）。")
    print("     首页策略与形态页策略信号逻辑不同，CL 同时出现在 43 形态页与 47 首页（两套独立策略）。")


if __name__ == "__main__":
    main()
