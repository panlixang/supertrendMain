# -*- coding: utf-8 -*-
"""Signal Engine V3 ── 趋势打分闸门。

回测（backtest/build_signal_features_1h.py）与实盘（pattern_trade.py）共用本模块，
保证「可选参数里的 V3 过滤」和历史复盘是同一个口径。

架构（不再是 Signal→Gate→Trade 的单闸门，而是打分制）：

              ST Signal
                   |
        +----------+-----------+-----------+
    Trend Gate          Early Breakout V2      Slow Catch
   （成熟趋势）          （早期启动，仅多头）    （慢热接住，仅多头）
        |                      |                    |
        +----------+-----------+----------+---------+
                   |
              Trend Score
                   |
            Range Penalty（两级 Fuse）
                   |
                Execute

多头三条放行路径（score 100 / 80 / 60）：
  路径1「成熟趋势」Trend Gate : 4h MA30 斜率 > 0.16  或 (斜率 > 0 且 1h MA30 距离 > 0.13)
  路径2「早期启动」V2 旁路    : break_mom5      > 0.5   （动量突破）
                             ㄱ atr_contract50  > 0     （波动收缩后重新扩张）
                             ㄱ st_dist_change  > 0     （ST 方向一致，不是冲一下就回落）
                             ㄱ |C-MA30|/ATR    < 3     （可以脱离 MA，但不能已飞太远＝末端追涨）
  路径3「慢热接住」（新增）   : 4h MA30 斜率    > -0.6  （允许 4h 尚未转正，但不可太熊）
                             ㄱ mom5           > 0.6   （1h 5根动量已启动）
    └ 动机：漏掉的大赢家中 19 个是多头，几乎全因「4h 斜率 ≤ 0」被路径1 硬门槛
      挡掉（其中 17 笔是「先逆信号洗盘再反转」型）。路径3 放宽斜率、改用 1h
      动量作证据，接住慢热型启动（详见 backtest/_v3_exit_research.py）。

空头：沿用原 Short Gate（数据未证明需要放宽，不做旁路）
  (前100根_波动周期 < 9.5 且 前20根_ADX变化 < 3.25)
  或 (前100根_波动周期 > 9.5 且 4h_MA30距离 > -0.7 且 前20根_ER变化 > -0.06)

两级 Extreme Range Fuse（普通震荡减权，极端震荡才关闭）：
  一级（降权，非禁止） flip50 > 6                 → score -= 20
  二级（硬禁）         flip50 > 8 且 ER20 < 0.15  → 禁止交易

回测口径（808 笔 1h 信号，1x 满仓复利 + 标准最大回撤）：
  仅路径1/2（旧）          ：放行 498 笔，累计 +314.4%，PF 1.35，DD 28.2%，红字（盈亏>2%）105/137 = 77%
  含路径3「慢热接住」（现行）：放行 550 笔，累计 +404.6%，PF 1.37，DD 26.8%，红字 116/137 = 85%
    └ 路径3 单独看：新增放行 52 笔（其中 33 笔亏损），净贡献 +22.95%；
      但挽回 11/32 个原被漏掉的大赢家（多头漏赢家 19 个中挽回 11 个 ≈ 58%，贡献 +61.13%）。
  验证脚本：backtest/_verify_v3_third.py（用本模块真实 v3_decide 含 Fuse 复算）。
"""
from __future__ import annotations

import bisect

from indicators import super_trend, ta_adx, ta_sma

# 打分常量（tenure）
SCORE_MATURE = 100      # 路径1：成熟趋势（Trend Gate 通过）
SCORE_EARLY = 80        # 路径2：早期启动（V2 旁路通过）
SCORE_SLOW = 60         # 路径3：慢热接住（4h未转正但1h已启动，信心低于前两条）
PENALTY_RANGE = 20      # 一级 Fuse：震荡减权
SCORE_BAN = -1000       # 二级 Fuse：硬禁

# 路径3「慢热接住」阈值 —— backtest/_v3_exit_research.py 网格寻优结果
#   （slope=-0.6 / mom=0.6 为最优：550笔 复利404.6% PF1.37 DD26.8%，
#    挽回 11/32 漏掉的大赢家，其中多头漏赢家挽回 11/19 ≈ 58%）
SLOW_SLOPE_MIN = -0.6   # 4h MA30 斜率下界：允许仍为负，但不能太熊
SLOW_MOM_MIN = 0.6      # 1h 5根动量(mom5=(C[i]-C[i-5])/ATR) 下界：已启动

PATH_MATURE = "成熟趋势"
PATH_EARLY = "早期启动"
PATH_SLOW = "慢热接住"
PATH_NONE = "未通过"
PATH_FUSE = "极端震荡熔断"


# ── 路径1：多头 Trend Gate（成熟趋势）──────────────────────────
def long_mature_gate(slope_htf, dist_base_ma):
    """4h MA30 斜率足够陡；或斜率向上且价格已站上 1h MA30 一定距离。

    slope_htf    : 4h_MA30斜率（%，10根 MA30 累计变化）
    dist_base_ma : 1h_MA30距离（%，信号价相对 1h MA30 偏离）
    """
    if slope_htf is None or dist_base_ma is None:
        return False
    return (slope_htf > 0.16) or (slope_htf > 0 and dist_base_ma > 0.13)


# ── 路径2：多头 Early Breakout V2 旁路（早期启动）──────────────
def long_breakout_bypass(mom5, atr_contract50, st_dist_change, close_ma30_atr):
    """四条同时成立才算「早期启动」，缺一不可。

    mom5            : (C[i]-C[i-5])/ATR —— 动量突破
    atr_contract50  : ATR%[i-20]/ATR%[i-50]-1 —— 波动由收缩转扩张
    st_dist_change  : ST距离[i]-ST距离[i-20] —— ST 方向一致（多头需 >0）
    close_ma30_atr  : |C-MA30|/ATR —— 距离过滤：<3 才不是末端追涨
    """
    return (mom5 > 0.5
            and atr_contract50 > 0
            and st_dist_change > 0
            and close_ma30_atr < 3)


# ── 路径3：多头 慢热接住（4h 未转正，但 1h 动量已启动）──────────
def long_slow_catch(slope_htf, mom5):
    """接住「4h 趋势尚未转正、但 1h 短期动量已经起来」的慢热型启动。

    动机：V3 漏掉的大赢家中 19 个是多头，几乎全部因 `4h_MA30斜率 ≤ 0` 被
    Trend Gate 硬门槛挡掉（见 backtest/_v3_diag.py PART A/D：其中 17 笔是
    「先逆信号洗盘再反转」的震荡洗盘型——入场被扫一下才拉升，4h 斜率当时仍为负）。
    本路径放宽 4h 斜率要求（只要不太熊即可），改用 1h 5 根动量作为启动证据。

    与路径2 的区别：路径2 要求「波动收缩后重新扩张 + ST 距离变化 + 未追涨末端」
    四条同时成立（严格的早期突破）；本路径只看 1h 动量，因此能接住那些
    动量慢慢爬升、不算典型突破但最后走成大趋势的慢热型。

    slope_htf : 4h_MA30斜率（%），允许为负但不能太熊（> SLOW_SLOPE_MIN）
    mom5      : (C[i]-C[i-5])/ATR —— 1h 5 根动量，> SLOW_MOM_MIN 视为已启动
    """
    if slope_htf is None or mom5 is None:
        return False
    return slope_htf > SLOW_SLOPE_MIN and mom5 > SLOW_MOM_MIN


# ── 空头 Short Gate（沿用，不放宽）─────────────────────────────
def short_gate(vol100, adx_chg20, dist_htf_ma, er_chg20):
    """vol100=前100根_波动周期；adx_chg20=前20根_ADX变化；
    dist_htf_ma=4h_MA30距离（%）；er_chg20=前20根_ER变化。"""
    if None in (vol100, adx_chg20, dist_htf_ma, er_chg20):
        return False
    if vol100 < 9.5 and adx_chg20 < 3.25:
        return True
    if vol100 > 9.5 and dist_htf_ma > -0.7 and er_chg20 > -0.06:
        return True
    return False


# ── V3 主决策 ─────────────────────────────────────────────────
def v3_decide(side, feats):
    """给一笔信号打分并裁决是否放行。

    side  : 1=多头，-1=空头
    feats : dict，需含 ——
            斜率类（来自 CSV 列或实盘现算）：
              slope_htf / dist_base_ma / dist_htf_ma / adx_chg20 / er_chg20 / vol100
            k 线派生类（base_features 产出）：
              mom5 / atr_contract50 / st_dist_change / close_ma30_atr / flip50 / er20

    返回 dict：score / path / execute / fused
    """
    score, path = 0, PATH_NONE
    if side == 1:
        if long_mature_gate(feats.get("slope_htf"), feats.get("dist_base_ma")):
            score, path = SCORE_MATURE, PATH_MATURE
        elif long_breakout_bypass(
            feats.get("mom5", 0.0),
            feats.get("atr_contract50", 0.0),
            feats.get("st_dist_change", 0.0),
            feats.get("close_ma30_atr", 0.0),
        ):
            score, path = SCORE_EARLY, PATH_EARLY
        elif long_slow_catch(feats.get("slope_htf"), feats.get("mom5", 0.0)):
            # 路径3：4h 未转正但 1h 动量已起 —— 接住慢热型，挽回被 Trend Gate
            # 斜率硬门槛挡掉的多头大赢家（仅多头，空头数据未证明需要放宽）
            score, path = SCORE_SLOW, PATH_SLOW
    else:
        if short_gate(feats.get("vol100"), feats.get("adx_chg20"),
                      feats.get("dist_htf_ma"), feats.get("er_chg20")):
            score, path = SCORE_MATURE, PATH_MATURE

    return _apply_fuse(score, path, feats.get("flip50", 0), feats.get("er20", 0.0))


def _apply_fuse(score, path, flip50, er20):
    """两级 Extreme Range Fuse。注意：一级只降权不禁止。"""
    fused = False
    if flip50 > 6:                      # 一级：普通震荡减权
        score -= PENALTY_RANGE
    if flip50 > 8 and er20 < 0.15:      # 二级：极端震荡硬禁
        score, path, fused = SCORE_BAN, PATH_FUSE, True
    return {"score": score, "path": path, "execute": score > 0, "fused": fused}


# ── k 线派生特征（回测/实盘同一函数）───────────────────────────
def base_features(closes, atr, atr_pct, er20, st_dist, flips, ma, i, side):
    """从已算好的序列里取 V3 需要的 6 个 k 线派生特征。索引不足返回 None。"""
    if i is None or i < 50 or i >= len(closes):
        return None
    ai = atr[i] if atr and atr[i] else 0.0
    if side > 0 and ai:
        mom5 = (closes[i] - closes[i - 5]) / ai
    elif ai:
        mom5 = (closes[i - 5] - closes[i]) / ai
    else:
        mom5 = 0.0
    atr_contract50 = (atr_pct[i - 20] / atr_pct[i - 50] - 1.0
                      if atr_pct[i - 50] > 0 else 0.0)
    st_dist_change = st_dist[i] - st_dist[i - 20]
    close_ma30_atr = (abs(closes[i] - ma[i]) / ai) if (ma[i] and ai) else 0.0
    flip50 = sum(1 for f in flips if i - 50 < f <= i)
    er_val = er20[i] if er20 and len(er20) > i else 0.0
    return {
        "mom5": mom5,
        "atr_contract50": atr_contract50,
        "st_dist_change": st_dist_change,
        "close_ma30_atr": close_ma30_atr,
        "flip50": flip50,
        "er20": er_val,
    }


# ── 实盘/任意周期：从 K 线列表直接算全套 V3 特征 ────────────────
def _er20_series(c):
    cum = [0.0] * (len(c) + 1)
    for i in range(1, len(c)):
        cum[i] = cum[i - 1] + abs(c[i] - c[i - 1])
    er = [0.0] * len(c)
    for i in range(20, len(c)):
        den = cum[i] - cum[i - 20]
        er[i] = abs(c[i] - c[i - 20]) / den if den > 0 else 0.0
    return er


def _st_dist_series(trend, up, dn, c, atr):
    out = [0.0] * len(c)
    for i in range(len(c)):
        if trend[i] is None or atr[i] in (None, 0):
            continue
        line = up[i] if trend[i] == 1 else dn[i]
        out[i] = (c[i] - line) / atr[i]
    return out


def features_from_candles(candles, i, side, candles_htf=None, *,
                          st_periods=10, st_mult=3.0, ma_n=30, adx_n=14, slope_bars=10):
    """实盘用：给定基础周期 K 线 + 信号 bar 索引 + 方向，算出 v3_decide 需要的全部特征。

    candles / candles_htf : [{"ts","o","h","l","c","vol"}, ...]，按时间升序
    所有百分比口径与 build_signal_features_1h.py 保持一致（斜率/距离用 %，距离过滤用 ATR 倍数）。
    """
    o = [x["o"] for x in candles]; h = [x["h"] for x in candles]
    l = [x["l"] for x in candles]; c = [x["c"] for x in candles]
    n = len(c)
    if i is None or i < 50 or i >= n:
        return None

    res = super_trend(o, h, l, c, periods=st_periods, multiplier=st_mult, change_atr=True)
    atr = res["atr"]
    flips = sorted(f["i"] for f in res["flips"])
    atr_pct = [(atr[k] / c[k] * 100.0) if (atr[k] and c[k]) else 0.0 for k in range(n)]
    ma = ta_sma(c, ma_n)
    er20 = _er20_series(c)
    st_dist = _st_dist_series(res["trend"], res["up"], res["dn"], c, atr)

    feats = base_features(c, atr, atr_pct, er20, st_dist, flips, ma, i, side)
    if not feats:
        return None

    # 1h(基础周期) MA 距离（%）
    feats["dist_base_ma"] = ((c[i] - ma[i]) / ma[i] * 100.0) if ma[i] else None

    # 空头 Short Gate 需要的三个（口径同 build_signal_features_1h.py）
    adx = ta_adx(h, l, c, adx_n)
    a0, a1 = max(0, i - 20), i - 1
    feats["adx_chg20"] = (adx[a1] or 0) - (adx[a0] or 0)
    feats["er_chg20"] = er20[a1] - er20[a0]
    win = range(max(0, i - 100), i)
    med_list = sorted(atr_pct[k] for k in win if atr_pct[k] > 0)
    med = med_list[len(med_list) // 2] if med_list else 0.0
    feats["vol100"] = sum(1 for k in win if med > 0 and atr_pct[k] > 1.5 * med)

    # 高周期（4h）MA30 斜率 / 距离
    feats["slope_htf"] = None
    feats["dist_htf_ma"] = None
    if candles_htf:
        c4 = [x["c"] for x in candles_htf]; ts4 = [x["ts"] for x in candles_htf]
        ma4 = ta_sma(c4, ma_n)
        j4 = bisect.bisect_right(ts4, candles[i]["ts"]) - 1
        if j4 >= slope_bars and ma4[j4] and ma4[j4 - slope_bars]:
            feats["slope_htf"] = (ma4[j4] - ma4[j4 - slope_bars]) / ma4[j4 - slope_bars] * 100.0
            feats["dist_htf_ma"] = (c[i] - ma4[j4]) / ma4[j4] * 100.0
    return feats
