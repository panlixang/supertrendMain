"""扫 4 个组合，定位 CL 当前持仓到底在哪台/哪个模块。只读。"""
import json, urllib.request

SERVERS = {
    "43": "http://43.108.10.84:5174",
    "47": "http://47.84.106.154:5174",
}
NAME = "CL"


def get(url):
    try:
        return json.loads(urllib.request.urlopen(url, timeout=20).read())
    except Exception as e:
        return {"__err__": str(e)}


def short_pos(d):
    if not isinstance(d, dict):
        return None
    p = d.get("positions")
    if isinstance(p, dict):
        return p.get(NAME + "-USDT-SWAP") or p.get(NAME + "-usdt-swap")
    if isinstance(p, list):
        for x in p:
            if isinstance(x, dict) and str(x.get("symbol", "")).upper().startswith(NAME + "-"):
                return x
    return None


for tag, host in SERVERS.items():
    print(f"\n===== {tag} ({host}) =====")
    # 首页
    d = get(host + "/api/trade/symbols")
    syms = (d.get("symbols") if isinstance(d, dict) else None) or []
    cl = [s for s in syms if str(s.get("symbol", "")).upper().startswith(NAME + "-")]
    print(f"  [首页 trade] CL 配置数={len(cl)} " +
          (f"enabled={cl[0].get('enabled')} ST={cl[0].get('params',{}).get('periods')}×{cl[0].get('params',{}).get('multiplier')}"
           if cl else "无"))
    d2 = get(host + "/api/trade/positions")
    pos = short_pos(d2)
    print(f"  [首页 trade] CL 持仓: {json.dumps(pos, ensure_ascii=False) if pos else '无'}")

    # 形态页
    d3 = get(host + "/api/pattern/trade/config")
    psyms = (d3.get("symbols") if isinstance(d3, dict) else None) or []
    pcl = [s for s in psyms if str(s.get("symbol", "")).upper().startswith(NAME + "-")]
    print(f"  [形态页 pattern] CL 配置数={len(pcl)} " +
          (f"enabled={pcl[0].get('enabled')} ST={pcl[0].get('params',{}).get('periods')}×{pcl[0].get('params',{}).get('multiplier')}"
           if pcl else "无"))
    d4 = get(host + "/api/pattern/trade/state")
    poss = [x for x in (d4.get("positions", []) if isinstance(d4, dict) else [])]
    ppos = [x for x in poss if str(x.get("symbol", "")).startswith(NAME + "-")]
    print(f"  [形态页 pattern] CL 持仓: {json.dumps(ppos, ensure_ascii=False) if ppos else '无'}")
