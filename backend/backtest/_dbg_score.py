# -*- coding: utf-8 -*-
import json, os, sys
from dataclasses import replace
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import run_backtest
from _live_cfg_backtest import exit_rules, trade_cfg, _get
import regime_scoring as RS

LIVE = "http://43.108.10.84:5174/api/trade/symbols"
d = _get(LIVE)
sym_map = {s["symbol"].replace("-USDT-SWAP","").replace("-USDT",""): s for s in d["symbols"]}
s = sym_map["MU"]
print("use_scoring(sym)=", s.get("use_scoring"), "score_engine=", repr(s.get("score_engine")))
cfg = trade_cfg(s)
print("cfg.use_scoring=", cfg.use_scoring, "cfg.score_engine=", repr(cfg.score_engine),
      "cfg.enabled=", cfg.enabled)
v2cfg = replace(trade_cfg(s), score_engine="v2") if (lambda: True)() else cfg
cfg = v2cfg  # 测试 v2 引擎是否触发 score_signal
print("TESTING engine=", repr(cfg.score_engine))

cnt = {"n": 0}
_orig = RS.score_signal
def _p(sig, candles, c, cbtf=None, p=None):
    cnt["n"] += 1
    if cnt["n"] <= 3:
        print("PATCH fired ts=", sig.get("ts"), "engine=", getattr(c,"score_engine",None))
    return _orig(sig, candles, c, cbtf, p)
RS.score_signal = _p

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
cbtf = {k: v for k, v in json.load(open(os.path.join(DATA,"MU.json"),encoding="utf-8")).items() if isinstance(v,list) and v}
gtf = s["allow_tfs"][0]
common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True, exit_rules=exit_rules(s),
              sizing="fixed", margin_usdt=s["margin_usdt"], leverage=s["leverage"],
              gate_tf=gtf, candles_by_tf=cbtf)
r = run_backtest(cbtf[gtf], s["params"], live_gate=cfg, **common)
print("PATCH called", cnt["n"], "times; trades=", r.get("trades"))
RS.score_signal = _orig
