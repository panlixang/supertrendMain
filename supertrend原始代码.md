//@version=5
indicator("Supertrend Native Code", overlay=true)

// 1. 输入参数
atrPeriod = input.int(10, "ATR Period", minval=1)
factor = input.float(3.0, "ATR Factor", minval=0.01, step=0.1)

// 2. 计算基础指标 (ATR 与 中轴价)
atr = ta.atr(atrPeriod)
hl2 = (high + low) / 2

// 3. 计算上下轨基本线
up = hl2 - (factor * atr)
dn = hl2 + (factor * atr)

// 4. 定义变量记录平滑后的轨线与趋势方向
var float trendUp = na
var float trendDown = na
var int trend = 1 // 1 表示多头，-1 表示空头

// 5. 递归平滑计算轨线 (保证上轨只升不降，下轨只降不升)
trendUp := close[1] > trendUp[1] ? math.max(up, trendUp[1]) : up
trendDown := close[1] < trendDown[1] ? math.min(dn, trendDown[1]) : dn

// 6. 趋势方向判断
trend := close > trendDown[1] ? 1 : (close < trendUp[1] ? -1 : nz(trend[1], 1))

// 7. 计算最终的 Supertrend 线
supertrend = trend == 1 ? trendUp : trendDown

// 8. 绘图
plot(supertrend, color = trend == 1 ? color.green : color.red, linewidth = 2, title = "Supertrend")