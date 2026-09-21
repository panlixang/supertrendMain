// 压力/支撑区间填充图层 —— 在 K 线下方画一条横向色带，标注价格与强度。
// 复用 SuperTrendPrimitive 同款 ISeriesPrimitive 接口（useBitmapCoordinateSpace）。
//
// zones: [{ priceTop, priceBottom, color, border, label, labelColor }]

class ZoneRenderer {
  constructor(src) {
    this._src = src;
  }

  draw(target) {
    const { _zones, _chart, _series } = this._src;
    if (!_zones?.length || !_chart || !_series) return;

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const rx = scope.horizontalPixelRatio;
      const ry = scope.verticalPixelRatio;
      const W = scope.mediaSize?.width || 0;

      for (const z of _zones) {
        const yT = _series.priceToCoordinate(z.priceTop);
        const yB = _series.priceToCoordinate(z.priceBottom);
        if (yT == null || yB == null) continue;

        const top = Math.min(yT, yB) * ry;
        const bot = Math.max(yT, yB) * ry;
        if (bot - top < 0.5) continue;

        // 色带
        ctx.fillStyle = z.color || 'rgba(255,255,255,0.08)';
        ctx.fillRect(0, top, W, bot - top);

        // 上下边界线
        ctx.strokeStyle = z.border || z.color || 'rgba(255,255,255,0.3)';
        ctx.lineWidth = 1 * rx;
        ctx.beginPath();
        ctx.moveTo(0, top); ctx.lineTo(W, top);
        ctx.moveTo(0, bot); ctx.lineTo(W, bot);
        ctx.stroke();

        // 价格标签（右上角）
        if (z.label) {
          ctx.fillStyle = z.labelColor || '#fff';
          ctx.font = `${Math.round(11 * ry)}px ui-monospace, monospace`;
          ctx.textAlign = 'right';
          ctx.textBaseline = 'top';
          ctx.fillText(z.label, W - 6 * rx, top + 3 * ry);
        }
      }
    });
  }
}

class ZoneView {
  constructor(src) {
    this._r = new ZoneRenderer(src);
  }
  zOrder() {
    return 'bottom'; // 压在 K 线下面
  }
  renderer() {
    return this._r;
  }
}

export class ZonePrimitive {
  constructor() {
    this._zones = [];
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
    this._views = [new ZoneView(this)];
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

  /** zones: [{ priceTop, priceBottom, color, border, label, labelColor }] */
  setZones(zones) {
    this._zones = zones || [];
    this._requestUpdate?.();
  }

  updateAllViews() {}

  paneViews() {
    return this._views;
  }
}
