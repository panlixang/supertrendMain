# -*- coding: utf-8 -*-
"""一次性核查 43 / 47 两台服务器的【首页】(trade) 与【形态识别】(pattern) 品种配置。

预期分配：
  首页(home)  = NVDA CL MU SNDK SKHYNIX ETH INTC AMD BTC XAU  (10 个，做主力)
  形态(pattern)= TSLA SPX  (留在形态识别页)
即：TSLA / SPX 不应出现在任何首页接口；10 个主力不应出现在形态接口（形态接口可只有 TSLA/SPX）。
"""
import json
import urllib.request

HOSTS = {
    "43": "http://43.108.10.84:5174",
    "47": "http://47.84.106.154:5174",
}


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "chk/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def base_name(sym):
    return sym.replace("-USDT-SWAP", "").replace("-USDT", "")


def home_row(s):
    p = s.get("params") or {}
    er = s.get("exit_rules") or {}
    return (f"en={s.get('enabled')} ST={p.get('periods')}x{p.get('multiplier')}/{p.get('src')} "
            f"se={s.get('score_engine')!r} thr={s.get('scoring_full_threshold')} "
            f"range={s.get('range_filter_enabled')} "
            f"exit tp1={er.get('tp1_pct')}/{er.get('tp1_ratio')} "
            f"sl={er.get('sl_pct')}({er.get('sl_mode')}) trail={er.get('trail_with_st')}")


def pat_row(s):
    return (f"en={s.get('enabled')} tp1={s.get('tp1_pct')}/{s.get('tp1_ratio')} "
            f"sl={s.get('sl_pct')}({s.get('sl_mode')}) exit={s.get('exit_mode')} "
            f"v3={s.get('filter_v3')} trail={s.get('trail_with_st')} "
            f"be={s.get('move_sl_to_entry')} rc={s.get('reverse_close')} "
            f"tfs={','.join(s.get('allow_tfs') or [])} margin={s.get('margin_usdt')} lev={s.get('leverage')}")


def main():
    for tag, host in HOSTS.items():
        print("=" * 92)
        print(f"[{tag} 服务器] {host}")
        print("=" * 92)

        # ── 首页 (home) ──
        print("\n── 首页 /api/trade/symbols ──")
        try:
            d = _get(f"{host}/api/trade/symbols")
            syms = d.get("symbols") or []
        except Exception as e:
            print("   !! 拉取失败:", e)
            syms = []
        home = {base_name(s["symbol"]): s for s in syms}
        if not home:
            print("   (空)")
        for nm in sorted(home):
            print(f"   {nm:<10} {home_row(home[nm])}")

        # ── 形态识别 (pattern) ──
        print("\n── 形态识别 /api/pattern/trade/symbols ──")
        pat = {}
        try:
            try:
                d = _get(f"{host}/api/pattern/trade/symbols")
                syms = d.get("symbols") or []
            except Exception:
                d = _get(f"{host}/api/pattern/trade/config")
                syms = d.get("symbols") or []
            pat = {base_name(s["symbol"]): s for s in syms}
        except Exception as e:
            print("   !! 拉取失败:", e)
        if not pat:
            print("   (空)")
        for nm in sorted(pat):
            print(f"   {nm:<10} {pat_row(pat[nm])}")

    # ── 汇总分配核对 ──
    print("\n" + "=" * 92)
    print("【分配核对】")
    print("=" * 92)
    HOME_WANT = {"NVDA", "CL", "MU", "SNDK", "SKHYNIX", "ETH", "INTC", "AMD", "BTC", "XAU"}
    PAT_WANT = {"TSLA", "SPX"}

    all_home, all_pat = set(), set()
    raw = {}
    for tag, host in HOSTS.items():
        # 重新快速拉取（不打印）
        try:
            d = _get(f"{host}/api/trade/symbols")
            for s in (d.get("symbols") or []):
                if s.get("enabled"):
                    all_home.add(base_name(s["symbol"]))
        except Exception:
            pass
        try:
            try:
                d = _get(f"{host}/api/pattern/trade/symbols")
            except Exception:
                d = _get(f"{host}/api/pattern/trade/config")
            for s in (d.get("symbols") or []):
                if s.get("enabled"):
                    all_pat.add(base_name(s["symbol"]))
        except Exception:
            pass

    print(f"  首页(两页合计, enabled) = {sorted(all_home)}")
    print(f"  形态(两页合计, enabled) = {sorted(all_pat)}")
    print(f"  预期首页 = {sorted(HOME_WANT)}")
    print(f"  预期形态 = {sorted(PAT_WANT)}")
    print()
    print(f"  首页缺：{sorted(HOME_WANT - all_home) or '无'}")
    print(f"  首页多出(不在预期10里)：{sorted(all_home - HOME_WANT) or '无'}")
    print(f"  TSLA/SPX 是否混进首页：{sorted({'TSLA','SPX'} & all_home) or '无 ✅'}")
    print(f"  形态缺：{sorted(PAT_WANT - all_pat) or '无'}")
    print(f"  10主力是否误进形态：{sorted(HOME_WANT & all_pat) or '无 ✅'}")


if __name__ == "__main__":
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
