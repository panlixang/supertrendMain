# -*- coding: utf-8 -*-
"""Shadow Mode（阶段3）—— 双引擎对照采集器。

当品种 cfg.shadow_engine 非空时，每次判单（regime.evaluate 调用点）会额外用
shadow 引擎重算一遍信号，并把"主引擎 vs shadow 引擎"的分歧落盘为 JSONL：

    backend/logs/shadow_<SYMBOL>.jsonl

记录字段（一行一条信号）：
    symbol / ts / tf / side / price /
    main_engine / main_score / main_action / main_reasons /
    shadow_engine / shadow_score / shadow_action / shadow_reasons

用途（配合 backtest/_shadow_analyze.py）：
    - 统计 V1买V2不买 / V2买V1不买 的分歧样本
    - 回填未来收益后判断"被新引擎过滤的信号是赢家还是亏损"，验证回测结论是否
      在实时成立（NVDA: v2 过滤有效；CL: v2 过度过滤）。

默认关闭：cfg.shadow_engine 为空 → 全部 no-op，零开销。
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import replace

import regime_scoring

_SHADOW_DIR = os.environ.get(
    "SHADOW_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"))
_LOCK = threading.Lock()


def _path(symbol: str) -> str:
    os.makedirs(_SHADOW_DIR, exist_ok=True)
    return os.path.join(_SHADOW_DIR, f"shadow_{symbol.replace('-USDT', '').replace('-SWAP', '')}.jsonl")


def shadow_engine_of(cfg) -> str:
    """返回 cfg 配置的 shadow 引擎（空=关闭）。"""
    return (getattr(cfg, "shadow_engine", "") or "").strip().lower()


def is_shadow_on(cfg) -> bool:
    return bool(shadow_engine_of(cfg))


def _engine_name(engine: str) -> str:
    aliases = {"v1": "trend_follow_v1", "v2": "quality_filter_v2"}
    return aliases.get(engine, engine)


def record_dispatch(cfg, symbol: str, sig: dict, candles: list[dict],
                    candles_by_tf: dict, p: dict, tf: str = None):
    """在判单点调用：主引擎结果已由 evaluate 产生，这里补算 shadow 引擎并落盘。

    只在 cfg.shadow_engine 非空时工作；否则直接返回。
    """
    shadow_eng = shadow_engine_of(cfg)
    if not shadow_eng:
        return
    try:
        main = regime_scoring.score_signal(sig, candles, cfg, candles_by_tf, p)
        sh_cfg = replace(cfg, score_engine=shadow_eng, score_v2=False)
        shadow = regime_scoring.score_signal(sig, candles, sh_cfg, candles_by_tf, p)
        row = {
            "symbol": symbol,
            "ts": sig.get("ts"),
            "tf": tf or sig.get("tf"),
            "side": sig.get("type"),
            "price": sig.get("price") or (candles[-1]["c"] if candles else None),
            "main_engine": _engine_name(main.get("engine") or cfg.score_engine or "v1"),
            "main_score": main.get("total_score"),
            "main_action": main.get("action"),
            "main_reasons": main.get("reasons", []),
            "shadow_engine": _engine_name(shadow.get("engine") or shadow_eng),
            "shadow_score": shadow.get("total_score"),
            "shadow_action": shadow.get("action"),
            "shadow_reasons": shadow.get("reasons", []),
        }
        with _LOCK:
            with open(_path(symbol), "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:  # Shadow 绝不能影响主流程
        import logging
        logging.getLogger(__name__).warning(f"shadow record failed: {e}")
