"""真实 4 年 OKX 1h 数据 + 真实管线 build_dataset 训练。
对比「全量」与「仅 v3_pass 子集」两种信号集，看过滤假信号后预测力是否出来。
标签：fwd（未来 N 根方向收益）+ exit（tp1 1.5% 平 70% + 反向）对照。
"""
import json
import asyncio
import history
import strategy_learning as sl

SYM = "BTC-USDT"
LIMIT = 36500  # ~4.17 年 1h


def load_cached():
    raw = history.fetch_candles("1h", LIMIT, SYM)
    if not raw:
        print("拉取失败：返回空"); return False
    bcandles = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol}
                for c in raw]
    print(f"已加载 {len(bcandles)} 根，区间 {raw[0].ts} ~ {raw[-1].ts}")
    json.dump({"base": bcandles},
              open("backtest/btc_1h_full_fetched.json", "w"), ensure_ascii=False)
    sl._CANDLE_CACHE[f"{SYM}|1h|{LIMIT}"] = bcandles
    return True


def report(rows, tag):
    if not rows:
        print(f"  {tag}: 无样本，跳过"); return
    n_v3 = sum(1 for r in rows if r["v3_pass"])
    n_win = sum(r["win"] for r in rows)
    print(f"  {tag}: 样本={len(rows)}  v3_pass={n_v3}  胜率={n_win / len(rows) * 100:.1f}%")
    r = sl.train_lightgbm(rows, "cls")
    m = r["metrics"]
    print(f"    分类 acc={m['accuracy']:.3f}  f1={m['f1']:.3f}  auc={m['auc']:.3f}  "
          f"(n_test={m['n_test']})")
    print("    特征重要性Top8: " + "  ".join(
        f"{d['cn']}={d['ratio']*100:.1f}%" for d in r["importance"][:8]))
    r2 = sl.train_lightgbm(rows, "reg")
    m2 = r2["metrics"]
    print(f"    回归 mae={m2['mae']:.3f}  rmse={m2['rmse']:.3f}  r2={m2['r2']:.3f}")


async def run(label_mode, horizon):
    tag = f"{label_mode}{('-h' + str(horizon)) if label_mode == 'fwd' else ''}"
    print(f"\n########## {tag} ##########")
    ds = await sl.build_dataset(symbol=SYM, base_tf="1h", limit=LIMIT,
                                years=None, label_mode=label_mode, horizon=horizon)
    if ds is None:
        print("build_dataset 返回 None"); return
    rows = ds["rows"]
    print(f"信号总数={len(rows)}")
    report(rows, "【全量】")
    report([r for r in rows if r["v3_pass"]], "【仅 v3_pass】")


async def main():
    if not load_cached():
        return
    for h in (10, 20, 50):
        await run("fwd", h)
    await run("exit", 0)


if __name__ == "__main__":
    asyncio.run(main())
    print("\n完成。全量 1h 已缓存至 backtest/btc_1h_full_fetched.json")
