"""GET 43/47 首页 /api/trade/symbols，提取 INTC/AMD 完整配置并留底。"""
import json, urllib.request, datetime

HOSTS = {"43": "http://43.108.10.84:5174", "47": "http://47.84.106.154:5174"}
NAMES = ["INTC", "AMD"]
out = {}
for tag, host in HOSTS.items():
    try:
        d = json.loads(urllib.request.urlopen(host + "/api/trade/symbols", timeout=20).read())
    except Exception as e:
        print(f"[{tag}] 拉取失败 {e}")
        continue
    for s in d.get("symbols") or []:
        if any(s["symbol"].upper().startswith(n + "-") for n in NAMES):
            out.setdefault(tag, {})[s["symbol"]] = s
            p = s.get("params") or {}
            er = s.get("exit_rules") or {}
            print(f"[{tag}] {s['symbol']} enabled={s.get('enabled')} "
                  f"ST={p.get('periods')}×{p.get('multiplier')}/{p.get('src')} "
                  f"thr={s.get('scoring_full_threshold')}/{s.get('scoring_half_threshold')}/"
                  f"{s.get('scoring_alert_threshold')} dyn={s.get('use_dynamic_threshold')} "
                  f"se={s.get('score_engine')!r}")
            print(f"        exit tp1={er.get('tp1_pct')}/{er.get('tp1_ratio')} "
                  f"sl={er.get('sl_mode')}/{er.get('sl_pct')} trail={er.get('trail_with_st')} "
                  f"move2entry={er.get('move_sl_to_entry')} tp2={er.get('tp2_pct')}/{er.get('tp2_ratio')} "
                  f"tp3={er.get('tp3_pct')}/{er.get('tp3_ratio')}/{er.get('tp3_mode')}")
if out:
    fn = "_revert_INTC_AMD_before_%s.json" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    json.dump(out, open(fn, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n[备份] 已保存当前配置 → {fn}（如需回滚用此文件）")
else:
    print("未找到 INTC/AMD，请检查服务器")
