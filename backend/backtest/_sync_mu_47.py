# -*- coding: utf-8 -*-
"""把 47 节点 MU-USDT-SWAP 的出场规则同步成 43 节点的值。

仅下发与 43 不同的字段（normal: max_loss_enabled; quick: 4 处），
POST /api/trade/exit-rules 后重新 GET 验证一致性。
"""
import json
import urllib.request

L43 = "http://43.108.10.84:5174"
L47 = "http://47.84.106.154:5174"
SYM = "MU-USDT-SWAP"


def _get(base):
    with urllib.request.urlopen(base + "/api/trade/symbols", timeout=25) as r:
        return json.loads(r.read())


def _mu(d):
    return next(s for s in d["symbols"] if s["symbol"] == SYM)


def _post(profile, patch):
    body = {"symbol": SYM, "profile": profile, **patch}
    req = urllib.request.Request(
        L47 + "/api/trade/exit-rules",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def main():
    a = _mu(_get(L43))   # 源：43
    b = _mu(_get(L47))   # 目标：47

    # normal 档差异（影响实盘）
    normal_patch = {}
    if a["exit_rules"]["max_loss_enabled"] != b["exit_rules"]["max_loss_enabled"]:
        normal_patch["max_loss_enabled"] = a["exit_rules"]["max_loss_enabled"]

    # quick 档差异（MU quick_enabled=False，不生效，仅对齐配置）
    quick_fields = ("tp2_pct", "trail_with_st", "protect_profit_at", "tp3_pct")
    quick_patch = {}
    for f in quick_fields:
        if a["exit_rules_quick"][f] != b["exit_rules_quick"][f]:
            quick_patch[f] = a["exit_rules_quick"][f]

    print("== 将下发的差异 ==")
    print("  normal:", normal_patch)
    print("  quick :", quick_patch)

    if normal_patch:
        resp = _post("normal", normal_patch)
        print("  POST normal ->", resp.get("ok"), resp.get("error", ""))
    else:
        print("  normal 已一致，无需改")

    if quick_patch:
        resp = _post("quick", quick_patch)
        print("  POST quick  ->", resp.get("ok"), resp.get("error", ""))
    else:
        print("  quick 已一致，无需改")

    # 验证
    c = _mu(_get(L47))
    print("\n== 47 改后验证 ==")
    print("  normal.max_loss_enabled =", c["exit_rules"]["max_loss_enabled"],
          "(目标", a["exit_rules"]["max_loss_enabled"], ")")
    print("  quick =", {f: c["exit_rules_quick"][f] for f in quick_fields},
          "(目标", {f: a["exit_rules_quick"][f] for f in quick_fields}, ")")


if __name__ == "__main__":
    main()
