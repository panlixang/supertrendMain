# -*- coding: utf-8 -*-
"""回测数据清理：保留 .py 脚本 + 全部原始 K 线数据，删除其他回测数据产物。

保留（KEEP）：
  - 所有 *.py / *.pine / *.sh / *.bat（脚本/代码）
  - 根目录 *.md 文档
  - 原始 K 线：candle_data.db、backend/_bt2026_data/*_USDT_SWAP.json、
    backend/backtest/_live_data/*、backend/backtest/btc_*_full.json、
    根目录 btc_*_2019_2026.csv / btc_*_2026.csv / btc_1d_*.csv
  - 线上配置 *.json（pattern_trade.json / *credentials.json 等，非 _ 前缀）
  - 实盘成交记录 logs/（如有）

删除（DEL）：
  - *.txt *.log *.jsonl *.png *.xlsx *.csv（回测日志/图表/结果）
  - _ 前缀的 *.json、backtest/ 下 st_*.json（配置快照/计算结果）
  - backtest/ 下的报告 *.md

默认干跑（只打印），加 --apply 才真正删除。
"""
import os, sys

ROOT = r"d:/个人项目代码/supertrendMain"
BACKEND = os.path.join(ROOT, "backend")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".idea", ".vscode"}


def is_raw_candle(path):
    name = os.path.basename(path)
    p = path.replace("\\", "/")
    if name == "candle_data.db":
        return True
    if "_bt2026_data" in p.split("/") and name.endswith("_USDT_SWAP.json"):
        return True
    if "/backtest/_live_data/" in p:
        return True
    if p.startswith(ROOT + "/") and os.path.dirname(path) == ROOT:
        if name.startswith("btc_") and name.endswith(".csv") and "trades" not in name:
            return True
    if "/backtest/" in p and name.startswith("btc_") and name.endswith("_full.json"):
        return True
    return False


def classify(path):
    name = os.path.basename(path)
    low = name.lower()
    d = os.path.dirname(path)
    p = path.replace("\\", "/")
    if low.endswith((".py", ".pine", ".sh", ".bat")):
        return "KEEP"
    if name in ("requirements.txt", "ml_requirements.txt"):
        return "KEEP"
    if d == ROOT and low.endswith(".md"):
        return "KEEP"
    if "/logs/" in p:
        return "KEEP"
    if is_raw_candle(path):
        return "KEEP"
    # 删除回测产物
    if low.endswith((".txt", ".log", ".jsonl", ".png", ".xlsx", ".csv")):
        return "DEL"
    if low.endswith(".json"):
        if "/backtest/" in p:
            return "DEL" if name.startswith("st_") else "KEEP"
        return "DEL" if name.startswith("_") else "KEEP"
    if low.endswith(".md"):
        return "DEL" if "/backtest/" in p else "KEEP"
    return "REVIEW"


def collect():
    del_list, keep_list, review_list = [], [], []
    # 根目录顶层文件（不递归进 frontend/ backend/）
    for nm in os.listdir(ROOT):
        fp = os.path.join(ROOT, nm)
        if os.path.isfile(fp):
            c = classify(fp)
            (del_list if c == "DEL" else review_list if c == "REVIEW" else keep_list).append(fp)
    # backend 递归
    for dirpath, dirnames, filenames in os.walk(BACKEND):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            c = classify(fp)
            (del_list if c == "DEL" else review_list if c == "REVIEW" else keep_list).append(fp)
    return del_list, keep_list, review_list


def main():
    apply = "--apply" in sys.argv
    del_list, keep_list, review_list = collect()
    cats = {}
    for f in del_list:
        ext = os.path.splitext(f)[1].lower() or "(noext)"
        cats[ext] = cats.get(ext, 0) + 1
    print(f"扫描完成：KEEP={len(keep_list)}  DEL={len(del_list)}  REVIEW={len(review_list)}")
    print("待删除按扩展名：", cats)
    print("\n--- 将删除的文件 ---")
    for f in sorted(del_list):
        print("  " + f)
    if review_list:
        print("\n--- REVIEW（未自动删，请人工确认）---")
        for f in sorted(review_list):
            print("  " + f)
    if not apply:
        print("\n[干跑] 未实际删除。确认无误后加 --apply 执行。")
        return
    n = 0
    for f in del_list:
        try:
            os.remove(f)
            n += 1
        except Exception as e:
            print(f"  删除失败 {f}: {e}")
    print(f"\n[已执行] 删除 {n}/{len(del_list)} 个文件。")


if __name__ == "__main__":
    main()
