"""拉取 43/47 两台服务器、首页 + 形态页的已配置品种及关键参数。"""
import json, urllib.request

HOSTS = {"43": "http://43.108.10.84:5174", "47": "http://47.84.106.154:5174"}


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=20).read())


for tag, host in HOSTS.items():
    print("=" * 70)
    print(f"服务器 {tag}  {host}")
    # 形态页
    try:
        d = get(host + "/api/pattern/trade/config")
        cfg = d.get("cfg") or {}
        syms = d.get("symbols") or []
        print(f"\n--- 形态识别页 (paper={cfg.get('paper')}, block_4h={cfg.get('block_4h')}, filter_v3默认={cfg.get('filter_v3')}) ---")
        for s in syms:
            if s.get("enabled"):
                print(f"  {s['symbol']:<22} lev={s.get('leverage')} tf={s.get('allow_tfs')} "
                      f"tp1={s.get('tp1_pct')}/{s.get('tp1_ratio')} sl={s.get('sl_mode')}/{s.get('sl_pct')} "
                      f"trail={s.get('trail_with_st')} be={s.get('move_sl_to_entry')} v3={s.get('filter_v3')}")
    except Exception as e:
        print(f"  形态页拉取失败: {e}")
    # 首页
    try:
        d = get(host + "/api/trade/symbols")
        syms = d.get("symbols") or []
        print(f"\n--- 首页 (paper={d.get('paper')}, periods={d.get('params',{}).get('periods')} "
              f"x{d.get('params',{}).get('multiplier')}) ---")
        for s in syms:
            if s.get("enabled"):
                er = s.get("exit_rules") or {}
                print(f"  {s['symbol']:<22} lev={s.get('leverage')} "
                      f"gate={s.get('er_min')} tp1={er.get('tp1_pct')}/{er.get('tp1_ratio')} "
                      f"sl={er.get('sl_mode')}/{er.get('sl_pct')} trail={er.get('trail_with_st')} "
                      f"be={er.get('move_sl_to_entry')}")
    except Exception as e:
        print(f"  首页拉取失败: {e}")
