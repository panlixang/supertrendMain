"""诊断 CL：43 形态识别页(多) vs 47 首页(空)。只读不写。"""
import json, urllib.request

HOSTS = {
    "43_pattern": "http://43.108.10.84:5174",
    "47_home":    "http://47.84.106.154:5174",
}
NAME = "CL"


def get(url):
    try:
        return json.loads(urllib.request.urlopen(url, timeout=20).read())
    except Exception as e:
        return {"__err__": str(e)}


def find(syms, name):
    for s in syms or []:
        if str(s.get("symbol", "")).upper().startswith(name.upper() + "-"):
            return s
    return None


def safe(obj, key):
    v = obj.get(key) if isinstance(obj, dict) else None
    return v


print("########## 43 形态识别页 (config) ##########")
d = get(HOSTS["43_pattern"] + "/api/pattern/trade/config")
syms = (d.get("symbols") if isinstance(d, dict) else None) or []
s = find(syms, NAME)
if s:
    p = s.get("params") or {}
    print(f"symbol={s.get('symbol')} enabled={s.get('enabled')}")
    print(f"  ST={p.get('periods')}×{p.get('multiplier')}/{p.get('src')} change_atr={p.get('change_atr')}")
    print(f"  filter_v3={s.get('filter_v3')} confirm_close={s.get('confirm_close')} "
          f"block_4h={s.get('block_4h')} signal_max_age={s.get('signal_max_age_sec')}")
    print(f"  exit tp1={s.get('tp1_pct')}/{s.get('tp1_ratio')} sl={s.get('sl_mode')}/{s.get('sl_pct')} "
          f"trail={s.get('trail_with_st')} move2entry={s.get('move_sl_to_entry')}")
else:
    print("  未找到 CL：", d.get("__err__", list(d.keys()) if isinstance(d, dict) else d))

print("  --- state ---")
st = get(HOSTS["43_pattern"] + "/api/pattern/trade/state")
print(f"  running={st.get('running')} 总持仓数={len(st.get('positions', []))}")
poss = [x for x in st.get("positions", []) if str(x.get("symbol", "")).startswith(NAME + "-")] if isinstance(st, dict) else []
print(f"  CL 持仓: {json.dumps(poss, ensure_ascii=False) if poss else '无'}")
ords = [o for o in st.get("orders", []) if str(o.get("symbol", "")).startswith(NAME + "-")] if isinstance(st, dict) else []
print(f"  CL 委托(末10): {json.dumps(ords[-10:], ensure_ascii=False)}")
evs = [e for e in st.get("events", []) if NAME in json.dumps(e, ensure_ascii=False)] if isinstance(st, dict) else []
print(f"  CL 事件(末8): {json.dumps(evs[-8:], ensure_ascii=False)}")

print("\n########## 47 首页 ##########")
d = get(HOSTS["47_home"] + "/api/trade/symbols")
s = find(d.get("symbols"), NAME)
if s:
    p = s.get("params") or {}
    er = s.get("exit_rules") or {}
    print(f"symbol={s.get('symbol')} enabled={s.get('enabled')}")
    print(f"  ST={p.get('periods')}×{p.get('multiplier')}/{p.get('src')}")
    print(f"  score_engine={s.get('score_engine')!r} use_scoring={s.get('use_scoring')} "
          f"thr={s.get('scoring_full_threshold')}/{s.get('scoring_half_threshold')}/"
          f"{s.get('scoring_alert_threshold')} dyn={s.get('use_dynamic_threshold')}")
    print(f"  er_min={s.get('er_min')} er_trend={s.get('er_trend')} "
          f"mtf_filter={s.get('mtf_filter_enabled')} adx_min={s.get('adx_min')}")
    print(f"  exit tp1={er.get('tp1_pct')}/{er.get('tp1_ratio')} sl={er.get('sl_mode')}/{er.get('sl_pct')} "
          f"trail={er.get('trail_with_st')} move2entry={er.get('move_sl_to_entry')}")
else:
    print("  未找到 CL：", d.get("__err__", list(d.keys()) if isinstance(d, dict) else d))

print("  --- positions(raw) ---")
d2 = get(HOSTS["47_home"] + "/api/trade/positions")
print(f"  keys={list(d2.keys()) if isinstance(d2, dict) else type(d2)}")
poss = d2.get("positions") if isinstance(d2, dict) else None
if isinstance(poss, list):
    cl = [x for x in poss if isinstance(x, dict) and str(x.get("symbol", "")).startswith(NAME + "-")]
    print(f"  CL 持仓: {json.dumps(cl, ensure_ascii=False) if cl else '无'}")
else:
    print(f"  positions 字段: {json.dumps(poss, ensure_ascii=False)[:500]}")

print("  --- orders(末15) ---")
d3 = get(HOSTS["47_home"] + "/api/trade/orders?limit=50")
o3 = d3.get("orders", d3) if isinstance(d3, dict) else d3
if isinstance(o3, list):
    cl = [o for o in o3 if isinstance(o, dict) and str(o.get("symbol", "")).startswith(NAME + "-")]
    print(f"  CL 委托(末15): {json.dumps(cl[-15:], ensure_ascii=False)}")
else:
    print(f"  orders: {json.dumps(o3, ensure_ascii=False)[:500]}")
