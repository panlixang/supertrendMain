# -*- coding: utf-8 -*-
"""把弱档(quick)出场规则推送到 43/47 两台生产服务器。

目标设置（用户指定，偏保守，因实盘有滑点）：
  BTC / ETH / SPCX : tp1 = 1.5
  CL  / NVDA       : tp1 = 1.8
  全部 5 个品种      : 弱档止损 sl_pct = 1.5, sl_mode = "pct"

注意杠杆放大：实盘有效止损 = sl_pct × lev_factor
  (1-3x->1.0, 5x->1.5, 10x->2.5, 20x->4.0)，回测用的是 lev=1。

用法：
  python apply_weak_profile.py            # 默认 dry-run，只打印将要改什么
  python apply_weak_profile.py --apply    # 真正 POST 并回读验证

仅对“已在该节点配置”的品种下发；节点上没有的品种自动跳过并提示。
若 quick_enabled 当前为 False，apply 时一并开启（否则弱档不生效）。
"""
from __future__ import annotations
import json
import sys
import urllib.request

SERVERS = {
    "43": "http://43.108.10.84:5174",
    "47": "http://47.84.106.154:5174",
}
# 目标品种 -> tp1
TARGETS = {"BTC": 1.5, "ETH": 1.5, "SPCX": 1.5, "NVDA": 1.8, "CL": 1.8}
SL = 1.5


def _req(url, payload=None, timeout=25):
    if payload is None:
        req = urllib.request.Request(url, headers={"User-Agent": "cfg/1.0"})
    else:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", "User-Agent": "cfg/1.0"},
            method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def find_sym(symbols, name):
    for s in symbols:
        if s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "") == name:
            return s
    return None


def quick_patch(tp1):
    return {
        "profile": "quick",
        "enabled": True,
        "tp1_pct": tp1, "tp1_ratio": 100.0,
        "tp2_pct": 999.0, "tp2_ratio": 0.0,
        "tp3_pct": 999.0, "tp3_ratio": 0.0,
        "tp3_mode": "pct",
        "move_sl_to_entry": False, "trail_with_st": False,
        "sl_mode": "pct", "sl_pct": SL,
        "sl_buffer_atr": 0.3, "sl_min_pct": SL,
        "protect_profit_at": 999.0, "protect_trail_pct": 0.0,
        "max_loss_enabled": False, "max_loss_pct": 10.0,
    }


def lev_factor(lev):
    if lev >= 20: return 4.0
    if lev >= 10: return 2.5
    if lev >= 5:  return 1.5
    return 1.0


def main():
    apply = "--apply" in sys.argv
    print(f"MODE: {'APPLY (真实下发)' if apply else 'DRY-RUN (不改任何东西)'}")
    for node, base in SERVERS.items():
        try:
            g = _req(f"{base}/api/trade/config")
        except Exception as e:
            g = None
        if g:
            print(f"[全局 {node}] enabled={g.get('enabled')} paper={g.get('paper')} "
                  f"score_engine={g.get('score_engine')}")
        try:
            data = _req(f"{base}/api/trade/symbols")
        except Exception as e:
            print(f"[{node}] GET 失败: {e}"); continue
        symbols = data.get("symbols", [])
        print(f"\n===== 节点 {node} ({base}) 共 {len(symbols)} 个品种 =====")
        for name, tp1 in TARGETS.items():
            s = find_sym(symbols, name)
            if not s:
                print(f"  [{node}] {name}: 该节点无此品种 -> 跳过")
                continue
            lev = s.get("leverage")
            qe = s.get("quick_enabled")
            qr = s.get("exit_rules_quick") or {}
            ewm = s.get("er_weak_min")
            emn = s.get("er_min")
            eff = round(SL * lev_factor(lev or 1), 2)
            print(f"  [{node}] {name} lev={lev} quick_enabled={qe} "
                  f"er_weak_min={ewm} er_min={emn}")
            print(f"          当前quick: tp1={qr.get('tp1_pct')} enabled={qr.get('enabled')} "
                  f"sl={qr.get('sl_pct')}/{qr.get('sl_mode')} sl_min={qr.get('sl_min_pct')}")
            print(f"          -> 目标: tp1={tp1} sl={SL}(pct) | 实盘有效止损≈{eff}%")
            if not apply:
                continue
            # 仅写入弱档出场规则(tp1/sl)。不自动打开 quick_enabled：
            # 当前 quick_enabled=False => 这些设置会写入但暂不起交易作用，
            # 待用户确认开启总闸后再生效(实盘账户, 需谨慎)。
            payload = {"symbol": s["symbol"], **quick_patch(tp1)}
            r2 = _req(f"{base}/api/trade/exit-rules", payload)
            print(f"     [exit-rules] ok={r2.get('ok')} "
                  f"{'' if r2.get('ok') else r2.get('error')}")
            # 验证回读
            after = find_sym(_req(f"{base}/api/trade/symbols").get("symbols", []), name)
            aq = (after or {}).get("exit_rules_quick") or {}
            print(f"     [verify] tp1={aq.get('tp1_pct')} enabled={aq.get('enabled')} "
                  f"sl={aq.get('sl_pct')}/{aq.get('sl_mode')} sl_min={aq.get('sl_min_pct')} "
                  f"quick_enabled={after.get('quick_enabled') if after else '?'}")
            if not qe:
                print(f"     [WARN] quick_enabled=False => 已写入但弱档暂不下单；"
                      f"要生效需另开总闸(实盘弱ER自动交易)")
    print("\n完成。")


if __name__ == "__main__":
    main()
