# V2 闸门生产配置 · 上线说明

> 配套脚本：`backend/apply_v2_prod_config.py`（POST 到 `/api/trade/symbols`，幂等可重跑）
> 适用品种：**MU / ETH / SPCX**（V2 软分引擎闸门品种）

## 1. 验证结论摘要

### 1.1 阈值标定（03-01 ~ 09-30 半年窗，生产闸门模式）
| 品种 | V1成交 | 旧阈值 | 寻优 t* | 结论 |
|---|--:|--:|--:|:--|
| MU | 95 | 40 | **40** | 独立验证吻合 |
| ETH | 62 | 44 | 38(保守段中位)/44 处 in-sample 更优 | 维持 **44** |
| SPCX | 59 | 44 | **44** | 独立验证吻合 |

阈值扫描准则：E>0 且 PF>1 且成交≥50% V1，取连续合格段中位；并切 03-06 / 07-09 做稳健性校验。

### 1.2 OOS 验证（07-01 ~ 12-31，生产闸门 `score_only_gate=False`）
三品种合并逐笔：

| 类别 | T | 收益% | E% | WR% | PF |
|---|--:|--:|--:|--:|--:|
| V1 全量 | 107 | 35.93 | 0.34 | 63.6 | 1.45 |
| **V2 通过(A)** | 89 | 44.88 | **0.50** | 67.4 | **1.72** |
| V2 过滤(B，被拦) | 18 | -8.95 | **-0.50** | 44.4 | 0.45 |

终局判定：**A 类 E=0.50 > V1 0.34；B 类 E=-0.50 < 0 ⇒ V2 在生产模式 OOS 有效。**
三个品种均满足 A>E_V1 且被拦的 B 类劣于通过的 A 类——闸门确实把最差交易挡掉。

### 1.3 重要纠正
昨天那份"V2 OOS 有效但 SPCX 偏弱"的 filter-quality 测试，跑的是 **`score_only_gate=True`（"假设关弱档"的非生产回测模式，`backtest.py:340` 注释明确说明实盘默认不拦）**。
改用**真实生产闸门**（V2 引擎 + `scoring_half_threshold` 入场闸门）后：SPCX 的 A 类 E 由 -0.06 翻正为 **+0.08**，B 类 E 由 -0.95 降到 **-1.19**（拦掉 7 笔 WR 仅 14.3% 的烂交易）。即**生产模式下 V2 闸门在 SPCX 上反而最强**。本说明配置即生产模式。

## 2. 上线配置（目标值）

对 MU / ETH / SPCX 三个品种，经 `POST /api/trade/symbols` 设置（其余字段保持现状不动）：

| 字段 | MU | ETH | SPCX | 说明 |
|---|--:|--:|--:|---|
| `use_scoring` | `true` | `true` | `true` | 启用打分制 |
| `score_engine` | `"v2"` | `"v2"` | `"v2"` | V2 软分引擎（生产闸门） |
| `scoring_full_threshold` | `40` | `44` | `44` | 全仓阈值 = 入场闸门 |
| `scoring_half_threshold` | `40` | `44` | `44` | 半仓阈值（= 入场闸门，与 full 一致） |
| `scoring_alert_threshold` | `40` | `44` | `44` | 提醒阈值（与上面一致） |
| `use_dynamic_threshold` | 不动 | 不动 | 不动 | 仅影响 ER 闸门，验证时已含，保持现状 |
| `score_only_gate` | 不设 | 不设 | 不设 | **严禁开启**（=非生产模式，会误判） |

> 三个 `scoring_*` 阈值必须相等（full=half=alert），与验证用的单一二元闸门一致。

## 3. 应用方式

### 方式 A：直接运行配套脚本（推荐）
```bash
cd backend
python apply_v2_prod_config.py            # 默认打到 43.108.10.84:5174
# 或指定另一个节点：
BASE=http://47.84.106.154:5174 python apply_v2_prod_config.py
```
脚本对每个品种依次 POST，打印服务端返回，全部 `ok:true` 即生效（服务端 `save_settings()` 已落盘）。

### 方式 B：手动 curl
```bash
for s in MU ETH SPCX; do
  case $s in MU) T=40;; ETH|SPCX) T=44;; esac
  curl -s -X POST http://43.108.10.84:5174/api/trade/symbols \
    -H 'Content-Type: application/json' \
    -d "{\"symbol\":\"$s\",\"use_scoring\":true,\"score_engine\":\"v2\",\
\"scoring_full_threshold\":$T,\"scoring_half_threshold\":$T,\"scoring_alert_threshold\":$T}"
  echo
done
```

### 校验
```bash
curl -s http://43.108.10.84:5174/api/trade/symbols | python -m json.tool
# 确认 MU/ETH/SPCX 的 score_engine=="v2" 且三个 scoring 阈值分别为 40/44/44
```

## 4. 回滚
将 `score_engine` 置空（回退 V1）或改回原阈值即可，例如：
```bash
curl -s -X POST http://43.108.10.84:5174/api/trade/symbols \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"MU","score_engine":""}'
```

## 5. 注意事项
1. **不要用 `score_only_gate=True` 跑生产**——那是"假设关弱档"的非生产回测开关，会使 SPCX 等指标失真。
2. `use_dynamic_threshold` 保持现状：它只自适应 `er_min`（ER 闸门），与本次验证的评分闸门（40/44/44）正交，不影响结论。
3. 本次仅改三个 V2 品种的评分闸门阈值，未触碰 ER / 过滤器 / 止盈止损等其他参数。
4. 配置经服务端 `save_settings()` 持久化；多节点需对每个节点分别执行（脚本默认节点见 `apply_v2_prod_config.py` 顶部 `LIVE` 列表）。
