# -*- coding: utf-8 -*-
"""正式 flow 双因子 gate（vol proxy 版，已验证稳健）。

验证结论（2026 全周期，4 品种，本地 OKX 数据 + 线上 opt 配置）：
- flow 因子（量价配合 = tanh(vol_z)*sign(price_chg)）相比纯动量，3/4 品种反超单因子，
  且增益在宽参数区间成立（非过拟合）。纯动量因子 0/4 反超，证实动量⊆趋势信息（共线）无效。
- 趋势流畅品种（MU/CL）→ 用补位（量能确认增加好交易）；
  高频假突破品种（SKHYNIX 7x2.0）→ 用否决（量能过滤假突破，扭亏为盈）；
  SNDK 已强（3.38%）提升空间极小。
- 限制：vol proxy 非真 OI/订单流；OKX 免费历史 OI 拿不到，资金费率仅约33天。
  真·低相关因子验证见 _scan_funding.py（用真实费率交叉确认方向一致）。

因子定义：
  vol_z = (vol_now - mean(recent)) / std(recent)     # 量能 z-score
  flow  = tanh(vol_z / SCALE) * sign(price_chg_over_lookback)   # 量价配合方向，∈[-1,1]
  composite = W_TREND * trend_norm + W_FLOW * flow
    trend_norm = (total_score - 50) / 50

用法：
  from flow_gate import make_flow_gate, make_flow_veto
  run_backtest(..., gate_fn=make_flow_gate(0.3))        # 补位（趋势未开但量价配合强则开）
  run_backtest(..., gate_fn=make_flow_veto(0.5))       # 否决（趋势想开但量能背离则否）
"""
import math


FLOW_LOOKBACK = 24
FLOW_SCALE = 2.0


def flow_factor(candles, lookback=FLOW_LOOKBACK, scale=FLOW_SCALE):
    if len(candles) <= lookback:
        return 0.0
    vs = [c["vol"] for c in candles[-lookback:]]
    m = sum(vs) / len(vs)
    s = (sum((x - m) ** 2 for x in vs) / len(vs)) ** 0.5
    if s <= 1e-9:
        return 0.0
    vz = (candles[-1]["vol"] - m) / s
    pc = candles[-1]["c"] / candles[-lookback]["c"] - 1.0
    sign = 1.0 if pc >= 0 else -1.0
    return math.tanh(vz / scale) * sign


def make_flow_gate(THETA, lookback=FLOW_LOOKBACK, scale=FLOW_SCALE,
                   W_TREND=0.5, W_FLOW=0.5):
    from integration import enhanced_signal_handler
    def gate(full, candles, live_gate, candles_by_tf, p, typ=None, **kw):
        base = enhanced_signal_handler(
            full, candles, live_gate, candles_by_tf=candles_by_tf, p=p,
            use_momentum=True, use_false_filter=True, use_adaptive=True)
        ts = base.get("total_score") or 0
        trend_norm = (ts - 50) / 50.0
        flow = flow_factor(candles, lookback, scale)
        composite = W_TREND * trend_norm + W_FLOW * flow
        if base.get("trade") and not base.get("hidden"):
            return base
        reasons = list(base.get("reasons") or [])
        if typ == "sell" and composite < -THETA:
            base["trade"] = True
            base["hidden"] = False
            reasons.append(f"量能补位空(c={composite:.2f},f={flow:.2f})")
            base["reasons"] = reasons
            return base
        if typ == "buy" and composite > THETA:
            base["trade"] = True
            base["hidden"] = False
            reasons.append(f"量能补位多(c={composite:.2f},f={flow:.2f})")
            base["reasons"] = reasons
            return base
        return base
    return gate


def make_flow_veto(VETO, lookback=FLOW_LOOKBACK, scale=FLOW_SCALE):
    from integration import enhanced_signal_handler
    cnt = {"n": 0}

    def gate(full, candles, live_gate, candles_by_tf, p, typ=None, **kw):
        base = enhanced_signal_handler(
            full, candles, live_gate, candles_by_tf=candles_by_tf, p=p,
            use_momentum=True, use_false_filter=True, use_adaptive=True)
        flow = flow_factor(candles, lookback, scale)
        if base.get("trade") and not base.get("hidden"):
            if typ == "buy" and flow < -VETO:
                base["trade"] = False
                base["hidden"] = True
                cnt["n"] += 1
                return base
            if typ == "sell" and flow > VETO:
                base["trade"] = False
                base["hidden"] = True
                cnt["n"] += 1
                return base
            return base
        return base
    return gate, cnt


def st_direction(candles, params):
    """返回最后一根 K 的 SuperTrend 方向：+1 多 / -1 空 / 0 未知。
    供实盘补位模式判断「SuperTrend 是否已转向」。"""
    try:
        from indicators import super_trend
        o = [c["o"] for c in candles]
        h = [c["h"] for c in candles]
        l = [c["l"] for c in candles]
        c_ = [c["c"] for c in candles]
        pp = params if isinstance(params, dict) else vars(params)
        st = super_trend(o, h, l, c_,
                         periods=pp.get("periods", 15),
                         multiplier=pp.get("multiplier", 9.1),
                         change_atr=True)
        tr = st.get("trend") or []
        if not tr:
            return 0
        t = tr[-1]
        if t is None:                     # ATR 未成型
            return 0
        return 1 if t > 0 else (-1 if t < 0 else 0)
    except Exception:
        return 0


def apply_flow_gate(candles, params, sig, gate, cfg):
    """实盘双因子：在 enhanced_signal_handler 的结果 gate 上做否决/补位。

    返回可能修改的 gate 副本（新增 trade 键）。flow_enabled=False 时原样返回。
    - veto：趋势想开仓(gate.trade=True)但量能因子背离(flow 与方向反向超阈值)→否决
    - fill：SuperTrend 已转向但被次级过滤拦掉(gate.trade=False)，量能因子极强同向→复活补位
    """
    if not getattr(cfg, "flow_enabled", False):
        return gate
    flow = flow_factor(candles, getattr(cfg, "flow_lookback", 24),
                       getattr(cfg, "flow_scale", 2.0))
    want_long = sig.get("type") == "buy"
    if getattr(cfg, "flow_mode", "veto") == "veto":
        if gate.get("trade"):
            diverge = (want_long and flow < -cfg.flow_veto) or \
                      (not want_long and flow > cfg.flow_veto)
            if diverge:
                gate = {**gate, "trade": False}
                _r = f"双因子否决：量能因子{flow:.2f}背离(阈值{cfg.flow_veto:.2f})"
                gate["reasons"] = list(gate.get("reasons", [])) + [_r]
    else:  # fill
        if not gate.get("trade"):
            st_dir = st_direction(candles, params)
            same_dir = (want_long and st_dir > 0) or (not want_long and st_dir < 0)
            strong = (want_long and flow >= cfg.flow_theta) or \
                     (not want_long and flow <= -cfg.flow_theta)
            if same_dir and strong:
                gate = {**gate, "trade": True}
                _r = f"双因子补位：量能因子{flow:.2f}共振(阈值{cfg.flow_theta:.2f})"
                gate["reasons"] = list(gate.get("reasons", [])) + [_r]
    return gate
