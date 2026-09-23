import sys, time
sys.path.insert(0, ".")
sys.path.insert(0, "backtest")
from datetime import datetime, timezone
from backtest import run_backtest
from position import ExitRules
from _live_cfg_backtest import fetch_candles

ST_PERIODS, ST_MULT = 10, 3.0
INIT_CASH, MARGIN, LEV = 1000.0, 100.0, 10
TP1_PCT, TP1_RATIO, SL_PCT = 1.5, 70.0, 2.0
START = int(datetime(2026, 3, 23, tzinfo=timezone.utc).timestamp() * 1000)
p = {"periods": ST_PERIODS, "multiplier": ST_MULT, "src": "hl2", "change_atr": True}
rules = ExitRules(tp1_pct=TP1_PCT, tp1_ratio=TP1_RATIO, sl_mode="st", sl_pct=SL_PCT,
                  move_sl_to_entry=True, trail_with_st=True)

raw1 = fetch_candles("BTC-USDT-SWAP", "1h", 5300)
candles = [c for c in raw1 if c["ts"] >= START]
print(f"candles={len(candles)}", flush=True)
r = run_backtest(candles, p, init_cash=INIT_CASH, fee_rate=0.0005, allow_short=True,
                 exit_rules=rules, sizing="fixed", margin_usdt=MARGIN, leverage=LEV, full_trades=True)
trades = r.get("trade_list") or []
n_tp1_trades = sum(1 for t in trades
                   if any("止盈" in (e.get("reason") or "") for e in t.get("exits", [])))
all_tp1_events = sum(1 for t in trades
                     for e in t.get("exits", []) if "止盈" in (e.get("reason") or ""))
print("result tp1_count / tp2 / tp3 / stop:",
      r.get("tp1_count"), r.get("tp2_count"), r.get("tp3_count"), r.get("stop_count"))
print(f"trades total={len(trades)} | trades that hit >=1 TP1 = {n_tp1_trades} | total 止盈 events = {all_tp1_events}")
