# -*- coding: utf-8 -*-
"""
策略学习 · 独立新管线 API  (/api/sl2)
=====================================

与原有 `/api/sl/*` 完全隔离，具体做法：

1. **本模块不 import sl_v2，也不 import strategy_learning。**
   后端进程里不会加载任何 K 线/模型，不会往原有 `_CACHE` 写东西，共享状态为零。
2. 训练/预测一律**起子进程**跑 `sl_v2.py`：
   独立进程 → 独立内存 → 独立输出目录 `ml_runs/<run>/`。
   后端只负责启动、转发状态、读回结果。
3. 不改 `strategy_learning.py` 一行，原有页面 `/api/sl/*` 逻辑完全不受影响。

端点
----
  GET  /api/sl2/health           环境自检
  GET  /api/sl2/runs             列出所有新训练 run（含指标）
  GET  /api/sl2/runs/{name}      单个 run 的 meta
  POST /api/sl2/train            启动一次新训练（异步，立即返回）
  GET  /api/sl2/jobs             所有训练任务状态
  GET  /api/sl2/jobs/{name}      单个训练任务状态（含日志尾部 / 结果指标）
  POST /api/sl2/predict          用某个 run 预测（同步返回明细）
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

BACKEND_DIR = Path(__file__).resolve().parent
SCRIPT = BACKEND_DIR / "sl_v2.py"
RUNS_DIR = BACKEND_DIR / "ml_runs"

sl2_router = APIRouter(prefix="/api/sl2", tags=["strategy-learning-v2"])

# 训练任务表（只存后端进程内存，重启即清空；模型结果已落盘，不会丢）
_JOBS: dict[str, dict] = {}


# ──────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────
def _valid_name(name: str) -> bool:
    return bool(name) and all(c.isalnum() or c in "-_." for c in name) and ".." not in name


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"     # 子进程输出统一 UTF-8，避免 GBK 乱码
    env["PYTHONUTF8"] = "1"
    return env


def _read_meta(name: str) -> dict | None:
    f = RUNS_DIR / name / "meta.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def _log_tail(name: str, n: int = 14) -> str:
    f = RUNS_DIR / name / "train.log"
    if not f.exists():
        return ""
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    return "\n".join(lines[-n:])


def _job_view(name: str) -> dict | None:
    j = _JOBS.get(name)
    if not j:
        return None
    proc: subprocess.Popen = j["proc"]
    rc = proc.poll()
    meta = _read_meta(name)
    if rc is None:
        status = "running"
    elif meta is not None:
        status = "done"
    else:
        status = "failed"
    view = {
        "name": name, "status": status, "returncode": rc,
        "elapsed": round(time.time() - j["started"], 1),
        "args": j["args"], "log_tail": _log_tail(name),
    }
    if status == "done":
        view["meta"] = meta
    return view


def _run_summary(d: Path) -> dict | None:
    meta = _read_meta(d.name)
    if not meta:
        return None
    m = meta.get("metrics") or {}
    return {
        "name": d.name, "created_at": meta.get("created_at"),
        "symbol": meta.get("symbol"), "base_tf": meta.get("base_tf"),
        "task": meta.get("task"), "sample": meta.get("sample"),
        "label_mode": meta.get("label_mode"), "horizon": meta.get("horizon"),
        "tp_pct": meta.get("tp_pct"), "sl_pct": meta.get("sl_pct"),
        "n_features": meta.get("n_features"), "n_rows": m.get("n_rows"),
        "best_iteration": meta.get("best_iteration"),
        "metrics": m,
        "top_importance": (meta.get("importance") or [])[:6],
    }


# ──────────────────────────────────────────────────────────────
# 端点
# ──────────────────────────────────────────────────────────────
@sl2_router.get("/health")
async def health():
    try:
        import lightgbm as lgb
        lgb_ver = lgb.__version__
    except Exception:
        lgb_ver = None
    return {
        "ok": True,
        "script": str(SCRIPT), "script_exists": SCRIPT.exists(),
        "python": sys.executable, "lightgbm": lgb_ver,
        "runs_dir": str(RUNS_DIR), "runs_dir_exists": RUNS_DIR.exists(),
        "active_jobs": [n for n, j in _JOBS.items() if j["proc"].poll() is None],
    }


@sl2_router.get("/runs")
async def list_runs():
    if not RUNS_DIR.exists():
        return {"ok": True, "runs": []}
    runs = []
    for d in sorted(RUNS_DIR.iterdir(), reverse=True):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        s = _run_summary(d)
        if s:
            runs.append(s)
    return {"ok": True, "runs": runs}


@sl2_router.get("/runs/{name}")
async def get_run(name: str):
    meta = _read_meta(name)
    if not meta:
        return {"ok": False, "error": f"找不到 run：{name}"}
    return {"ok": True, "meta": meta}


class TrainReq(BaseModel):
    name: str
    symbol: str = "BTC-USDT"
    base_tf: str = "1h"
    label_mode: str = "tpsl"          # exit|fwd|tpsl(分类) / vol|mfe(回归)
    horizon: int = 30
    tp_pct: float = 2.5
    sl_pct: float = 2.0
    sample: str = "all"               # all | v3 | nonchop | v3_nonchop
    task: str = "cls"                 # cls | reg
    test_size: float = 0.3
    valid_size: float = 0.2           # 从训练集切出的验证集，仅供 early stopping
    rounds: int = 300
    seed: int = 42
    years: int = 5
    force: bool = False
    no_cache: bool = False


@sl2_router.post("/train")
async def start_train(req: TrainReq):
    name = (req.name or "").strip()
    if not _valid_name(name):
        return {"ok": False, "error": "run 名称只能含字母/数字/-/_/.，不能有路径分隔符"}
    if req.label_mode not in ("exit", "fwd", "tpsl", "vol", "mfe"):
        return {"ok": False, "error": "label_mode 只能是 exit / fwd / tpsl / vol / mfe"}
    if req.task not in ("cls", "reg"):
        return {"ok": False, "error": "task 只能是 cls / reg"}
    if req.label_mode in ("vol", "mfe") and req.task != "reg":
        return {"ok": False, "error": f"{req.label_mode} 是回归标签，task 必须是 reg"}
    if req.sample not in ("all", "v3", "nonchop", "v3_nonchop"):
        return {"ok": False, "error": "sample 只能是 all / v3 / nonchop / v3_nonchop"}

    cur = _JOBS.get(name)
    if cur and cur["proc"].poll() is None:
        return {"ok": False, "error": f"{name} 正在训练中，请先等它结束"}
    if (RUNS_DIR / name / "model.txt").exists() and not req.force:
        return {"ok": False, "error": f"{name} 已存在同名模型，换个名称或勾选「覆盖」"}

    run_dir = RUNS_DIR / name
    run_dir.mkdir(parents=True, exist_ok=True)

    args = [
        "train", "--name", name, "--symbol", req.symbol, "--base-tf", req.base_tf,
        "--label-mode", req.label_mode, "--horizon", str(req.horizon),
        "--tp", str(req.tp_pct), "--sl", str(req.sl_pct), "--sample", req.sample,
        "--task", req.task, "--test-size", str(req.test_size),
        "--valid-size", str(req.valid_size),
        "--rounds", str(req.rounds), "--seed", str(req.seed), "--years", str(req.years),
    ]
    if req.force:
        args.append("--force")
    if req.no_cache:
        args.append("--no-cache")

    log = open(run_dir / "train.log", "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), *args],
            cwd=str(BACKEND_DIR), stdout=log, stderr=subprocess.STDOUT, env=_env())
    except Exception as e:
        log.close()
        return {"ok": False, "error": f"子进程启动失败：{e}"}

    _JOBS[name] = {"proc": proc, "log": log, "started": time.time(), "args": args}
    return {"ok": True, "name": name, "pid": proc.pid}


@sl2_router.get("/jobs")
async def list_jobs():
    return {"ok": True, "jobs": [v for v in (_job_view(n) for n in list(_JOBS)) if v]}


@sl2_router.get("/jobs/{name}")
async def get_job(name: str):
    v = _job_view(name)
    if not v:
        return {"ok": False, "error": f"没有该训练任务：{name}"}
    return {"ok": True, **v}


class PredictReq(BaseModel):
    name: str
    tail: int = 50
    sample: Optional[str] = None
    no_cache: bool = False


@sl2_router.post("/predict")
async def run_predict(req: PredictReq):
    if not (RUNS_DIR / req.name / "model.txt").exists():
        return {"ok": False, "error": f"找不到 run：{req.name}"}
    out_file = RUNS_DIR / req.name / "_pred_tmp.json"
    args = ["predict", "--name", req.name, "--tail", str(max(1, min(500, req.tail))),
            "--json-out", str(out_file)]
    if req.sample:
        args += ["--sample", req.sample]
    if req.no_cache:
        args.append("--no-cache")

    def _run():
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              cwd=str(BACKEND_DIR), env=_env(),
                              capture_output=True, timeout=900)

    try:
        cp = await asyncio.get_event_loop().run_in_executor(None, _run)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "预测超时（>15 分钟）"}
    except Exception as e:
        return {"ok": False, "error": f"子进程执行失败：{e}"}

    if not out_file.exists():
        err = (cp.stdout or b"").decode("utf-8", "replace")[-1500:]
        return {"ok": False, "error": "预测未产出结果", "detail": err}
    try:
        res = json.loads(out_file.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"结果解析失败：{e}"}
    finally:
        out_file.unlink(missing_ok=True)
    return {"ok": True, **res}
