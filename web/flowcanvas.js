// Flow-field canvas: background contour, streamlines, body, markers and a draggable probe.
// The probe snaps to the nearest streamline point; all local values for every streamline
// point arrive with the data, so dragging needs no round trip to Python.

const MAPS = {
  cp: ["#053061", "#2166ac", "#4393c3", "#92c5de", "#d1e5f0", "#f7f7f7", "#fddbc7", "#f4a582", "#d6604d", "#b2182b", "#67001f"],
  speed_ratio: ["#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"],
  mach: ["#0d0887", "#46039f", "#7201a8", "#9c179e", "#bd3786", "#d8576b", "#ed7953", "#fb9f3a", "#fdca26", "#f0f921"],
};
const LABELS = { cp: "Pressure coefficient Cp", speed_ratio: "Speed V/V∞", mach: "Local Mach number" };

function hexToRgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const LUT = {};
for (const [k, stops] of Object.entries(MAPS)) {
  const rgb = stops.map(hexToRgb);
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = (i / 255) * (rgb.length - 1);
    const a = Math.min(Math.floor(t), rgb.length - 2);
    const f = t - a;
    for (let c = 0; c < 3; c++) lut[i * 3 + c] = rgb[a][c] + (rgb[a + 1][c] - rgb[a][c]) * f;
  }
  LUT[k] = lut;
}
export function colorAt(kind, t) {
  const lut = LUT[kind];
  const i = Math.max(0, Math.min(255, Math.round(t * 255))) * 3;
  return `rgb(${lut[i]},${lut[i + 1]},${lut[i + 2]})`;
}

function percentile(values, p) {
  const v = Array.from(values).filter(Number.isFinite).sort((a, b) => a - b);
  if (!v.length) return 0;
  return v[Math.min(v.length - 1, Math.max(0, Math.round((p / 100) * (v.length - 1))))];
}

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export class FlowCanvas {
  constructor(canvas, { onProbe, xLabel = "x/c", yLabel = "y/c" } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.onProbe = onProbe || (() => {});
    this.xLabel = xLabel;
    this.yLabel = yLabel;
    this.field = null;
    this.view = null;
    this.colorBySpeed = false;
    this.probeIndex = -1;
    this.lineIndex = -1;
    this.lineFrac = 0.06;
    this.bgImage = null;
    this._drag = null;
    canvas.addEventListener("pointerdown", (e) => this._down(e));
    canvas.addEventListener("pointermove", (e) => this._move(e));
    canvas.addEventListener("pointerup", (e) => this._up(e));
    canvas.addEventListener("pointercancel", (e) => this._up(e));
    canvas.addEventListener("wheel", (e) => this._wheel(e), { passive: false });
    canvas.addEventListener("dblclick", () => this.resetView());
    new ResizeObserver(() => this.draw()).observe(canvas);
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => this.draw());
  }

  // ---------------------------------------------------------------- data
  setField(field, { keepView = true } = {}) {
    const prevLines = this.field ? this.field.line_offsets.length : -1;
    this.field = field;
    if (!this.view || !keepView) this.resetView(false);
    this._buildBackground();
    const nLines = field.line_offsets.length - 1;
    if (this.lineIndex < 0 || this.lineIndex >= nLines || prevLines !== field.line_offsets.length) {
      this.lineIndex = this._defaultLine();
      this.lineFrac = 0.06;
    }
    this._placeOnLine(this.lineIndex, this.lineFrac);
    this.draw();
    this._emit();
  }

  _defaultLine() {
    const f = this.field;
    const n = f.line_offsets.length - 1;
    if (n <= 0) return -1;
    let xMid = 0.5, yTop = 0;
    if (f.outlines.length) {
      const [ox, oy] = f.outlines[0];
      let xmin = Infinity, xmax = -Infinity;
      yTop = -Infinity;
      for (let i = 0; i < ox.length; i++) {
        xmin = Math.min(xmin, ox[i]); xmax = Math.max(xmax, ox[i]); yTop = Math.max(yTop, oy[i]);
      }
      xMid = 0.5 * (xmin + xmax);
    } else {
      xMid = 0; yTop = 0;
    }
    let best = 0, bestD = Infinity;
    for (let l = 0; l < n; l++) {
      const a = f.line_offsets[l], b = f.line_offsets[l + 1];
      let k = a, dk = Infinity;
      for (let i = a; i < b; i++) {
        const d = Math.abs(f.x[i] - xMid);
        if (d < dk) { dk = d; k = i; }
      }
      const dy = f.y[k] - yTop;
      if (dk < 0.25 && dy > 0 && dy < bestD) { bestD = dy; best = l; }
    }
    return best;
  }

  _lineRange(l) {
    return [this.field.line_offsets[l], this.field.line_offsets[l + 1]];
  }

  _arcLengths(l) {
    const [a, b] = this._lineRange(l);
    const s = new Float64Array(b - a);
    for (let i = a + 1; i < b; i++) {
      s[i - a] = s[i - a - 1] + Math.hypot(this.field.x[i] - this.field.x[i - 1], this.field.y[i] - this.field.y[i - 1]);
    }
    return s;
  }

  _placeOnLine(l, frac) {
    if (l < 0) { this.probeIndex = -1; return; }
    const s = this._arcLengths(l);
    const target = frac * s[s.length - 1];
    let k = 0;
    while (k < s.length - 1 && s[k] < target) k++;
    this.lineIndex = l;
    this.probeIndex = this._lineRange(l)[0] + k;
  }

  _snap(x, y) {
    const f = this.field;
    let best = -1, bd = Infinity;
    for (let i = 0; i < f.x.length; i++) {
      const d = (f.x[i] - x) ** 2 + (f.y[i] - y) ** 2;
      if (d < bd) { bd = d; best = i; }
    }
    if (best < 0) return;
    let l = 0;
    while (f.line_offsets[l + 1] <= best) l++;
    this.lineIndex = l;
    this.probeIndex = best;
    const s = this._arcLengths(l);
    this.lineFrac = s[best - f.line_offsets[l]] / (s[s.length - 1] || 1);
    this.draw();
    this._emit();
  }

  probeState() {
    if (!this.field || this.probeIndex < 0) return null;
    const i = this.probeIndex, st = this.field.state;
    const out = { x: this.field.x[i], y: this.field.y[i] };
    for (const k of Object.keys(st)) out[k] = st[k][i];
    return out;
  }

  currentLine() {
    if (!this.field || this.lineIndex < 0) return null;
    const [a, b] = this._lineRange(this.lineIndex);
    return { start: a, end: b };
  }

  _emit() {
    this.onProbe(this.probeState(), this.currentLine());
  }

  // ---------------------------------------------------------------- view
  resetView(redraw = true) {
    if (!this.field) return;
    const [x0, x1, y0, y1] = this.field.bounds;
    this.view = { x0, x1, y0, y1 };
    if (redraw) this.draw();
  }

  zoom(factor, cx, cy) {
    const v = this.view;
    if (!v) return;
    cx = cx ?? (v.x0 + v.x1) / 2;
    cy = cy ?? (v.y0 + v.y1) / 2;
    this.view = { x0: cx + (v.x0 - cx) * factor, x1: cx + (v.x1 - cx) * factor,
                  y0: cy + (v.y0 - cy) * factor, y1: cy + (v.y1 - cy) * factor };
    this.draw();
  }

  _transform() {
    const c = this.canvas, dpr = window.devicePixelRatio || 1;
    const W = c.clientWidth, H = c.clientHeight;
    // narrow screens: no colour bar, legend or readout box (the probe table shows the values)
    const compact = W < 520;
    const pad = compact ? { l: 34, r: 8, t: 6, b: 30 }
      : { l: 46, r: this.field && this.field.grid ? 92 : 14, t: 10, b: 34 };
    const v = this.view;
    const sx = (W - pad.l - pad.r) / (v.x1 - v.x0);
    const sy = (H - pad.t - pad.b) / (v.y1 - v.y0);
    const s = Math.min(sx, sy);
    const ox = pad.l + (W - pad.l - pad.r - s * (v.x1 - v.x0)) / 2;
    const oy = pad.t + (H - pad.t - pad.b - s * (v.y1 - v.y0)) / 2;
    return {
      W, H, dpr, s, pad, compact,
      X: (x) => ox + (x - v.x0) * s,
      Y: (y) => oy + (v.y1 - y) * s,
      ix: (px) => v.x0 + (px - ox) / s,
      iy: (py) => v.y1 - (py - oy) / s,
      box: [ox, oy, s * (v.x1 - v.x0), s * (v.y1 - v.y0)],
    };
  }

  _eventXY(e) {
    const r = this.canvas.getBoundingClientRect();
    const t = this._transform();
    return [t.ix(e.clientX - r.left), t.iy(e.clientY - r.top)];
  }

  _down(e) {
    if (!this.field) return;
    this.canvas.setPointerCapture(e.pointerId);
    if (e.shiftKey || e.button === 1 || this.panMode) {
      this._drag = { pan: true, start: this._eventXY(e), view: { ...this.view } };
    } else {
      this._drag = { pan: false };
      this._snap(...this._eventXY(e));
    }
  }

  _move(e) {
    if (!this._drag) return;
    if (this._drag.pan) {
      const r = this.canvas.getBoundingClientRect();
      const t = this._transform();
      const v0 = this._drag.view;
      // pan relative to the starting view
      const [sx, sy] = this._drag.start;
      const px = e.clientX - r.left, py = e.clientY - r.top;
      const curX = v0.x0 + (px - t.box[0]) / t.s;
      const curY = v0.y1 - (py - t.box[1]) / t.s;
      const dx = curX - sx, dy = curY - sy;
      this.view = { x0: v0.x0 - dx, x1: v0.x1 - dx, y0: v0.y0 - dy, y1: v0.y1 - dy };
      this.draw();
    } else {
      this._snap(...this._eventXY(e));
    }
  }

  _up() {
    this._drag = null;
  }

  _wheel(e) {
    if (!this.field) return;
    e.preventDefault();
    const [x, y] = this._eventXY(e);
    this.zoom(e.deltaY > 0 ? 1.15 : 1 / 1.15, x, y);
  }

  // ---------------------------------------------------------------- drawing
  _buildBackground() {
    const g = this.field.grid;
    this.bgImage = null;
    this.bgRange = null;
    if (!g) return;
    const vals = g.values;
    let lo, hi;
    if (g.kind === "cp") {
      const lim = Math.max(Math.abs(percentile(vals, 1)), Math.abs(percentile(vals, 99)), 0.05);
      lo = -lim; hi = lim;
    } else {
      lo = percentile(vals, 0.5); hi = percentile(vals, 99.5);
      if (hi <= lo) hi = lo + 1e-3;
    }
    const off = document.createElement("canvas");
    off.width = g.nx; off.height = g.ny;
    const octx = off.getContext("2d");
    const img = octx.createImageData(g.nx, g.ny);
    const lut = LUT[g.kind];
    for (let j = 0; j < g.ny; j++) {
      for (let i = 0; i < g.nx; i++) {
        const v = vals[j * g.nx + i];
        const p = ((g.ny - 1 - j) * g.nx + i) * 4;   // image rows run top to bottom
        if (!Number.isFinite(v)) { img.data[p + 3] = 0; continue; }
        const t = Math.max(0, Math.min(255, Math.round(((v - lo) / (hi - lo)) * 255))) * 3;
        img.data[p] = lut[t]; img.data[p + 1] = lut[t + 1]; img.data[p + 2] = lut[t + 2]; img.data[p + 3] = 225;
      }
    }
    octx.putImageData(img, 0, 0);
    this.bgImage = off;
    this.bgRange = [lo, hi, g.kind];
  }

  draw() {
    const c = this.canvas, f = this.field;
    if (!f || !this.view) return;
    if (c.clientWidth < 50 || c.clientHeight < 50) return;   // hidden tab: drawn when it becomes visible
    const t = this._transform();
    if (c.width !== Math.round(t.W * t.dpr) || c.height !== Math.round(t.H * t.dpr)) {
      c.width = Math.round(t.W * t.dpr);
      c.height = Math.round(t.H * t.dpr);
    }
    const ctx = this.ctx;
    ctx.setTransform(t.dpr, 0, 0, t.dpr, 0, 0);
    ctx.clearRect(0, 0, t.W, t.H);
    const fg = css("--text"), muted = css("--muted"), body = css("--body");
    // over a coloured background (light in the middle of every colour map) streamlines are drawn dark
    const lineCol = this.bgImage ? "rgba(20, 26, 36, 0.72)" : css("--streamline");

    ctx.save();
    ctx.beginPath();
    ctx.rect(...t.box);
    ctx.clip();
    ctx.fillStyle = css("--plot-bg");
    ctx.fillRect(...t.box);
    if (this.bgImage) {
      const [x0, x1, y0, y1] = f.bounds;
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(this.bgImage, t.X(x0), t.Y(y1), t.X(x1) - t.X(x0), t.Y(y0) - t.Y(y1));
    }
    // streamlines
    const n = f.line_offsets.length - 1;
    const sr = f.state.speed_ratio;
    let srMin = Infinity, srMax = -Infinity;
    if (this.colorBySpeed) for (const v of sr) if (Number.isFinite(v)) { srMin = Math.min(srMin, v); srMax = Math.max(srMax, v); }
    ctx.lineWidth = 1;
    for (let l = 0; l < n; l++) {
      const [a, b] = this._lineRange(l);
      if (this.colorBySpeed) {
        for (let i = a + 1; i < b; i++) {
          ctx.strokeStyle = colorAt("speed_ratio", (sr[i] - srMin) / (srMax - srMin || 1));
          ctx.beginPath();
          ctx.moveTo(t.X(f.x[i - 1]), t.Y(f.y[i - 1]));
          ctx.lineTo(t.X(f.x[i]), t.Y(f.y[i]));
          ctx.stroke();
        }
      } else {
        ctx.strokeStyle = lineCol;
        ctx.beginPath();
        ctx.moveTo(t.X(f.x[a]), t.Y(f.y[a]));
        for (let i = a + 1; i < b; i++) ctx.lineTo(t.X(f.x[i]), t.Y(f.y[i]));
        ctx.stroke();
      }
      if (l % 3 === 0 && b - a > 8) this._arrow(t, a + Math.floor((b - a) / 4), lineCol);
    }
    // Mach waves and the propeller disk
    ctx.setLineDash([6, 4]);
    ctx.strokeStyle = "#d03030";
    ctx.lineWidth = 1.3;
    for (const [mx, my] of f.mach_lines) {
      ctx.beginPath();
      ctx.moveTo(t.X(mx[0]), t.Y(my[0]));
      for (let i = 1; i < mx.length; i++) ctx.lineTo(t.X(mx[i]), t.Y(my[i]));
      ctx.stroke();
    }
    ctx.setLineDash([]);
    if (f.disk) {
      ctx.strokeStyle = fg;
      ctx.lineWidth = 5;
      ctx.beginPath();
      ctx.moveTo(t.X(0), t.Y(-1));
      ctx.lineTo(t.X(0), t.Y(1));
      ctx.stroke();
      ctx.fillStyle = fg;
      ctx.font = "12px system-ui, sans-serif";
      ctx.fillText("propeller disk", t.X(0) + 6, t.Y(1) - 6);
    }
    // bodies
    for (const [ox, oy] of f.outlines) {
      ctx.beginPath();
      ctx.moveTo(t.X(ox[0]), t.Y(oy[0]));
      for (let i = 1; i < ox.length; i++) ctx.lineTo(t.X(ox[i]), t.Y(oy[i]));
      ctx.closePath();
      ctx.fillStyle = body;
      ctx.fill();
      ctx.strokeStyle = fg;
      ctx.lineWidth = 1;
      ctx.stroke();
    }
    // boundary-layer markers
    for (const m of f.markers) this._marker(t, m);
    // probe and its streamline
    if (this.probeIndex >= 0) {
      const [a, b] = this._lineRange(this.lineIndex);
      ctx.strokeStyle = "#ff2d2d";
      ctx.lineWidth = 2.4;
      ctx.beginPath();
      ctx.moveTo(t.X(f.x[a]), t.Y(f.y[a]));
      for (let i = a + 1; i < b; i++) ctx.lineTo(t.X(f.x[i]), t.Y(f.y[i]));
      ctx.stroke();
      const px = t.X(f.x[this.probeIndex]), py = t.Y(f.y[this.probeIndex]);
      ctx.beginPath();
      ctx.arc(px, py, 8, 0, 2 * Math.PI);
      ctx.fillStyle = "#ff2d2d";
      ctx.fill();
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = "#ffffff";
      ctx.stroke();
    }
    ctx.restore();

    this._axes(t, fg, muted);
    if (t.compact) return;
    this._legend(t, fg);
    this._colorbar(t, fg);
    this._readout(t);
  }

  _arrow(t, i, color) {
    const f = this.field, ctx = this.ctx;
    const x0 = t.X(f.x[i]), y0 = t.Y(f.y[i]), x1 = t.X(f.x[i + 1]), y1 = t.Y(f.y[i + 1]);
    const ang = Math.atan2(y1 - y0, x1 - x0);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(x1 + 6 * Math.cos(ang), y1 + 6 * Math.sin(ang));
    ctx.lineTo(x1 + 6 * Math.cos(ang + 2.6), y1 + 6 * Math.sin(ang + 2.6));
    ctx.lineTo(x1 + 6 * Math.cos(ang - 2.6), y1 + 6 * Math.sin(ang - 2.6));
    ctx.closePath();
    ctx.fill();
  }

  _marker(t, m) {
    const ctx = this.ctx, x = t.X(m.x), y = t.Y(m.y);
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = "#111";
    if (m.kind === "stagnation") {
      ctx.fillStyle = "#ffd60a";
      ctx.beginPath(); ctx.arc(x, y, 5.5, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
    } else if (m.kind === "transition") {
      ctx.fillStyle = "#29a329";
      ctx.beginPath(); ctx.moveTo(x, y - 7); ctx.lineTo(x - 6, y + 4); ctx.lineTo(x + 6, y + 4); ctx.closePath();
      ctx.fill(); ctx.stroke();
    } else {
      ctx.strokeStyle = "#e03030"; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(x - 6, y - 6); ctx.lineTo(x + 6, y + 6); ctx.moveTo(x + 6, y - 6); ctx.lineTo(x - 6, y + 6);
      ctx.stroke();
    }
  }

  _legend(t, fg) {
    const kinds = [...new Set(this.field.markers.map((m) => m.kind))];
    if (!kinds.length) return;
    const ctx = this.ctx;
    const [bx, by, bw, bh] = t.box;
    const w = 112, h = 8 + 18 * kinds.length;
    const x = bx + bw - w - 6, y = by + bh - h - 6;
    ctx.fillStyle = css("--panel");
    ctx.globalAlpha = 0.88;
    ctx.fillRect(x, y, w, h);
    ctx.globalAlpha = 1;
    kinds.forEach((k, i) => {
      this._marker({ X: () => x + 12, Y: () => y + 13 + 18 * i }, { kind: k, x: 0, y: 0 });
      ctx.fillStyle = fg;
      ctx.font = "12px system-ui, sans-serif";
      ctx.fillText(k === "stagnation" ? "stagnation point" : k, x + 24, y + 17 + 18 * i);
    });
  }

  _axes(t, fg, muted) {
    const ctx = this.ctx, v = this.view;
    const [bx, by, bw, bh] = t.box;
    ctx.strokeStyle = muted;
    ctx.lineWidth = 1;
    ctx.strokeRect(bx, by, bw, bh);
    ctx.fillStyle = fg;
    ctx.font = "11px system-ui, sans-serif";
    const step = niceStep((v.x1 - v.x0) / 6);
    ctx.textAlign = "center";
    for (let x = Math.ceil(v.x0 / step) * step; x <= v.x1 + 1e-9; x += step) {
      ctx.fillText(+x.toFixed(3), t.X(x), by + bh + 14);
    }
    ctx.fillText(this.xLabel, bx + bw / 2, by + bh + 29);
    ctx.textAlign = "right";
    const stepY = niceStep((v.y1 - v.y0) / 5);
    for (let y = Math.ceil(v.y0 / stepY) * stepY; y <= v.y1 + 1e-9; y += stepY) {
      ctx.fillText(+y.toFixed(3), bx - 5, t.Y(y) + 4);
    }
    ctx.save();
    ctx.translate(12, by + bh / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = "center";
    ctx.fillText(this.yLabel, 0, 0);
    ctx.restore();
    ctx.textAlign = "left";
  }

  _colorbar(t, fg) {
    if (!this.bgRange) return;
    const [lo, hi, kind] = this.bgRange;
    const ctx = this.ctx;
    const [bx, by, bw, bh] = t.box;
    const x = bx + bw + 12, w = 12, h = Math.min(bh, 260), y = by + (bh - h) / 2;
    for (let i = 0; i < h; i++) {
      ctx.fillStyle = colorAt(kind, 1 - i / h);
      ctx.fillRect(x, y + i, w, 1.5);
    }
    ctx.fillStyle = fg;
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillText(fmtNum(hi), x + w + 3, y + 8);
    ctx.fillText(fmtNum((lo + hi) / 2), x + w + 3, y + h / 2 + 4);
    ctx.fillText(fmtNum(lo), x + w + 3, y + h);
    ctx.save();
    ctx.translate(x + w + 46, y + h / 2);
    ctx.rotate(Math.PI / 2);
    ctx.textAlign = "center";
    ctx.fillText(LABELS[kind], 0, 0);
    ctx.restore();
  }

  _readout(t) {
    const st = this.probeState();
    if (!st) return;
    const ctx = this.ctx;
    const lines = [
      `V    ${st.speed.toFixed(2)} m/s`,
      `V/V∞ ${st.speed_ratio.toFixed(3)}`,
      `M    ${st.mach.toFixed(3)}`,
      `Cp   ${st.cp.toFixed(3)}`,
      `p    ${(st.pressure / 1000).toFixed(2)} kPa`,
    ];
    const [bx, by] = t.box;
    ctx.font = "12px ui-monospace, Menlo, Consolas, monospace";
    const w = 150, h = 10 + 16 * lines.length;
    ctx.fillStyle = css("--panel");
    ctx.globalAlpha = 0.9;
    ctx.fillRect(bx + 6, by + 6, w, h);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = css("--muted");
    ctx.strokeRect(bx + 6, by + 6, w, h);
    ctx.fillStyle = css("--text");
    lines.forEach((s, i) => ctx.fillText(s, bx + 13, by + 22 + 16 * i));
  }
}

function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const m = raw / p;
  return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
}

export function fmtNum(v) {
  if (!Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(2);
  return (+v.toPrecision(3)).toString();
}
