# 超级趋势监控台 · Signal Engine

把 TradingView 的 Pine 脚本 **«Signal Engine Quantum Edge»**（© Quantum Edge Capital LLC, MPL-2.0）
完整移植成一个实时盯盘 Web 应用：OKX 实时行情 → SuperTrend 买卖点 → MTF Bias 共振表 → 回测寻优。

源脚本见 [read.md](read.md)，移植后的算法在 [backend/indicators.py](backend/indicators.py)。

---

## 快速开始

```bash
bash start.sh          # 若需代理: bash start.sh 7890
# 打开 http://localhost:5174
```

手动启动：

```bash
cd backend  && pip3 install -r requirements.txt && python3 -m uvicorn main:app --port 8000
cd frontend && npm install && npm run dev
```

要求 Python 3.10–3.13（推荐 3.12）、Node ≥ 18。不要用 3.9。

---

## Pine → Python 对照

移植的核心在 `backend/indicators.py:super_trend()`，逐行对应原脚本的 ATR / SUPERTREND CORE：

| Pine                                                                         | 本项目                                                      |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `atr2 = ta.sma(ta.tr, Periods)`                                              | `ta_sma(ta_tr(...), periods)`                               |
| `atr = changeATR ? ta.atr(Periods) : atr2`                                   | `ta_rma(...) if change_atr else ta_sma(...)`                |
| `up = src - Multiplier * atr`                                                | `raw_up = srcv[i] - multiplier * atr[i]`                    |
| `up := close[1] > up1 ? max(up, up1) : up`                                   | `up[i] = max(raw_up, up1) if closes[i-1] > up1 else raw_up` |
| `dn := close[1] < dn1 ? min(dn, dn1) : dn`                                   | `dn[i] = min(raw_dn, dn1) if closes[i-1] < dn1 else raw_dn` |
| `trend := trend==-1 and close>dn1 ? 1 : trend==1 and close<up1 ? -1 : trend` | 同名分支，见 `for` 循环                                     |
| `buySignal = trend==1 and trend[1]==-1`                                      | `flips` 里 `type: "buy"`                                    |
| `plot(trend==1 ? up : na, style=linebr)`                                     | `up_plot` / `dn_plot`，前端用 whitespace 点断线             |
| `fill(ohlc4, upPlot/dnPlot)`                                                 | `superTrendPrimitive.js` 自绘（连同 linebr 断线）           |
| `barcolor(buy1[1] < sell1[1] ? ...)`                                         | `bar_colors()` = trend 位移一根                             |
| `f_bias(tf) = fastMA >= slowMA ? 1 : -1`                                     | `indicators.bias()` / `strategy.mtf_bias()`                 |

三个容易写错、这里刻意对齐的点：

1. **棘轮与翻转判定都用 `up1/dn1`（前一根的最终轨）**，不是当根的 `up/dn`。用错会让信号早/晚一根 K 线。
2. **`trend` 的种子值是 `1`**（Pine `trend = 1` + `nz(trend[1], trend)`），不是按首根价格去猜方向。
3. **`ta.tr` 首根 = `high - low`**（没有 `close[1]`）；`ta.atr` 是 RMA(Wilder) 而非 SMA。

移植结果与一份独立重写的参照实现在 400 根 K 线上逐根比对：`trend` 与 `up/dn` 零差异。

### 默认参数

原脚本默认 `Periods=15, Multiplier=9.1, src=hl2, changeATR=true`，Bias 表用 `EMA 20/50`。
9.1 倍 ATR 极宽，只在大级别反转才翻向 —— 小周期上可能几百根 K 线才出一个信号，这是脚本原意，不是 bug。
换品种/周期后建议用「回测 → 参数寻优」重调，面板也内置了几档常用预设（经典 10×3、灵敏 7×2、稳健 14×5）。

---

## 功能

**主图**（`CandleChart.jsx`）

- 超趋线按趋势分段：多头段绿色 `up`、空头段红色 `dn`，切换处断开（`style_linebr`）
- ohlc4 与趋势线之间的高亮色带（`fill`），自绘 series primitive
- Buy / Sell 箭头标签打在轨道上，带 A/B/C 等级
- K 线按趋势方向整根上色（`barcolor`）
- 三个开关对应 Pine 的 `showsignals` / `highlighting` / `barcoloring`

**MTF Bias 表** — 原脚本右上角那张表，8 个周期 `5m→1M`，`fast MA >= slow MA` 判多空。
额外加了每周期的 SuperTrend 方向：两者同向标 ★，那才是干净的顺势位置。
以及一个按周期加权的整体偏向（高周期权重更大），`|score| >= 0.5` 才算共振。

**买卖点列表** — 历史翻转流水，含触发价、超趋线（=初始跟踪止损）、至今浮动盈亏、强度评分。
信号等级（本项目补充，原脚本无）：

| 等级  | 含义                                           |
| ----- | ---------------------------------------------- |
| **A** | 顺 Bias 方向，且强度 ≥2/3（突破干脆）          |
| **B** | 顺 Bias 方向强度一般；或 Bias 分歧但翻转质量好 |
| **C** | 逆 Bias 方向，仅提示，不建议跟                 |

强度 3 分制：翻转当根实体同向 +1、量能超前 20 根均量 1.2 倍 +1、收盘离被破轨道 >0.3 ATR +1。

**回测** — 等价于 Pine 的 `strategy.entry`（翻转反手、永远满仓），另加两个实盘开关：
只做多、MTF Bias 过滤（只接受与偏向一致的开仓，逆向信号只平不反手）。
输出策略收益 / 买入持有基准 / 超额 α / 最大回撤 / 胜率 / 盈亏比 + 权益曲线 + 成交明细。
「参数寻优」网格跑 `period × multiplier`，可一键把某组参数应用到全局。

**信号提醒** — 三个渠道同时生效：

- 页面醒目弹窗 + 提示音（买单上行音、卖单下行音，Esc 关闭）
- 右上角小提示
- 微信推送（Server酱，可选）

```bash
cp .env.example .env      # 然后填 SERVERCHAN_SENDKEY
```

---

## 自动挂单（OKX 合约）

信号触发时自动挂限价单，**但震荡行情只提醒不下单**。

### 配置

```bash
cp .env.example .env
# 编辑 .env 填入 OKX key / secret / passphrase
# OKX_SIMULATED=1 默认模拟盘，改 0 才是实盘
bash start.sh
```

模拟盘 key 在 OKX：交易 → 模拟交易 → 个人中心 → 模拟盘 API。实盘 key 与模拟盘 key 不通用。

### 下单闸门 —— 全部满足才挂单

| 条件     | 默认      | 说明                                 |
| -------- | --------- | ------------------------------------ |
| 行情状态 | ER ≥ 0.15 | **震荡市实测亏损率 80%，此时只提醒** |
| 信号等级 | A / B     | C 级逆 MTF Bias                      |
| 翻转强度 | ≥ 2/3     | 突破要干脆                           |
| 周期     | 15m 以上  | 小周期噪声大                         |
| 冷却     | 300s      | 同周期不重复开仓                     |

任一条不满足，弹窗和微信推送里会**逐条列出没下单的原因**。

### 止盈止损（网页可配）

```
开仓 → 浮盈 1.5% → 止盈 70% 仓位 → 止损抬到开仓价（保本）
                                  → 剩余 30% 等反向信号全平
```

- **止损**：默认用超趋线本身（SuperTrend 就是跟踪止损），也可改固定百分比
- **移动止损**：剩余仓位跟随超趋线推进，只朝有利方向移
- **反向信号**：先平掉旧仓，再按闸门决定要不要反手
- 止盈止损走**市价 reduceOnly** —— 限价挂上去不成交等于没止损

⚠️ **1.5% 是价格幅度，不是保证金收益率**。10x 杠杆下对应保证金 15%。这样规则不随杠杆漂移。

### 合约参数

杠杆 1~20x 可调，保证金每笔默认 10 USDT，名义价值 = 保证金 × 杠杆。

OKX 合约的 `sz` 单位是**张**，1 张 = `ctVal` 个币（BTC 是 0.01）。张数 = 名义价值 ÷ 价格 ÷ ctVal —— 这步漏了会差几十倍，是 OKX 最容易踩的坑。所有精度（tickSz / lotSz / minSz / ctVal）都从 `/api/v5/public/instruments` 实时拉取，不写死。

### 安全设计

- 总开关默认**关**，环境默认**模拟盘**
- 切实盘需二次确认，全程红色警示条
- `.env` 已在 `.gitignore`，密钥不进版本库
- 持仓只在内存，重启清空；面板有「查账户」和交易所持仓对账

---

## 项目结构

```
backend/
  indicators.py   Pine 逐行移植：ta_tr/ta_rma/ta_atr/super_trend/bias
  strategy.py     MTF Bias 表、信号分级 A/B/C
  backtest.py     回测 + 参数寻优网格
  feed.py         OKX WS 采集，收盘判翻转 → 闸门 → 广播/下单/推送
  history.py      REST 历史 K 线（after 游标翻页）
  regime.py       效率比 ER 行情判定 + 下单闸门 + TradeConfig
  trade.py        OKX v5 下单（签名/张数换算/杠杆/模拟盘）
  position.py     持仓状态机 + 止盈止损规则
  executor.py     信号/价格 → 开仓、分批止盈、保本、移动止损、平仓
  router.py       HTTP API + /ws
  state.py        全局状态、TF_CONFIG、Params（对应 Pine input）
  notify.py       Server酱推送
  instruments.py  品种搜索
frontend/src/
  components/
    CandleChart.jsx  主图（PLOTS / FILLS / BAR COLOR）
    superTrendPrimitive.js  超趋线断线 + 高亮区自绘
    BiasTable.jsx    MTF Bias 表
    SignalList.jsx   买卖点流水
    SignalModal.jsx  信号醒目弹窗 + 提示音
    TradePanel.jsx   自动挂单 / 止盈止损 / 持仓
    BacktestPanel.jsx / ParamPanel.jsx / TickerBar.jsx / ...
  hooks/useWebSocket.js
  stores/useStore.js
```

## API

| 方法     | 路径                      | 说明                                                   |
| -------- | ------------------------- | ------------------------------------------------------ |
| GET      | `/api/indicators?tf=1h`   | 单周期 SuperTrend 全量（轨道/趋势/信号/状态）          |
| GET      | `/api/overview`           | MTF Bias 表 + 各周期 ST 方向                           |
| GET      | `/api/signals?tf=&limit=` | 历史买卖点                                             |
| GET/POST | `/api/params`             | 读写参数（对应 Pine input）                            |
| POST     | `/api/backtest`           | 回测                                                   |
| POST     | `/api/sweep`              | 参数寻优网格                                           |
| GET/POST | `/api/symbol`             | 读取 / 切换品种                                        |
| GET      | `/api/instruments?q=`     | 品种搜索                                               |
| GET      | `/api/regime?tf=`         | 行情状态（震荡 / 弱趋势 / 趋势）                       |
| GET/POST | `/api/trade/config`       | 挂单配置（开关/杠杆/金额/闸门）                        |
| GET/POST | `/api/trade/exit-rules`   | 止盈止损规则                                           |
| GET      | `/api/trade/position`     | 当前持仓 + 已平仓历史                                  |
| POST     | `/api/trade/close`        | 手动市价全平                                           |
| GET      | `/api/trade/ping`         | 密钥自检                                               |
| POST     | `/api/trade/test-order`   | 测试单（盘口 ±3%，不成交）                             |
| WS       | `/ws`                     | snapshot / ticker / candle / signal / order / position |

信号只在 K 线 **收盘确认**（`confirm=1`）时判定 —— 未收盘价格来回穿轨会导致信号反复出现又消失（repaint）。

---

## 说明

指标与回测仅用于行情研究，不构成投资建议。回测是样本内结果，实盘还有滑点、流动性、资金费率等因素。

**自动挂单会真实下单。** 默认指向模拟盘，切实盘前请先在模拟盘跑几天，确认逻辑、参数、
止盈止损都符合预期。杠杆会同时放大盈利和亏损，10x 下价格反向 10% 即爆仓。风险自负。

原 Pine 脚本以 MPL-2.0 授权，© Quantum Edge Capital LLC。

② 服务器上装依赖并启动：

cd /opt/supertrend
sudo chown -R admin:admin .
sudo apt install -y python3-venv python3-pip nodejs npm # Ubuntu 22.04 自带 python3.10，够用
bash start.sh
