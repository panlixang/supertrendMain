"""真实 4 年 OKX 1h 数据 + 真实管线 build_dataset 训练。
方向：regime 过滤 × 结构化 TP/SL 标签（tpsl）。
- 复用市场状态分类（classify_market_regime）的 regime_cn / tradeable；
- 剔除「震荡无序期」= 假突破期(flip≥5) + 震荡吸收期(默认)，即反复跳 ST 信号的噪声段；
- 对每个 tpsl 配置报告：全量 / v3_pass / 剔除震荡无序 / 各 regime 明细。
"""
import json
from collections import defaultdict
import asyncio
import history
import strategy_learning as sl

SYM = "BTC-USDT"
LIMIT = 36500  # ~4.17 年 1h

# 震荡无序期：由新 regime 分类器标 is_disorder=True（反复跳 ST 信号、无趋势无波动）
def is_choppy(r):
    return bool(r.get("is_disorder"))


def load_cached():
    path = "backtest/btc_1h_full_fetched.json"
    try:
        d = json.load(open(path, "r", encoding="utf-8"))
        bcandles = d.get("base") or d.get("candles") or []
        if bcandles:
            print(f"已从本地加载 {len(bcandles)} 根 1h K 线（{path}）")
            sl._CANDLE_CACHE[f"{SYM}|1h|{LIMIT}"] = bcandles
            return True
    except Exception as e:
        print(f"本地文件读取失败：{e}")
    raw = history.fetch_candles("1h", LIMIT, SYM)
    if not raw:
        print("拉取失败：返回空"); return False
    bcandles = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol}
                for c in raw]
    print(f"已拉取 {len(bcandles)} 根，区间 {raw[0].ts} ~ {raw[-1].ts}")
    json.dump({"base": bcandles}, open(path, "w"), ensure_ascii=False)
    sl._CANDLE_CACHE[f"{SYM}|1h|{LIMIT}"] = bcandles
    return True


def report(rows, tag):
    if not rows:
        print(f"  {tag}: 无样本，跳过"); return
    n_v3 = sum(1 for r in rows if r.get("v3_pass"))
    n_win = sum(r["win"] for r in rows)
    print(f"  {tag}: 样本={len(rows)}  v3_pass={n_v3}  胜率={n_win / len(rows) * 100:.1f}%")
    if len(rows) < 20:
        print("    样本<20，跳过训练"); return
    r = sl.train_lightgbm(rows, "cls")
    m = r["metrics"]
    print(f"    分类 acc={m['accuracy']:.3f}  f1={m['f1']:.3f}  auc={m['auc']:.3f}  "
          f"(n_test={m['n_test']})")
    print("    特征重要性Top8: " + "  ".join(
        f"{d['cn']}={d['ratio']*100:.1f}%" for d in r["importance"][:8]))


def report_ts(rows, tag):
    """时间外验证：按时间顺序切分（前70%训练 / 后30%测试），看随机切分的 AUC 是否为分桶选择偏差。"""
    if not rows:
        print(f"  {tag}[时间外]: 无样本，跳过"); return
    if len(rows) < 40:
        print(f"  {tag}[时间外]: 样本={len(rows)} <40，跳过"); return
    r = sl.train_lightgbm(rows, "cls", time_split=True)
    m = r["metrics"]
    print(f"  {tag}[时间外]: auc={m['auc']:.3f}  acc={m['accuracy']:.3f}  "
          f"(n_train={m['n_train']} n_test={m['n_test']})")
    r2 = sl.train_lightgbm(rows, "reg")
    m2 = r2["metrics"]
    print(f"    回归 mae={m2['mae']:.3f}  rmse={m2['rmse']:.3f}  r2={m2['r2']:.3f}")


def regime_breakdown(rows):
    buckets = defaultdict(list)
    for r in rows:
        buckets[r.get("regime6") or "unknown"].append(r)
    print("  -- regime breakdown (n / win% / AUC) --")
    for name, rs in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        n = len(rs); nw = sum(x["win"] for x in rs)
        wr = nw / n * 100 if n else 0.0
        if n >= 40:
            try:
                rr = sl.train_lightgbm(rs, "cls"); a = rr["metrics"]["auc"]
                extra = f"  auc={a:.3f}"
            except Exception:
                extra = "  auc=NA"
        else:
            extra = "  (n<40 skip)"
        print(f"    {name}: n={n}  win={wr:.1f}%{extra}")


async def run_tpsl(horizon, tp_pct, sl_pct):
    tag = f"tpsl tp={tp_pct} sl={sl_pct} h={horizon}"
    print(f"\n########## {tag} ##########")
    ds = await sl.build_dataset(symbol=SYM, base_tf="1h", limit=LIMIT,
                                years=None, label_mode="tpsl", horizon=horizon,
                                tp_pct=tp_pct, sl_pct=sl_pct)
    if ds is None:
        print("build_dataset 返回 None"); return
    rows = ds["rows"]
    print(f"信号总数={len(rows)}")
    report(rows, "【全量】")
    report([r for r in rows if r.get("v3_pass")], "【仅 v3_pass】")
    nonchop = [r for r in rows if not is_choppy(r)]
    report(nonchop, "【剔除震荡无序期】")
    trend_v3 = [r for r in rows if r["regime6"] == "trend_run" and r.get("v3_pass")]
    report(trend_v3, "【趋势 + v3_pass】")
    good = [r for r in rows if r["regime6"] in ("trend_end", "range_start")]
    report(good, "【仅高AUC regime: trend_end+range_start】")
    print("  —— 时间外验证（前70%训练/后30%测试）——")
    report_ts(rows, "全量")
    report_ts([r for r in rows if r.get("v3_pass")], "v3_pass")
    report_ts(nonchop, "剔除震荡无序期")
    report_ts([r for r in rows if r["regime6"] == "trend_end"], "trend_end(趋势末期)")
    report_ts([r for r in rows if r["regime6"] == "choppy_disorder"], "choppy_disorder(震荡无序期)")
    regime_breakdown(rows)


async def main():
    if not load_cached():
        return
    sl._DS_CACHE.clear()  # 分类器已改，强制重新构建数据集
    # 结构化 TP/SL 标签 × regime 过滤
    for (tp, sl_pct, h) in [(2.5, 2.0, 30), (3.0, 2.0, 50), (2.0, 1.5, 20)]:
        await run_tpsl(h, tp, sl_pct)


if __name__ == "__main__":
    asyncio.run(main())
    print("\n完成。")
