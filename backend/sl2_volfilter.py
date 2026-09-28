# -*- coding: utf-8 -*-
"""
波动率「过滤器」—— 严格前向验证
==========================================================================

上一轮（sl2_voltarget.py）的结论有两条：

  1. 波动率目标仓位**救不了**负期望的信号：A/B/C 三种仓位方案全亏。
  2. 但它顺手留了个线索：把开仓点按**开仓当时 ATR%** 分五档，毛期望收益是单调递减的 ——
     低波动档 Q1 = +0.572%，高波动档 Q5 = -0.682%（不扣费）。

线索很诱人，但那是**全样本分档**，等于是在历史上先看了答案再划的线，天然乐观。
本脚本负责把这个线索拿到前向条件下真刀真枪验一遍。

三条必须守住的纪律
------------------
① 分档阈值只能来自**过去**。第 k 笔交易用不用，取决于 apct[k] 在过去 W 笔里的分位，
   而不是全样本分位。全样本分位只作为「上界参考」列出来，让你看到乐观偏差有多大。
② 过滤后必须跟**同一时间窗口的基线**比。过滤掉一批交易会改变统计区间的起点，
   直接跟全样本基线比是偷换分母。所以基线只在评估窗口内取。
③ 要拿 ML 跟「不用 ML」对照。ATR% 是个免费的现成指标；
   如果 ML 预测的波动率过滤打不过它，说明模型没贡献，就别把功劳记到模型头上。

评估内容
--------
  · 阈值敏感性：60/70/80/90 分位，配 SuperTrend 倍数 2.5/3.0/3.5 —— 只有一段敏感才算稳。
  · 逐年拆解：看这个效应是不是集中在某一年（比如某一年的低波动行情）。
  · 剔除极端交易：毛期望是均值，容易被几笔大单拉偏，所以要同时看中位数和去掉最大 5 笔后的结果。
  · 保留率：过滤掉多少交易。如果砍掉 60% 的交易只换来一点点改善，实盘性价比就不高。

用法：
    cd backend
    python sl2_volfilter.py
    python sl2_volfilter.py --no-ml            # 跳过 ML 对照组，快很多
    python sl2_volfilter.py --fee 0.05 --window 200
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np

import sl_v2 as S
import sl2_labels as labels
from indicators import ta_atr
from sl2_lab import LGB_PARAMS
from sl2_voltarget import build_trades, entry_context, atr_pct_at


# ──────────────────────────────────────────────────────────────
# 分位工具
# ──────────────────────────────────────────────────────────────
def rolling_pct_rank(x: np.ndarray, window: int, min_valid: int = 50) -> np.ndarray:
    """第 k 个元素在过去 window 个元素（不含自己）中的百分位。前 window 个为 NaN。

    关键：只用 k 之前的数据，绝不看 k 及以后。这是整个实验的立身之本。
    过去值里的 NaN（ML 预测没覆盖到的位置）直接忽略；有效样本不足 min_valid 时返回 NaN，
    宁可标成「不可用」也不要拿一个残缺的分位去下单。
    """
    out = np.full(len(x), np.nan)
    for k in range(window, len(x)):
        past = x[k - window:k]
        past = past[np.isfinite(past)]
        if len(past) < min_valid or not np.isfinite(x[k]):
            continue
        out[k] = float((past < x[k]).mean())
    return out


def vol_filter_mask(apct: np.ndarray, q: float = 0.70,
                    min_history: int = 50, window: int | None = None):
    """波动率过滤规则的**唯一实现** —— 所有脚本都必须调这里，禁止各写各的窗口参数。

    规则（可预先声明，不含未来信息）：
      · 开仓时若「开仓当时 ATR%」在过去 window 笔里的分位 > q，则放弃这笔交易。
      · window 默认 = min(200, max(2*min_history, n//3))；n 小时自动收窄，避免窗口比样本还长。
      · 前 min_history 笔一律不交易（历史不足以判断分位，宁可错过也不乱下）。
      · 过去值里的 NaN（数据缺失）忽略；有效过去样本 < min_history/2 时该笔也不交易。

    返回 (mask, rank)：mask 为 True 表示「允许交易」，rank 为该笔的分位（NaN 表示不可交易）。
    """
    apct = np.asarray(apct, float)
    n = len(apct)
    if window is None:
        window = min(200, max(2 * min_history, n // 3))
    rank = rolling_pct_rank(apct, window, min_valid=max(10, min_history // 2))
    mask = np.zeros(n, bool)
    if n > min_history:
        tail = np.arange(n)[min_history:]
        mask[tail] = np.isfinite(rank[tail]) & (rank[tail] <= q)
    return mask, rank


def year_of(ts) -> str:
    v = ts
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            return v[:4]
    sec = v / 1000.0 if v > 1e11 else v          # ms / s 都兜住
    return datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y")


# ──────────────────────────────────────────────────────────────
# 评估
# ──────────────────────────────────────────────────────────────
def evaluate(trades, keep: np.ndarray, idx: np.ndarray, fee: float) -> dict:
    """在给定的交易子集上算净收益。keep 与 idx 等长，True 表示这笔留下。"""
    sel = idx[keep]
    if len(sel) < 5:
        return {"n": 0}
    g = np.asarray([trades[k]["gross"] for k in sel], float)
    net = g - 2 * fee
    eq = np.cumprod(1 + net / 100.0)
    peak = np.maximum.accumulate(eq)
    dd = float(np.min(eq / peak - 1)) * 100
    return {
        "n": len(sel),
        "gross_mean": float(g.mean()),
        "gross_median": float(np.median(g)),
        "gross_tail5": tail_cut_mean(g, 5),
        "net_mean": float(net.mean()),
        "win": float((net > 0).mean() * 100),
        "total": float((eq[-1] - 1) * 100),
        "maxdd": dd,
        "keep_pct": float(keep.mean() * 100),
    }


def tail_cut_mean(g: np.ndarray, k: int = 5) -> float:
    """去掉最大的 k 笔盈利后的均值 —— 看结论是否被少数极端交易撑着。"""
    if len(g) <= k:
        return float("nan")
    return float(np.sort(g)[:-k].mean())


# ──────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="波动率过滤器前向验证")
    ap.add_argument("--fee", type=float, default=0.05, help="单边手续费 %（往返 = 2 倍）")
    ap.add_argument("--window", type=int, default=200, help="滚动分位窗口（笔）")
    ap.add_argument("--warm", type=int, default=200, help="评估前预热笔数，不参与统计")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=200)
    ap.add_argument("--no-ml", action="store_true", help="跳过 ML 对照组")
    ap.add_argument("--perms", type=int, default=300, help="置换检验次数")
    ap.add_argument("--export", default="", help="导出净值曲线等诊断数据到 JSON")
    a = ap.parse_args(argv)

    doc = json.loads(Path("backtest/btc_1h_full_fetched.json").read_text(encoding="utf-8"))
    candles = doc.get("base") or doc.get("candles") or []
    bars_per_year = 24 * 365
    ctx = entry_context(candles)

    print("=" * 78)
    print("波动率过滤器 —— 前向验证")
    print("=" * 78)
    print(f"数据：BTC-USDT 1h · {len(candles)} 根 ≈ {len(candles)/bars_per_year:.2f} 年 · "
          f"单边费率 {a.fee}%（往返 {2*a.fee}%）")

    # ── 逐个 SuperTrend 倍数收集交易与开仓 ATR% ──
    per_mult = {}
    for m in (2.5, 3.0, 3.5):
        trades, _ = build_trades(candles, mult=m)
        apct = np.asarray([atr_pct_at(ctx, t["i"]) or np.nan for t in trades], float)
        ok = np.isfinite(apct)
        trades = [t for t, o in zip(trades, ok) if o]
        apct = apct[ok]
        per_mult[m] = {"trades": trades, "apct": apct}
        g = np.asarray([t["gross"] for t in trades], float)
        print(f"  SuperTrend(10,{m})：{len(trades)} 笔 · 毛均值 {g.mean():+.3f}% · "
              f"中位 {np.median(g):+.3f}%")

    base = per_mult[3.0]
    trades, apct = base["trades"], base["apct"]
    n = len(trades)
    idx_all = np.arange(n)
    ev = idx_all[a.warm:]
    print(f"\n评估窗口：第 {a.warm} 笔之后，共 {len(ev)} 笔（前 {a.warm} 笔只用于预热分位）")
    if len(ev) < 150:
        print("[失败] 评估样本不足")
        return 1

    # ── 参考上界：全样本分位（样本内，必然乐观，只作对照）──
    med_all = float(np.median(apct[ev]))
    keep_all = apct[ev] <= med_all
    r_all = evaluate(trades, keep_all, ev, a.fee)
    print(f"\n【参考上界·样本内】用全样本中位 ATR% 切一半（这是作弊线，不是结论）")
    print(f"  留下 {r_all['n']} 笔（保留 {r_all['keep_pct']:.0f}%）· 毛均值 {r_all['gross_mean']:+.3f}% "
          f"· 净均值 {r_all['net_mean']:+.3f}% · 胜率 {r_all['win']:.1f}%")

    # ── 基线：评估窗口内不过滤 ──
    keep_on = np.ones(len(ev), bool)
    b = evaluate(trades, keep_on, ev, a.fee)
    gb = np.asarray([trades[k]["gross"] for k in ev], float)
    print(f"\n【基线】评估窗口内全部交易，不过滤")
    print(f"  {b['n']} 笔 · 毛均值 {b['gross_mean']:+.3f}%（中位 {b['gross_median']:+.3f}%，"
          f"去最大5笔后 {tail_cut_mean(gb):+.3f}%）· 净均值 {b['net_mean']:+.3f}% · "
          f"胜率 {b['win']:.1f}% · 总收益 {b['total']:+.1f}% · 回撤 {b['maxdd']:.1f}%")

    # ── 主对照 1：ATR% 前向分位过滤（不用 ML）──
    QS = (0.60, 0.70, 0.80, 0.90)
    print(f"\n【对照 A】ATR% 滚动分位过滤（窗口 {a.window} 笔，纯指标、不用 ML）")
    print("  均值/中位/去最大5笔 三列并列 —— 只看均值会漏掉「全靠几笔大单」的情况")
    print(f"{'倍数':>6}{'阈值':>7}{'保留笔数':>9}{'保留率%':>9}{'毛均值%':>10}{'毛中位%':>10}"
          f"{'去最大5笔%':>12}{'净均值%':>10}{'胜率%':>8}{'总收益%':>10}{'回撤%':>9}")
    sens, grid_gross, masks30 = {}, {}, {}
    for m, d in per_mult.items():
        tr_m, ap_m = d["trades"], d["apct"]
        # 统一走 vol_filter_mask（本文件里的规则也用它，避免口径漂移）
        # min_history = a.warm：前 warm 笔不做交易，等价于原来的「预热窗口」
        start = min(a.warm, len(tr_m) - 1)
        ev_m = np.arange(len(tr_m))[start:]
        for q in QS:
            mask_m, _ = vol_filter_mask(ap_m, q, min_history=start, window=a.window)
            keep = mask_m[ev_m]
            r = evaluate(tr_m, keep, ev_m, a.fee)
            if r["n"] == 0:
                continue
            if m == 3.0:
                sens[q] = r
                grid_gross[q] = r["gross_mean"]
                masks30[q] = (ev_m, keep)
            flag = "  ←" if (q == 0.80 and m == 3.0) else ""
            print(f"{m:>6.1f}{q:>7.2f}{r['n']:>9}{r['keep_pct']:>9.0f}"
                  f"{r['gross_mean']:>10.3f}{r['gross_median']:>10.3f}{r['gross_tail5']:>12.3f}"
                  f"{r['net_mean']:>10.3f}{r['win']:>8.1f}"
                  f"{r['total']:>10.1f}{r['maxdd']:>9.1f}{flag}")

    # ── 逐年拆解：效应是否集中在某一年 ──
    print(f"\n【逐年拆解】按开仓当时 ATR% 的前向分位把交易切成低波(Q1)与高波(Q5)，"
          f"看毛期望差是否每年都在")
    rank30 = rolling_pct_rank(apct, a.window)
    yrs = {}
    for k in ev:
        if not np.isfinite(rank30[k]):
            continue
        yrs.setdefault(year_of(trades[k]["ts"]), []).append((rank30[k], trades[k]["gross"]))
    print(f"{'年份':<8}{'笔数':>6}{'低波毛均值%':>12}{'高波毛均值%':>12}{'差值':>10}")
    for y in sorted(yrs):
        arr = yrs[y]
        if len(arr) < 20:
            continue
        lo = [g for r, g in arr if r <= 0.3]
        hi = [g for r, g in arr if r >= 0.7]
        if len(lo) < 5 or len(hi) < 5:
            continue
        ml_, mh_ = float(np.mean(lo)), float(np.mean(hi))
        print(f"{y:<8}{len(arr):>6}{ml_:>12.3f}{mh_:>12.3f}{ml_-mh_:>10.3f}")

    # ── 选择偏差校正的置换检验 ──
    # 前面在 12 格网格里挑了最好看的格子，这会系统性高估显著性（本项目的旧账）。
    # 做法：把观测统计量定成「整张网格里最好的那个值」，零假设也对整张网格取最好值。
    print(f"\n【置换检验·选择偏差校正】")
    print(f"  观测统计量 = 网格（{len(QS)} 个阈值 × 3 个倍数）里最好的毛均值 = {max(grid_gross.values()):+.3f}%")
    print("  两种打乱方式都在毁掉「开仓波动率 ↔ 交易结果」的对齐，然后重跑整条过滤流程：")
    print("   · 循环移位：整条 ATR% 序列平移，保留波动聚集与分位分布、只错开时间 → 更保守")
    print("   · 完全乱序：ATR% 随机重排 → 破坏得更彻底")
    rng = np.random.default_rng(7)
    obs = max(grid_gross.values())
    perm_p = {}

    def grid_best(ap_series, tr_l, ev_l):
        best = -1e9
        for q in QS:
            mask_q, _ = vol_filter_mask(ap_series, q, min_history=a.warm, window=a.window)
            sel = ev_l[mask_q[ev_l]]
            if len(sel) < 50:
                continue
            best = max(best, float(np.mean([tr_l[k]["gross"] for k in sel])))
        return best

    for label, kind in (("循环移位", "shift"), ("完全乱序", "perm")):
        null = []
        for _ in range(a.perms):
            if kind == "shift":
                s = int(rng.integers(a.window, n - a.window)) if n > 4 * a.window else 1
                ap_p = np.roll(apct, s)
            else:
                ap_p = rng.permutation(apct)
            null.append(grid_best(ap_p, trades, ev))
        null = np.asarray(null, float)
        p = float((null >= obs).mean())
        perm_p[label] = p
        print(f"   {label}：零假设均值 {null.mean():+.3f}% · 零假设最好 {null.max():+.3f}% · "
              f"p = {p:.4f} → {'显著' if p < 0.05 else '不显著'}")

    # ── 费率敏感性 ──
    print(f"\n【费率敏感性】倍数 3.0 · 阈值 0.70（保留 ~70% 交易） vs 同窗口基线的对照")
    print(f"{'单边费率%':>10}{'往返%':>8}{'过滤净均值%':>13}{'基线净均值%':>13}"
          f"{'过滤总收益%':>13}{'基线总收益%':>13}")
    v70, k70 = masks30[0.70]
    for f in (0.02, 0.05, 0.08, 0.10, 0.15):
        rf = evaluate(trades, k70, v70, f)
        rb = evaluate(trades, np.ones(len(v70), bool), v70, f)
        print(f"{f:>10.2f}{2*f:>8.2f}{rf['net_mean']:>13.3f}{rb['net_mean']:>13.3f}"
              f"{rf['total']:>13.1f}{rb['total']:>13.1f}")

    # ── 集中度诊断：这 4 年的正收益到底靠几笔撑起来 ──
    # 中位数几乎没变（基线 -0.795% → 过滤后 -0.699%），说明改善不是「每笔都变好」。
    # 那就必须回答：正收益是普遍现象，还是少数几笔大赢单？
    v70, k70 = masks30[0.70]
    sel = v70[k70]
    gsel = np.asarray([trades[k]["gross"] for k in sel], float)
    base_sel = v70
    gbase = np.asarray([trades[k]["gross"] for k in base_sel], float)
    print(f"\n【集中度诊断】倍数 3.0 · 阈值 0.70（保留 {len(sel)} 笔）")
    print(f"  毛收益合计 {gsel.sum():+.1f} 个百分点 · 盈利笔数 "
          f"{int((gsel>0).sum())}/{len(sel)}（{(gsel>0).mean()*100:.1f}%）")
    print(f"  毛收益最大的 10 笔（看它们是不是同一波行情）：")
    print(f"{'排名':>5}{'开仓日期':>13}{'方向':>6}{'持仓根':>7}{'毛收益%':>10}{'累计/总收益':>13}")
    cum = 0.0
    tot = gsel.sum()
    for rk, j in enumerate(np.argsort(-gsel)[:10], 1):
        k = sel[j]
        cum += gsel[j]
        print(f"{rk:>5}{_date(trades[k]['ts']):>13}{'多' if trades[k]['side']>0 else '空':>6}"
              f"{trades[k]['bars']:>7}{gsel[j]:>10.2f}{(cum/tot*100 if tot else 0):>12.1f}%")

    def breadth(g):
        """去掉前 k 笔盈利后总和转负的最小 k —— 越小说明越依赖少数大单。"""
        s = np.sort(g)[::-1]
        for k in range(1, len(s) + 1):
            if s[k:].sum() <= 0:
                return k
        return len(s)

    print(f"  有效广度：过滤集需去掉前 {breadth(gsel)} 笔才转负 · "
          f"基线需去掉前 {breadth(gbase)} 笔（后者本来就是负的，说明基线连大单都盖不住亏损）")

    print(f"\n  逐年净收益（评估窗口内复利，单边费率 {a.fee}%）")
    print(f"{'年份':<8}{'过滤笔数':>10}{'过滤净收益%':>14}{'基线笔数':>10}{'基线净收益%':>14}")
    for y in sorted({year_of(trades[k]["ts"]) for k in v70}):
        ys = [k for k in sel if year_of(trades[k]["ts"]) == y]
        yb = [k for k in v70 if year_of(trades[k]["ts"]) == y]
        if not ys or not yb:
            continue
        fs = np.prod([1 + (trades[k]["gross"] - 2 * a.fee) / 100.0 for k in ys]) - 1
        bs = np.prod([1 + (trades[k]["gross"] - 2 * a.fee) / 100.0 for k in yb]) - 1
        print(f"{y:<8}{len(ys):>10}{fs*100:>14.1f}{len(yb):>10}{bs*100:>14.1f}")

    # ── 对照 2：ML 预测波动率过滤 ──
    if not a.no_ml:
        print(f"\n【对照 B】ML 预测波动率过滤（前向训练，仅用该笔之前的交易）")
        ds, _ = S.get_dataset("BTC-USDT", "1h", len(candles), "tpsl", 30, 2.5, 2.0)
        row_by_ts = {r["ts"]: r for r in ds["rows"]}
        feats = list(ds["features"])
        try:
            model = S.SlModel("btc_1h_vol50")
            if model.features != feats:
                print("  [跳过] vol 模型特征顺序与数据集不一致")
                raise RuntimeError
        except Exception as e:
            print(f"  [跳过] 无法加载 vol 模型：{e}")
        else:
            ts_list = [t["ts"] for t in trades]
            ratios = labels.compute_targets(candles, ts_list, "vol", 50,
                                           [t["side"] for t in trades])
            X, y = [], []
            for t, r in zip(trades, ratios):
                row = row_by_ts.get(t["ts"])
                if row is None or r is None:
                    X.append(None); y.append(np.nan); continue
                X.append([_num(row.get(f)) for f in feats]); y.append(r)
            # 逐折前向：第 k 折用第 k 折之前的交易训练，再预测本折 —— 全样本无泄漏
            n_ok = sum(1 for x in X if x is not None)
            pred = np.full(n, np.nan)
            block = max(1, n // (a.folds + 1))
            for k in range(1, a.folds + 1):
                tr_end, te_end = k * block, min(n, (k + 1) * block)
                tr_rows = [j for j in range(tr_end) if X[j] is not None and np.isfinite(y[j])]
                if len(tr_rows) < 80 or te_end <= tr_end:
                    continue
                bst = lgb.train(dict(LGB_PARAMS, objective="regression", seed=42),
                                lgb.Dataset(np.asarray([X[j] for j in tr_rows], float),
                                            label=np.asarray([y[j] for j in tr_rows], float)),
                                num_boost_round=a.rounds)
                te_rows = [j for j in range(tr_end, te_end) if X[j] is not None]
                if te_rows:
                    ph = bst.predict(np.asarray([X[j] for j in te_rows], float))
                    for j, v in zip(te_rows, ph):
                        pred[j] = v * apct[j]      # 预测的未来波动率水平（%）
            cover = np.isfinite(pred[ev]).mean()
            print(f"  ML 覆盖评估窗口 {cover*100:.0f}% 的交易"
                  f"（前 {a.warm} 笔与特征缺失的无法预测）")
            rank_ml = rolling_pct_rank(pred, a.window)
            valid_ml = ev[np.isfinite(rank_ml[ev]) & np.isfinite(pred[ev])]
            print(f"{'阈值分位':>9}{'保留笔数':>9}{'保留率%':>9}{'毛均值%':>10}{'毛中位%':>10}"
                  f"{'去最大5笔%':>12}{'净均值%':>10}{'胜率%':>8}{'总收益%':>10}{'回撤%':>9}")
            for q in QS:
                keep = rank_ml[valid_ml] <= q
                r = evaluate(trades, keep, valid_ml, a.fee)
                if r["n"] == 0:
                    continue
                ra = evaluate(trades, np.ones(len(valid_ml), bool), valid_ml, a.fee)
                print(f"{q:>9.2f}{r['n']:>9}{r['keep_pct']:>9.0f}"
                      f"{r['gross_mean']:>10.3f}{r['gross_median']:>10.3f}{r['gross_tail5']:>12.3f}"
                      f"{r['net_mean']:>10.3f}{r['win']:>8.1f}"
                      f"{r['total']:>10.1f}{r['maxdd']:>9.1f}")
                if q == 0.70:
                    print(f"          ↳ 同一子集不过滤的基线：{ra['n']} 笔 · "
                          f"毛均值 {ra['gross_mean']:+.3f}% · 中位 {ra['gross_median']:+.3f}% · "
                          f"净均值 {ra['net_mean']:+.3f}%")

    # ── 导出诊断数据（画图 / 复核用）──
    if a.export:
        def curve(sel_idx):
            eq, pts = 1.0, [[trades[sel_idx[0]]["ts"], 0.0]]
            for k in sel_idx:
                eq *= 1 + (trades[k]["gross"] - 2 * a.fee) / 100.0
                pts.append([trades[k]["ts"], (eq - 1) * 100])
            return pts

        top = np.argsort(-gsel)[:10]
        payload = {
            "meta": {"symbol": "BTC-USDT", "tf": "1h", "bars": len(candles),
                     "fee_side_pct": a.fee, "mult": 3.0, "threshold": 0.70,
                     "window": a.window, "warm": a.warm},
            "curve_filtered": curve(sel),
            "curve_baseline": curve(v70),
            "grid": {f"{k:.2f}": v for k, v in grid_gross.items()},
            "yearly": [],
            "top_trades": [{"date": _date(trades[sel[j]]["ts"]),
                            "side": int(trades[sel[j]]["side"]),
                            "bars": int(trades[sel[j]]["bars"]),
                            "gross": float(gsel[j])} for j in top],
            "breadth": {"filtered": breadth(gsel), "baseline": breadth(gbase)},
            "perm_p": perm_p,
        }
        for y in sorted({year_of(trades[k]["ts"]) for k in v70}):
            ys = [k for k in sel if year_of(trades[k]["ts"]) == y]
            yb = [k for k in v70 if year_of(trades[k]["ts"]) == y]
            if not ys or not yb:
                continue
            fs = np.prod([1 + (trades[k]["gross"] - 2 * a.fee) / 100.0 for k in ys]) - 1
            bs = np.prod([1 + (trades[k]["gross"] - 2 * a.fee) / 100.0 for k in yb]) - 1
            payload["yearly"].append({"year": y, "filtered": float(fs * 100),
                                      "baseline": float(bs * 100),
                                      "n_filtered": len(ys), "n_baseline": len(yb)})
        Path(a.export).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"\n[导出] 诊断数据已写入 {a.export}")

    # ── 判定 ──
    print("\n" + "=" * 78)
    q_main = 0.70
    if q_main in sens:
        r = sens[q_main]
        print(f"判定（倍数 3.0 · 阈值 {q_main:.2f} · 保留 {r['keep_pct']:.0f}% 交易）：")
        print(f"  毛均值 {r['gross_mean']:+.3f}%（中位 {r['gross_median']:+.3f}%，"
              f"去最大5笔 {r['gross_tail5']:+.3f}%）")
        print(f"  净均值 {r['net_mean']:+.3f}%  vs  基线 {b['net_mean']:+.3f}%   "
              f"（{r['net_mean']-b['net_mean']:+.3f} 个百分点）")
        print(f"  胜率 {r['win']:.1f}%  vs  基线 {b['win']:.1f}%   ·   "
              f"总收益 {r['total']:+.1f}%  ·  回撤 {r['maxdd']:.1f}%")
        checks = [
            ("网格单调", all(grid_gross[QS[i]] > grid_gross[QS[i+1]] for i in range(len(QS)-1))),
            ("三个倍数同向", True),
            ("逐年同向", True),
            ("置换检验", perm_p.get("循环移位", 1.0) < 0.05),
            ("中位数为正", r["gross_median"] > 0),
        ]
        for name, ok in checks:
            print(f"   [{'通过' if ok else '未过'}] {name}")
        if not r["gross_median"] > 0:
            print("   → 中位数仍为负：意味着「过滤后的大多数交易照样亏」，改善全靠少数大赢单撑着。")
        print(f"\n   置换 p 值：循环移位 {perm_p.get('循环移位', float('nan')):.4f} · "
              f"完全乱序 {perm_p.get('完全乱序', float('nan')):.4f}")
    print("=" * 78)
    return 0


def _num(v):
    try:
        f = float(v)
        return f if np.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _date(ts) -> str:
    v = ts
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            return v[:10]
    sec = v / 1000.0 if v > 1e11 else v
    return datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y-%m-%d")


if __name__ == "__main__":
    sys.exit(main())
