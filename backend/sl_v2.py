# -*- coding: utf-8 -*-
"""
策略学习 · 独立新训练管线 (sl_v2)
=================================

目的：在不影响原有「策略学习」(strategy_learning.py / /api/sl/* / _CACHE) 的前提下，
从零训练一个**全新的**模型，并把模型 + 特征清单一起落盘，供后续预测严格对齐。

隔离设计（为什么要"新开一个"）
------------------------------
1. 独立进程运行：`python sl_v2.py train ...`
   strategy_learning.py 的训练结果只缓存在**进程内存** _CACHE 里，从不落盘；
   本脚本单独起进程，内存互不干扰，跑完即退出，不会动到正在跑的那个。
2. 独立输出目录：backend/ml_runs/<name>/，与 ml_models/ 完全分开。
3. **不修改 strategy_learning.py**：只复用它的 build_dataset() 做特征构建（只读），
   保证特征口径和页面完全一致；它内部的 _DS_CACHE/_CANDLE_CACHE 只存在于本进程。

两条硬性工程约束（已内置）
--------------------------
① 训练与预测必须使用同一批特征、同一顺序。
   训练结束时把 result["features"] 原样写入 features.json；
   预测时先读 features.json，再按这个顺序逐列取值（缺失补 0、多余忽略），
   **绝不**使用模块当前的 FEATURE_COLS —— 否则以后有人加/删特征，旧模型就会错位。
② 保存用 booster.save_model()，加载用 lgb.Booster(model_file=...)。
   保存: booster.save_model(path, num_iteration=best_iteration)
   加载: lgb.Booster(model_file=path)
   加载后用 booster.feature_name() 与 features.json 交叉校验，不一致直接报错。

产物
----
backend/ml_runs/<name>/
    model.txt        LightGBM Booster（booster.save_model 导出，纯文本）
    features.json    训练时的特征顺序（对齐基准）+ task
    meta.json        数据/标签配置 + 指标 + 时间戳 + 特征重要性
    preds_*.csv      预测结果（predict 时可选输出）

用法
----
    cd backend
    python sl_v2.py train   --name btc_1h_tpsl_h30 --label-mode tpsl --horizon 30 --tp 2.5 --sl 2.0
    python sl_v2.py train   --name btc_1h_v3      --sample v3 --label-mode exit
    python sl_v2.py list
    python sl_v2.py info    --name btc_1h_tpsl_h30
    python sl_v2.py predict --name btc_1h_tpsl_h30 --tail 20
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import lightgbm as lgb
from sklearn.metrics import (
    accuracy_score, confusion_matrix, f1_score, mean_absolute_error,
    precision_recall_fscore_support, r2_score, roc_auc_score,
)
from sklearn.model_selection import train_test_split

# 只读复用原有特征构建管线（不修改它）
import strategy_learning as sl
# 自定义标签口径（与 sl2_lab 共用同一份实现，避免两处漂移）
import sl2_labels as labels

RUNS_DIR = Path(__file__).resolve().parent / "ml_runs"

# 数据集构建参数（与页面 /api/sl 完全一致的口径）
SYMBOL_DEFAULT = "BTC-USDT"
TF_DEFAULT = "1h"


def _say(*a):
    try:
        print(*a)
    except UnicodeEncodeError:                       # Windows GBK 控制台兜底
        print(*(str(x).encode("utf-8", "replace").decode("utf-8", "replace") for x in a))


# ──────────────────────────────────────────────────────────────
# K 线取数：优先复用本地缓存（train_full.py 的同一套约定），无网也能训
# ──────────────────────────────────────────────────────────────
def _auto_candle_file(symbol: str, base_tf: str) -> Path | None:
    base = symbol.split("-")[0].lower()
    for p in (f"backtest/{base}_{base_tf}_full_fetched.json",
              f"backtest/{base}_{base_tf}_full.json"):
        f = Path(p)
        if f.exists() and f.stat().st_size > 2:
            return f
    return None


def preload_candles(symbol: str, base_tf: str, limit: int,
                    candles_file: str | None) -> int:
    """把本地 K 线灌进 strategy_learning 的进程级缓存，返回实际使用的 limit。

    命中缓存后 build_dataset 就不再向 OKX 翻页，训练可离线复现。
    """
    path = Path(candles_file) if candles_file else _auto_candle_file(symbol, base_tf)
    if path and path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            bc = doc.get("base") or doc.get("candles") or []
            if bc:
                limit = len(bc)                    # 让 limit 与实际根数一致，缓存 key 才对得上
                sl._CANDLE_CACHE[f"{symbol}|{base_tf}|{limit}"] = bc
                _say(f"[数据] 复用本地 K 线 {len(bc)} 根  ({path})")
                return limit
        except Exception as e:
            _say(f"[数据] 本地缓存读取失败，改为联网拉取：{e}")
    _say(f"[数据] 未命中本地缓存，将向 OKX 翻页拉取 {symbol} {base_tf} 近 {limit} 根")
    return limit


# ──────────────────────────────────────────────────────────────
# 数据集磁盘缓存：build_dataset 约 3 分钟（瓶颈在翻页/特征计算），
# 缓存下来后 train/predict 秒级复用。放在 ml_runs/_ds_cache/，与原有模块互不干扰。
# ──────────────────────────────────────────────────────────────
def _ds_cache_dir() -> Path:
    return RUNS_DIR / "_ds_cache"


def _ds_key(symbol, base_tf, limit, label_mode, horizon, tp_pct, sl_pct) -> str:
    raw = f"{symbol}|{base_tf}|{limit}|{label_mode}|{horizon}|{tp_pct}|{sl_pct}"
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in raw)


def load_ds_cache(key: str) -> dict | None:
    f = _ds_cache_dir() / f"{key}.json"
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        # 特征清单变了（有人增删了 FEATURE_COLS）→ 缓存作废，强制重建
        if list(d.get("features") or []) != list(sl.FEATURE_COLS):
            return None
        return d
    except Exception:
        return None


def save_ds_cache(key: str, ds: dict):
    try:
        _ds_cache_dir().mkdir(parents=True, exist_ok=True)
        slim = {k: v for k, v in ds.items() if k != "regime_stats"}
        (_ds_cache_dir() / f"{key}.json").write_text(
            json.dumps(slim, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        _say(f"[数据] 数据集缓存写入失败（不影响本次结果）：{e}")


def get_dataset(symbol, base_tf, limit, label_mode, horizon, tp_pct, sl_pct,
                no_cache=False, candles_file=None):
    """统一取数入口：先查磁盘缓存，未命中才 build_dataset，并回写缓存。

    返回 (ds, from_cache)。
    """
    limit = preload_candles(symbol, base_tf, limit, candles_file)
    key = _ds_key(symbol, base_tf, limit, label_mode, horizon, tp_pct, sl_pct)
    if not no_cache:
        d = load_ds_cache(key)
        if d:
            _say(f"[数据] 命中数据集缓存：{len(d.get('rows') or [])} 笔信号（跳过 build_dataset）")
            return d, True
    ds = asyncio.run(sl.build_dataset(
        symbol=symbol, base_tf=base_tf, limit=limit, years=None,
        label_mode=label_mode, horizon=horizon, tp_pct=tp_pct, sl_pct=sl_pct))
    if ds:
        save_ds_cache(key, ds)
    return ds, False


# ──────────────────────────────────────────────────────────────
# 特征对齐（约束 ① 的核心）
# ──────────────────────────────────────────────────────────────
def build_x(rows: list[dict], features: list[str]) -> np.ndarray:
    """按 features 的**给定顺序**把 rows 拼成 X。缺失列补 0，多余列忽略。

    注意：全程不用 df[FEATURE_COLS]，只按传入的 features 顺序取列，
    这样预测端只要拿到训练时存下的 features.json 就一定对齐。
    """
    if not rows:
        return np.zeros((0, len(features)), dtype=float)
    df = pd.DataFrame(rows)
    X = np.zeros((len(df), len(features)), dtype=float)
    for j, f in enumerate(features):
        if f in df.columns:
            X[:, j] = pd.to_numeric(df[f], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    return X


def build_y(rows: list[dict], task: str) -> np.ndarray:
    """标签列。实时预测的行没有 win/pnl，这里允许缺列（补 0），不影响特征对齐。"""
    df = pd.DataFrame(rows)
    if task == "cls":
        if "win" not in df.columns:
            return np.zeros(len(df), dtype=int)
        return pd.to_numeric(df["win"], errors="coerce").fillna(0).to_numpy(dtype=int)
    if "pnl" not in df.columns:
        return np.zeros(len(df), dtype=float)
    return pd.to_numeric(df["pnl"], errors="coerce").fillna(0.0).to_numpy(dtype=float)


def build_xy(rows: list[dict], features: list[str], task: str):
    return build_x(rows, features), build_y(rows, task)


def align_rows(rows: list[dict], features: list[str]) -> np.ndarray:
    """预测时的对齐入口：和训练用同一套规则，保证同序同列。"""
    return build_x(rows, features)


# ──────────────────────────────────────────────────────────────
# 模型读写（约束 ②）：booster.save_model() / lgb.Booster(model_file=...)
#
# ⚠️ 本项目踩坑记录：LightGBM 的 C API 用的是窄字符路径，
#    Windows 上**含非 ASCII 字符的路径**（本项目根目录 `个人项目代码`）
#    保存报 "Model file ... is not available for writes"，加载报 "Could not open ..."。
#    所以这里加一层 ASCII 临时文件中转：API 调用方式完全不变，
#    只是落盘/读取时借道 %TEMP% 再搬回来。
# ──────────────────────────────────────────────────────────────
def _is_ascii(p) -> bool:
    try:
        str(p).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def save_booster(booster, path: Path, num_iteration: int | None = None):
    """booster.save_model()，自动绕开非 ASCII 路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    kw = {"num_iteration": int(num_iteration)} if num_iteration and num_iteration > 0 else {}
    if _is_ascii(path):
        booster.save_model(str(path), **kw)
        return
    tmp = Path(tempfile.gettempdir()) / f"lgb_{uuid.uuid4().hex}.txt"
    try:
        booster.save_model(str(tmp), **kw)      # 先写 ASCII 临时文件
        shutil.move(str(tmp), str(path))        # 再搬到中文路径
    finally:
        if tmp.exists():
            tmp.unlink()


def load_booster(path: Path):
    """lgb.Booster(model_file=...)，自动绕开非 ASCII 路径。"""
    path = Path(path)
    if _is_ascii(path):
        return lgb.Booster(model_file=str(path))
    tmp = Path(tempfile.gettempdir()) / f"lgb_{uuid.uuid4().hex}.txt"
    try:
        shutil.copy(str(path), str(tmp))        # 先复制到 ASCII 临时文件
        return lgb.Booster(model_file=str(tmp))
    finally:
        if tmp.exists():
            tmp.unlink()


# ──────────────────────────────────────────────────────────────
# 训练
# ──────────────────────────────────────────────────────────────
def train_booster(Xtr, ytr, Xva, yva, features, task, rounds=300, seed=42, extra=None):
    """Xva/yva 为**独立验证集**，仅供 early stopping；测试集不参与选轮数。"""
    params = {
        "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 20,
        "feature_fraction": 0.9, "bagging_fraction": 0.8, "bagging_freq": 1,
        "verbose": -1, "seed": seed, "num_threads": 0,
        "objective": "binary" if task == "cls" else "regression",
        "metric": "auc" if task == "cls" else "rmse",
    }
    if extra:
        params.update(extra)

    dtrain = lgb.Dataset(Xtr, label=ytr, feature_name=list(features))
    valid, callbacks = [], [lgb.log_evaluation(0)]
    if Xva is not None and len(yva) > 0:
        valid = [lgb.Dataset(Xva, label=yva, reference=dtrain, feature_name=list(features))]
        callbacks.append(lgb.early_stopping(50, verbose=False))

    booster = lgb.train(params, dtrain, num_boost_round=rounds,
                        valid_sets=valid, callbacks=callbacks)
    return booster


def evaluate(booster, Xte, yte, task):
    if task == "cls":
        proba = booster.predict(Xte)
        pred = (proba >= 0.5).astype(int)
        p, r, f1, _ = precision_recall_fscore_support(yte, pred, average="binary", zero_division=0)
        cm = confusion_matrix(yte, pred, labels=[0, 1])
        m = {"task": "cls", "n_test": int(len(yte)),
             "accuracy": float(accuracy_score(yte, pred)),
             "precision": float(p), "recall": float(r), "f1": float(f1),
             "confusion_matrix": {"tn": int(cm[0, 0]), "fp": int(cm[0, 1]),
                                  "fn": int(cm[1, 0]), "tp": int(cm[1, 1])}}
        try:
            m["auc"] = float(roc_auc_score(yte, proba))
        except Exception:
            m["auc"] = None
        return m
    yp = booster.predict(Xte)
    return {"task": "reg", "n_test": int(len(yte)),
            "mae": float(mean_absolute_error(yte, yp)),
            "rmse": float(np.sqrt(np.mean((yte - yp) ** 2))),
            "r2": float(r2_score(yte, yp))}


def _run_dir(name: str) -> Path:
    return RUNS_DIR / name


def _carrier(label_mode, horizon, tp_pct, sl_pct):
    """决定用哪套参数去 build_dataset 取「特征行」。

    自定义标签（vol / mfe）只换**目标**，特征跟目标无关，
    所以统一用固定的 tpsl 载体去命中数据集缓存，不必为换标签重建数据集（省 3 分钟）。
    """
    if label_mode in labels.CUSTOM_MODES:
        return ("tpsl", 30, 2.5, 2.0)
    return (label_mode, horizon, tp_pct, sl_pct)


def _cached_candles(symbol: str, base_tf: str, limit: int):
    """从 strategy_learning 的进程级缓存取 K 线（preload_candles 已灌好）。"""
    return sl._CANDLE_CACHE.get(f"{symbol}|{base_tf}|{limit}")


def cmd_train(a):
    custom = a.label_mode in labels.CUSTOM_MODES
    task = "reg" if custom else a.task
    _say("=" * 64)
    _say(f"[训练] run={a.name}  {a.symbol} {a.base_tf}  task={task}  "
         f"label={a.label_mode}{' (自定义标签)' if custom else ''}")
    _say("=" * 64)

    carrier = _carrier(a.label_mode, a.horizon, a.tp_pct, a.sl_pct)
    limit = sl.compute_limit(a.base_tf, a.years) if a.limit is None else a.limit
    ds, _cached = get_dataset(a.symbol, a.base_tf, limit, *carrier,
                              no_cache=a.no_cache, candles_file=a.candles_file)
    if not ds:
        _say("[失败] build_dataset 返回空：检查网络 / 本地 K 线缓存")
        return 1
    limit = int(ds.get("candles_n") or limit)                    # 记录实际使用的 K 线根数

    features = list(ds["features"])            # ← 约束 ① 的基准，来自数据集本身
    rows = _filter(ds["rows"], a.sample)
    if custom:
        _say(f"[数据] 信号 {len(ds['rows'])} 笔  →  过滤({a.sample}) 后 {len(rows)} 笔  "
             f"特征维度 {len(features)}  标签={a.label_mode}(h={a.horizon})")
    else:
        _say(f"[数据] 信号 {len(ds['rows'])} 笔  →  过滤({a.sample}) 后 {len(rows)} 笔  "
             f"特征维度 {len(features)}  "
             f"胜率 {sum(r['win'] for r in rows) / max(1, len(rows)) * 100:.1f}%")
    if len(rows) < 40:
        _say("[失败] 过滤后样本 < 40，不适合训练")
        return 1

    out = _run_dir(a.name)
    if (out / "model.txt").exists() and not a.force:
        _say(f"[失败] 该 run 已有模型：{out}\n       换个 --name，或加 --force 覆盖（会删掉旧模型）")
        return 1
    out.mkdir(parents=True, exist_ok=True)

    if custom:
        candles = _cached_candles(a.symbol, a.base_tf, limit)
        if not candles:
            _say("[失败] 自定义标签需要 K 线，但进程缓存里没有")
            return 1
        ys = labels.compute_targets(candles, [r["ts"] for r in rows], a.label_mode,
                                    a.horizon, [r["dir"] for r in rows])
        keep = [k for k, v in enumerate(ys) if v is not None]
        if len(keep) < len(rows):
            _say(f"[数据] 剔除 {len(rows) - len(keep)} 个标签不可用的样本")
        rows = [rows[k] for k in keep]
        X = build_x(rows, features)
        y = np.asarray([ys[k] for k in keep], float)
    else:
        X, y = build_xy(rows, features, task)

    strat = y if (task == "cls" and len(set(y.tolist())) > 1) else None
    # 先切出独立 test（只用于最终报告）；early stopping 绝不看它
    Xtr_all, Xte, ytr_all, yte = train_test_split(
        X, y, test_size=a.test_size, random_state=a.seed, stratify=strat)
    # 再从训练部分切出 valid，仅用于决定轮数
    if a.valid_size and a.valid_size > 0 and len(ytr_all) >= 40:
        s2 = ytr_all if (task == "cls" and len(set(ytr_all.tolist())) > 1) else None
        Xtr, Xva, ytr, yva = train_test_split(
            Xtr_all, ytr_all, test_size=a.valid_size, random_state=a.seed, stratify=s2)
    else:
        Xtr, ytr, Xva, yva = Xtr_all, ytr_all, None, None
    _say(f"[训练] train={len(ytr)}  valid={0 if Xva is None else len(yva)}  "
         f"test={len(yte)}  rounds<={a.rounds}（test 只用于最终评估）")

    extra = {}
    for kv in (a.param or []):
        k, _, v = kv.partition("=")
        try:
            extra[k] = int(v) if v.isdigit() else float(v)
        except ValueError:
            extra[k] = v
    booster = train_booster(Xtr, ytr, Xva, yva, features, task,
                            rounds=a.rounds, seed=a.seed, extra=extra)

    metrics = evaluate(booster, Xte, yte, task)
    metrics.update({"n_train": int(len(ytr)),
                    "n_valid": 0 if Xva is None else int(len(yva)),
                    "n_rows": int(len(rows))})

    # ── 保存（约束 ②）：先 save_model，再存特征清单 ──
    model_path = out / "model.txt"
    try:
        bi = int(booster.best_iteration)
    except Exception:
        bi = 0
    save_booster(booster, model_path, num_iteration=bi)

    # 交叉校验：模型内嵌的特征名必须与 features.json 完全一致
    names = list(booster.feature_name())
    if names != features:
        raise RuntimeError(
            f"特征顺序不一致！模型内嵌 {names[:5]}… vs 数据集 {features[:5]}…，已中止")

    (out / "features.json").write_text(json.dumps(
        {"task": task, "n_features": len(features), "features": features},
        ensure_ascii=False, indent=2), encoding="utf-8")

    gain = booster.feature_importance(importance_type="gain")
    tot = float(gain.sum()) or 1.0
    importance = [{"feature": f, "cn": sl.FEATURE_CN.get(f, f),
                   "gain": float(v), "ratio": float(v / tot)}
                  for f, v in sorted(zip(features, gain), key=lambda x: -x[1])]

    # 退化体检：模型几乎只会输出一个常数 → 当前标签口径下特征没学到东西
    pred_spread = None
    if task == "cls":
        p_te = booster.predict(Xte)
        pred_spread = float(p_te.max() - p_te.min())

    meta = {
        "name": a.name, "created_at": datetime.now(timezone.utc).isoformat(),
        "symbol": a.symbol, "base_tf": a.base_tf, "limit": limit,
        "label_mode": a.label_mode, "horizon": a.horizon,
        "tp_pct": a.tp_pct, "sl_pct": a.sl_pct, "sample": a.sample,
        "task": task, "test_size": a.test_size, "valid_size": a.valid_size,
        "split": "train / valid(early-stop) / test(仅评估)",
        "seed": a.seed, "num_boost_round": a.rounds, "best_iteration": bi,
        "pred_spread_test": pred_spread,
        "lightgbm": lgb.__version__, "params": extra,
        "n_features": len(features), "feature_order": features,
        "metrics": metrics, "importance": importance[:20],
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                   encoding="utf-8")

    _say(f"[保存] {model_path.name}  features.json  meta.json   →  {out}")
    _say(f"[轮数] best_iteration={bi}（在独立 valid 上选，没看 test）")
    if task == "cls":
        _say(f"[指标] acc={metrics['accuracy']:.3f}  f1={metrics['f1']:.3f}  "
             f"auc={(metrics['auc'] or 0):.3f}  (n_test={metrics['n_test']})")
        if pred_spread is not None and (pred_spread < 0.05 or bi <= 3):
            _say(f"[警告] 模型近乎退化：test 预测概率跨度仅 {pred_spread:.3f}、最优迭代 {bi}。"
                 f"当前标签口径下特征几乎没有区分度 —— 建议换 --sample / --horizon / --label-mode 对比。")
    else:
        _say(f"[指标] mae={metrics['mae']:.3f}  rmse={metrics['rmse']:.3f}  r2={metrics['r2']:.3f}")
    _say("[重要] " + "  ".join(f"{d['cn']}={d['ratio'] * 100:.1f}%" for d in importance[:8]))
    return 0


def _filter(rows, sample):
    s = (sample or "all").lower()
    if s == "all":
        return list(rows)
    if s == "v3":
        return [r for r in rows if r.get("v3_pass")]
    if s == "nonchop":
        return [r for r in rows if not r.get("is_disorder")]
    if s == "v3_nonchop":
        return [r for r in rows if r.get("v3_pass") and not r.get("is_disorder")]
    raise SystemExit(f"未知 --sample: {sample}（可选 all | v3 | nonchop | v3_nonchop）")


# ──────────────────────────────────────────────────────────────
# 加载 & 预测（约束 ①② 的消费端）
# ──────────────────────────────────────────────────────────────
class SlModel:
    """一个训练 run 的加载器：模型 + 特征清单一起读，保证对齐。"""

    def __init__(self, name: str):
        d = _run_dir(name)
        if not (d / "model.txt").exists():
            raise FileNotFoundError(f"找不到模型：{d / 'model.txt'}")
        self.dir = d
        self.features = list(json.loads(
            (d / "features.json").read_text(encoding="utf-8"))["features"])
        self.meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        # 约束 ②：LightGBM 官方加载方式
        self.booster = load_booster(d / "model.txt")
        names = list(self.booster.feature_name())
        if names != self.features:
            raise RuntimeError(
                f"features.json 与 model.txt 特征不一致，拒绝预测：\n  json={self.features[:6]}\n  model={names[:6]}")
        self.task = self.meta.get("task", "cls")

    def predict_rows(self, rows: list[dict]):
        X = align_rows(rows, self.features)
        if X.shape[0] == 0:
            return np.array([]), np.array([])
        if self.task == "cls":
            proba = self.booster.predict(X)
            return (proba >= 0.5).astype(int), proba
        return self.booster.predict(X), self.booster.predict(X)

    def predict_one(self, row: dict) -> tuple[int, float]:
        pred, score = self.predict_rows([row])
        return int(pred[0]), float(score[0])


def predict_run(name, base_tf=None, limit=None, sample=None, tail=50, years=5,
                candles_file=None, no_cache=False) -> dict | None:
    """加载 run 并预测，返回结构化结果（供 CLI 与 /api/sl2 共用）。"""
    m = SlModel(name)
    tf = base_tf or m.meta["base_tf"]
    lim = limit or sl.compute_limit(tf, years)
    lm = m.meta.get("label_mode", "tpsl")
    carrier = _carrier(lm, m.meta.get("horizon", 30),
                       m.meta.get("tp_pct", 2.5), m.meta.get("sl_pct", 2.0))
    ds, cached = get_dataset(m.meta["symbol"], tf, lim, *carrier,
                             no_cache=no_cache, candles_file=candles_file)
    if not ds:
        return None

    used_sample = sample or m.meta.get("sample", "all")
    rows = _filter(ds["rows"], used_sample)[-tail:]
    pred, score = m.predict_rows(rows)

    out = []
    for r, p, s in zip(rows, pred, score):
        out.append({"ts": r["ts"], "dir": r["dir"], "pnl": r.get("pnl"),
                    "win": r.get("win"), "regime6_cn": r.get("regime6_cn"),
                    "v3_pass": bool(r.get("v3_pass")),
                    "prob": float(s), "pred": int(p)})
    has_label = m.task == "cls" and len(out) > 0 and out[0]["win"] is not None
    return {
        "run": name, "task": m.task, "n_features": len(m.features),
        "features": m.features, "symbol": m.meta["symbol"], "base_tf": tf,
        "sample": used_sample, "ds_cached": cached, "n": len(out),
        "rows": out, "label_kind": ("方向(分类)" if m.task == "cls" else "数值(回归)"),
        "hit": (sum(1 for o in out if int(o["pred"]) == int(o["win"])) if has_label else None),
        "meta": {"label_mode": m.meta.get("label_mode"), "horizon": m.meta.get("horizon"),
                 "tp_pct": m.meta.get("tp_pct"), "sl_pct": m.meta.get("sl_pct"),
                 "metrics": m.meta.get("metrics"), "created_at": m.meta.get("created_at")},
    }


def cmd_predict(a):
    res = predict_run(a.name, base_tf=a.base_tf, limit=a.limit, sample=a.sample,
                      tail=a.tail, years=a.years, candles_file=a.candles_file,
                      no_cache=a.no_cache)
    if res is None:
        _say("[失败] 数据集构建失败")
        return 1

    _say(f"[加载] {a.name}  task={res['task']}  特征 {res['n_features']} 维  "
         f"（model.txt + features.json 校验通过）")
    _say(f"       训练配置：{res['symbol']} {res['base_tf']}  "
         f"label={res['meta']['label_mode']}  sample={res['sample']}")

    _say(f"\n最近 {res['n']} 笔信号预测（t=UTC，prob=模型预测盈利概率）：")
    _say(f"{'时间':<17}{'方向':<5}{'实际pnl':>9}{'实际':>6}{'prob':>8}{'预测':>6}")
    for o in res["rows"]:
        t = datetime.fromtimestamp(o["ts"] / 1000, tz=timezone.utc).strftime("%Y/%m/%d %H:%M")
        act = "盈" if o["win"] else "亏"
        _say(f"{t:<17}{'多' if o['dir'] > 0 else '空':<5}{(o['pnl'] or 0):>9.2f}{act:>6}"
             f"{o['prob']:>8.3f}{('盈' if o['pred'] else '亏'):>6}")
    if res["hit"] is not None and res["n"]:
        _say(f"\n[命中] {res['hit']}/{res['n']} = {res['hit'] / res['n'] * 100:.1f}%（仅为样本内回看）")

    if a.out:
        fields = ["ts", "dir", "pnl", "win", "regime6_cn", "v3_pass"]
        pd.DataFrame([{k: o.get(k) for k in fields} for o in res["rows"]]).assign(
            prob=[o["prob"] for o in res["rows"]],
            pred=[o["pred"] for o in res["rows"]]).to_csv(
                a.out, index=False, encoding="utf-8-sig")
        _say(f"\n[输出] 明细已写入 {a.out}")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        _say(f"[输出] JSON 已写入 {a.json_out}")
    return 0


def cmd_list(a):
    if not RUNS_DIR.exists():
        _say("还没有任何新训练 run。")
        return 0
    runs = sorted([d for d in RUNS_DIR.iterdir() if (d / "model.txt").exists()])
    if not runs:
        _say("还没有任何新训练 run。")
        return 0
    _say(f"{'name':<24}{'task':<6}{'sample':<11}{'label':<7}{'n':>6}{'主指标':>28}")
    for d in runs:
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            mt = meta.get("metrics", {})
            key = (f"acc={mt['accuracy']:.3f} auc={(mt.get('auc') or 0):.3f}"
                   if meta.get("task") == "cls"
                   else f"mae={mt['mae']:.3f} r2={mt['r2']:.3f}")
            _say(f"{d.name:<24}{meta.get('task', ''):<6}{meta.get('sample', ''):<11}"
                 f"{meta.get('label_mode', ''):<7}{meta.get('metrics', {}).get('n_rows', 0):>6}{key:>28}")
        except Exception as e:
            _say(f"{d.name:<24}  (meta 读取失败: {e})")
    return 0


def cmd_info(a):
    meta = json.loads((_run_dir(a.name) / "meta.json").read_text(encoding="utf-8"))
    _say(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


# ──────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="策略学习 · 独立新训练管线（不影响原有 /api/sl 与 _CACHE）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("train", help="从零训练一个新模型并落盘")
    t.add_argument("--name", required=True, help="run 名称，输出到 ml_runs/<name>/")
    t.add_argument("--symbol", default=SYMBOL_DEFAULT)
    t.add_argument("--base-tf", default=TF_DEFAULT, dest="base_tf")
    t.add_argument("--limit", type=int, default=None, help="K线根数；默认按 --years 换算")
    t.add_argument("--years", type=int, default=5)
    t.add_argument("--label-mode", default="tpsl", dest="label_mode",
                   choices=["exit", "fwd", "tpsl", "vol", "mfe"],
                   help="exit/fwd/tpsl=方向类(分类)；vol=未来波动率比、mfe=最大有利/不利偏移(回归)")
    t.add_argument("--horizon", type=int, default=30)
    t.add_argument("--tp", type=float, default=2.5, dest="tp_pct")
    t.add_argument("--sl", type=float, default=2.0, dest="sl_pct")
    t.add_argument("--sample", default="all",
                   help="样本子集：all | v3 | nonchop | v3_nonchop")
    t.add_argument("--task", default="cls", choices=["cls", "reg"])
    t.add_argument("--test-size", type=float, default=0.3, dest="test_size")
    t.add_argument("--valid-size", type=float, default=0.2, dest="valid_size",
                   help="从训练集里再切出的验证集比例，仅供 early stopping（0=关闭早停）")
    t.add_argument("--rounds", type=int, default=300, help="num_boost_round 上限")
    t.add_argument("--seed", type=int, default=42)
    t.add_argument("--param", action="append", help="额外 LightGBM 参数，如 --param num_leaves=15")
    t.add_argument("--candles-file", default=None, dest="candles_file",
                   help="本地 K 线 JSON（默认自动找 backtest/<sym>_<tf>_full*.json）")
    t.add_argument("--no-cache", action="store_true", dest="no_cache",
                   help="忽略数据集磁盘缓存，强制重建")
    t.add_argument("--force", action="store_true", help="允许覆盖同名 run")
    t.set_defaults(func=cmd_train)

    p = sub.add_parser("predict", help="加载 run 并预测（按 features.json 对齐）")
    p.add_argument("--name", required=True)
    p.add_argument("--base-tf", default=None, dest="base_tf")
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--sample", default=None,
                   help="覆盖训练时的样本子集；默认沿用 meta 里的")
    p.add_argument("--tail", type=int, default=20, help="只看最近 N 笔")
    p.add_argument("--candles-file", default=None, dest="candles_file")
    p.add_argument("--no-cache", action="store_true", dest="no_cache")
    p.add_argument("--out", default=None, help="明细 CSV 输出路径")
    p.add_argument("--json-out", default=None, dest="json_out",
                   help="结构化结果 JSON 输出路径（供 /api/sl2 调用）")
    p.set_defaults(func=cmd_predict)

    l = sub.add_parser("list", help="列出所有新训练 run")
    l.set_defaults(func=cmd_list)

    i = sub.add_parser("info", help="查看某个 run 的 meta")
    i.add_argument("--name", required=True)
    i.set_defaults(func=cmd_info)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
