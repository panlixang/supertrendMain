# -*- coding: utf-8 -*-
"""把 SPCX-USDT-SWAP 作为新品种加到 47 服务器（47.84.106.154:5174）形态识别页。

出场参数取自 bt_spcx_optimize.py 寻优选中档（Calmar 2.54）：
    tp1=3.0/50  sl=st(兜底2%)  trail=False  move_sl_to_entry=True  reverse_close=False
    filter_v3=True（V3+形态识别页）  exit_mode=single（仅 TP1 单档，与回测一致）

非出场字段沿 47 现有风格：margin_usdt=60 / leverage=10 / sizing_mode=fixed / equity_pct=10。

机制：POST /api/pattern/trade/symbols action=add —— add_symbol 内部 self.save() 落盘，
      且 _sync() 即时生效（运行实例下一轮轮询即开始监听，无需重启）。
"""
import json
import urllib.request

HOST = "http://47.84.106.154:5174"
HDR = {"User-Agent": "apply/1.0", "Content-Type": "application/json"}


def _http(method, path, body=None):
    req = urllib.request.Request(
        HOST + path,
        data=(json.dumps(body).encode() if body is not None else None),
        headers=HDR, method=method)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def get_config():
    return _http("GET", "/api/pattern/trade/config")


SPEC = {
    "SPCX-USDT-SWAP": dict(
        enabled=True,
        margin_usdt=60.0, leverage=10, sizing_mode="fixed", equity_pct=10.0,
        allow_tfs=["1h"],
        tp1_pct=3.0, tp1_ratio=50.0,
        tp2_pct=None, tp2_ratio=None, tp3_pct=None, tp3_ratio=None, tp3_mode=None,
        exit_mode="single",
        sl_pct=2.0, sl_mode="st",
        move_sl_to_entry=True, trail_with_st=False,
        reverse_close=False, filter_v3=True,
    ),
}


def row(s):
    return (f"tp1={s.get('tp1_pct')}/{s.get('tp1_ratio')} sl={s.get('sl_mode')}/"
            f"{s.get('sl_pct')} exit={s.get('exit_mode')} v3={s.get('filter_v3')} "
            f"trail={s.get('trail_with_st')} be={s.get('move_sl_to_entry')} "
            f"tfs={','.join(s.get('allow_tfs') or [])} "
            f"margin={s.get('margin_usdt')} lev={s.get('leverage')}")


cfg = get_config()
have = {s["symbol"]: s for s in (cfg.get("symbols") or [])}
# 备份改动前配置
json.dump(cfg, open("_cfg_47_before_spcx.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("47 改动前品种:", sorted(have))
print("已备份到 _cfg_47_before_spcx.json")

for sym, kw in SPEC.items():
    exists = sym in have
    action = "update" if exists else "add"
    if exists:
        print(f"\n>>> {sym} 已存在，改前: {row(have[sym])}")
    else:
        print(f"\n>>> {sym} 不存在，【新增】")
    body = {"action": action, "symbol": sym, **kw}
    print(f"    POST {action} 目标: {row(kw)}")
    try:
        r = _http("POST", "/api/pattern/trade/symbols", body)
        print("    ok=%s error=%s" % (r.get("ok"), r.get("error", "")))
    except Exception as e:
        print("    ERROR", repr(e))

print("\n==== 改后验证 ====")
cfg = get_config()
for s in cfg.get("symbols") or []:
    if s["symbol"] in SPEC:
        print("%-20s en=%s %s" % (s["symbol"], s["enabled"], row(s)))
print("\n注：enabled=True 即会开始在 47 的 OKX 账户上按此参数实盘监听/下单；"
      "若要灰度可先在页面把 enabled 关掉。样本仅 ~5 个月，参数过拟合风险高，建议小仓位灰度。")
