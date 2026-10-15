# -*- coding: utf-8 -*-
"""深度诊断：AMD 10-14 12:00 空信号为何没有触发"""
import os, sys, json, urllib.request
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backtest"))
from engine import run_core, _extract

BASE43 = "http://43.108.10.84:5174"

def get43(endpoint):
    try:
        resp = urllib.request.urlopen(BASE43 + endpoint, timeout=15)
        return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}

def pull_bitget(symbol, gran, limit=500):
    """从Bitget拉取K线数据"""
    url = (f"https://api.bitget.com/api/v2/mix/market/candles"
           f"?symbol={symbol}&productType=usdt-futures&granularity={gran}&limit={limit}")
    rows = json.loads(urllib.request.urlopen(url, timeout=20).read()).get("data", [])
    return [{"ts": int(x[0]), "o": float(x[1]), "h": float(x[2]),
             "l": float(x[3]), "c": float(x[4]), "vol": float(x[5])} for x in rows]

# 1. 获取43配置
print("=" * 80)
print("第一步：获取43服务器AMD配置")
print("=" * 80)
symbols_data = get43("/api/trade/symbols")
amd_cfg = None
for s in symbols_data.get("symbols", []):
    if s.get("symbol") == "AMD-USDT-SWAP":
        amd_cfg = s
        break

if not amd_cfg:
    print("错误：未找到AMD配置")
    sys.exit(1)

print(f"AMD配置状态: enabled={amd_cfg['enabled']}")
print(f"评分系统: use_scoring={amd_cfg['use_scoring']}, threshold={amd_cfg['scoring_full_threshold']}")

# 2. 拉取历史K线数据
print("\n" + "=" * 80)
print("第二步：拉取Bitget历史数据（覆盖10-14）")
print("=" * 80)
CANDLES = {}
for tf, gran in [("1h", 3600), ("15m", 900), ("4h", 14400)]:
    CANDLES[tf] = pull_bitget("AMDUSDT", gran, limit=500)
    first_time = datetime.fromtimestamp(CANDLES[tf][0]["ts"]/1000, tz=timezone.utc)
    last_time = datetime.fromtimestamp(CANDLES[tf][-1]["ts"]/1000, tz=timezone.utc)
    print(f"{tf}: {len(CANDLES[tf])} 根, {first_time.strftime('%Y-%m-%d %H:%M')} ~ {last_time.strftime('%Y-%m-%d %H:%M')}")

# 3. 用43配置运行本地引擎
print("\n" + "=" * 80)
print("第三步：用43配置在本地运行回测引擎")
print("=" * 80)
SC, PR, ER = _extract(amd_cfg)
result = run_core("AMD-USDT-SWAP", SC, PR, ER, tf="1h", candles=CANDLES)

# 4. 查找10-14的交易
print("\n10-14 当天交易记录:")
t_1014_start = int(datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
t_1014_end = int(datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

trades_1014 = [t for t in result["trades"]
               if (t_1014_start <= t["entry_ts"] < t_1014_end)
               or (t.get("exit_ts") and t_1014_start <= t.get("exit_ts") < t_1014_end)]

if trades_1014:
    for t in trades_1014:
        entry_time = datetime.fromtimestamp(t["entry_ts"]/1000, tz=timezone.utc)
        exit_time = datetime.fromtimestamp(t.get("exit_ts", 0)/1000, tz=timezone.utc) if t.get("exit_ts") else None
        print(f"  {entry_time.strftime('%Y-%m-%d %H:%M')} {t['side']} @ {t['entry']:.2f}")
        if exit_time:
            print(f"    -> {exit_time.strftime('%Y-%m-%d %H:%M')} 平仓 @ {t.get('exit', 0):.2f}, 盈亏={t.get('pnl', 0):.2f}, 原因={t.get('reason', '')}")
else:
    print("  本地引擎：10-14 无交易")

# 5. 检查10-14的信号
print("\n10-14 当天信号:")
if "signals" in result:
    signals_1014 = [s for s in result.get("signals", []) if t_1014_start <= s["ts"] < t_1014_end]
    if signals_1014:
        for s in signals_1014:
            sig_time = datetime.fromtimestamp(s["ts"]/1000, tz=timezone.utc)
            print(f"  {sig_time.strftime('%Y-%m-%d %H:%M')} {s['type']} @ {s['price']:.2f}")
            print(f"    grade={s.get('grade')}, score={s.get('score')}, hidden={s.get('hidden')}, will_trade={s.get('will_trade')}")
            if s.get('gate_reasons'):
                print(f"    拒绝原因: {s.get('gate_reasons')}")
    else:
        print("  无信号")

# 6. 检查10-12到10-14之间的持仓状态
print("\n" + "=" * 80)
print("第四步：检查10-12平仓后的状态")
print("=" * 80)
t_1012_start = int(datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
trades_1012_1014 = [t for t in result["trades"]
                    if t_1012_start <= t["entry_ts"] < t_1014_end]

print(f"\n10-12 到 10-14 期间所有交易 ({len(trades_1012_1014)} 笔):")
for t in trades_1012_1014:
    entry_time = datetime.fromtimestamp(t["entry_ts"]/1000, tz=timezone.utc)
    exit_time = datetime.fromtimestamp(t.get("exit_ts", 0)/1000, tz=timezone.utc) if t.get("exit_ts") else None
    print(f"  {entry_time.strftime('%m-%d %H:%M')} {t['side']:5s} @ {t['entry']:7.2f}", end="")
    if exit_time:
        print(f" -> {exit_time.strftime('%m-%d %H:%M')} @ {t.get('exit', 0):7.2f} | pnl={t.get('pnl', 0):6.2f} | {t.get('reason', '')}")
    else:
        print(" -> [持仓中]")

# 7. 检查cooldown限制
print("\n" + "=" * 80)
print("第五步：检查cooldown冷却时间")
print("=" * 80)
cooldown_sec = amd_cfg.get("cooldown_sec", 0)
print(f"配置的cooldown: {cooldown_sec} 秒 ({cooldown_sec/60:.1f} 分钟)")

# 找到10-12平仓的那笔交易
history = get43("/api/trade/history?sym=AMD-USDT-SWAP&recent=10")
if history.get("recent"):
    last_trade = history["recent"][0]
    exit_ts = last_trade.get("exit_ts")
    if exit_ts:
        exit_time = datetime.fromtimestamp(exit_ts/1000, tz=timezone.utc)
        target_time = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
        time_diff = (target_time - exit_time).total_seconds()

        print(f"\n上一笔平仓时间: {exit_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"目标信号时间: {target_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"时间间隔: {time_diff:.0f} 秒 ({time_diff/3600:.1f} 小时)")
        print(f"Cooldown要求: {cooldown_sec} 秒")

        if time_diff < cooldown_sec:
            print(f"\n>>> 发现问题！信号在cooldown冷却期内被拒绝！")
            print(f"    还需要等待: {(cooldown_sec - time_diff)/60:.1f} 分钟")
        else:
            print(f"\n已超过cooldown时间，不是cooldown问题")

print("\n" + "=" * 80)
print("诊断完成")
print("=" * 80)
