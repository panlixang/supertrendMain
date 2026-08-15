/**
 * SuperTrend 自绘图层 —— 对应 Pine 的 PLOTS + FILLS：
 *   upPlot = plot(trend ==  1 ? up : na, style = plot.style_linebr, color = C_Bull)
 *   dnPlot = plot(trend == -1 ? dn : na, style = plot.style_linebr, color = C_Bear)
 *   fill(mPlot, upPlot) / fill(mPlot, dnPlot)      // mPlot = ohlc4
 *
 * 为什么不用两个 LineSeries：
 * lightweight-charts v4 的折线遇到 whitespace（只有 time 没有 value）不会断开，
 * 而是把缺口两端直连 —— 趋势切换处会拉出一条横跨整段的斜线，正是 style_linebr
 * 要避免的。所以这里用 ISeriesPrimitive 自己画：按趋势切段，段与段之间不连笔。
 */

const FILL_BULL = 'rgba(0, 201, 167, 0.15)';   // C_FillBull（transp 85）
const FILL_BEAR = 'rgba(139, 0, 0, 0.22)';     // C_FillBear（transp 80）
const LINE_BULL = '#00c9a7';                   // C_Bull
const LINE_BEAR = '#c0392b';                   // C_Bear（#8b0000 在暗色图上偏暗，提亮一档）

/** 把逐点数据按 dir 切成连续同向的段，方向变化处断开 */
function segments(pts) {
  const out = [];
  let cur = null;
  for (const p of pts) {
    if (!p) {
      cur = null;
      continue;
    }
    if (!cur || cur.dir !== p.dir) {
      cur = { dir: p.dir, items: [] };
      out.push(cur);
    }
    cur.items.push(p);
  }
  return out.filter((s) => s.items.length);
}

class Renderer {
  constructor(src) {
    this._src = src;
  }

  draw(target) {
    const { _data: data, _series: series, _chart: chart, _showFill: showFill } = this._src;
    if (!data?.length || !series || !chart) return;

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const ts = chart.timeScale();
      const rx = scope.horizontalPixelRatio;
      const ry = scope.verticalPixelRatio;

      // 先转坐标，顺带把不可见/无值的点变成 null（= 断点）
      const pts = data.map((d) => {
        if (d.line == null || d.dir == null) return null;
        const x = ts.timeToCoordinate(d.time);
        const yl = series.priceToCoordinate(d.line);
        if (x == null || yl == null) return null;
        const ym = d.mid == null ? null : series.priceToCoordinate(d.mid);
        return {
          x: x * rx,
          yl: yl * ry,
          ym: ym == null ? null : ym * ry,
          dir: d.dir,
        };
      });

      const segs = segments(pts);

      // 1) 高亮区：ohlc4 与趋势线之间
      if (showFill) {
        for (const seg of segs) {
          const it = seg.items.filter((p) => p.ym != null);
          if (it.length < 2) continue;
          ctx.beginPath();
          ctx.moveTo(it[0].x, it[0].ym);
          for (let i = 1; i < it.length; i++) ctx.lineTo(it[i].x, it[i].ym);
          for (let i = it.length - 1; i >= 0; i--) ctx.lineTo(it[i].x, it[i].yl);
          ctx.closePath();
          ctx.fillStyle = seg.dir === 1 ? FILL_BULL : FILL_BEAR;
          ctx.fill();
        }
      }

      // 2) 趋势线：每段独立描边，段间不连笔（style_linebr）
      ctx.lineWidth = 2 * ry;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'butt';
      for (const seg of segs) {
        const it = seg.items;
        ctx.beginPath();
        ctx.moveTo(it[0].x, it[0].yl);
        if (it.length === 1) {
          // 单点段画一小段横线，否则什么都看不见
          ctx.lineTo(it[0].x + 1.5 * rx, it[0].yl);
        } else {
          for (let i = 1; i < it.length; i++) ctx.lineTo(it[i].x, it[i].yl);
        }
        ctx.strokeStyle = seg.dir === 1 ? LINE_BULL : LINE_BEAR;
        ctx.stroke();
      }
    });
  }
}

class FillView {
  constructor(src) {
    this._r = new Renderer(src);
  }
  zOrder() {
    return 'bottom';   // 压在 K 线下面，不挡实体
  }
  renderer() {
    return this._r;
  }
}

export class SuperTrendPrimitive {
  constructor() {
    this._data = [];
    this._showFill = true;
    this._series = null;
    this._chart = null;
    this._requestUpdate = null;
    this._views = [new FillView(this)];
  }

  attached({ chart, series, requestUpdate }) {
    this._chart = chart;
    this._series = series;
    this._requestUpdate = requestUpdate;
  }

  detached() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  /** data: [{ time, line, mid, dir }]，line=当前趋势轨，mid=ohlc4，dir=±1 */
  setData(data, showFill = true) {
    this._data = data || [];
    this._showFill = showFill;
    this._requestUpdate?.();
  }

  updateAllViews() {}

  paneViews() {
    return this._views;
  }
}
