// Virtual Wind Tunnel, browser version: user interface.
// The physics runs in Python (Pyodide) inside worker.js; this file builds the controls,
// sends parameters to the worker and draws the results.
import { FlowCanvas, fmtNum } from "./flowcanvas.js";

// ------------------------------------------------------------------ worker RPC
const worker = new Worker("worker.js");
const pending = new Map();
let nextId = 1;
let markReady;
const ready = new Promise((resolve) => (markReady = resolve));

worker.onmessage = (event) => {
  const m = event.data;
  if (m.type === "progress") {
    document.getElementById("loading-text").textContent = m.text;
    document.getElementById("loading-bar").style.width = `${m.pct}%`;
  } else if (m.type === "ready") {
    document.getElementById("loading-bar").style.width = "100%";
    markReady();
  } else if (m.type === "error") {
    const t = document.getElementById("loading-text");
    t.textContent = `Could not start the physics engine: ${m.text}`;
    t.className = "error";
  } else if (m.type === "result") {
    const p = pending.get(m.id);
    pending.delete(m.id);
    if (m.ok) p.resolve(decode(JSON.parse(m.data)));
    else p.reject(new Error(m.error));
  }
};

function call(fn, args) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, fn, args });
  });
}

function decode(o) {
  if (Array.isArray(o)) return o.map(decode);
  if (o && typeof o === "object") {
    if (typeof o.f32 === "string") {
      const bin = atob(o.f32);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return new Float32Array(bytes.buffer);
    }
    const out = {};
    for (const k of Object.keys(o)) out[k] = decode(o[k]);
    return out;
  }
  return o;
}

let busyCount = 0;
function setBusy(delta) {
  busyCount += delta;
  document.getElementById("busy").hidden = busyCount <= 0;
}

class Runner {
  constructor(fn, params, onResult, onError) {
    Object.assign(this, { fn, params, onResult, onError, busy: false, again: false, timer: 0 });
  }
  schedule(delay = 140) {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.run(), delay);
  }
  async run() {
    if (this.busy) { this.again = true; return; }
    this.busy = true;
    setBusy(1);
    try {
      this.onResult(await call(this.fn, this.params()));
    } catch (err) {
      this.onError(err);
    } finally {
      this.busy = false;
      setBusy(-1);
      if (this.again) { this.again = false; this.run(); }
    }
  }
}

// ------------------------------------------------------------------ DOM helpers
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) el.append(c instanceof Node ? c : document.createTextNode(c));
  return el;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function plotLayout(extra = {}) {
  const text = cssVar("--text"), grid = cssVar("--border");
  const axis = { gridcolor: grid, zerolinecolor: grid, linecolor: grid, automargin: true };
  return {
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: text, size: 11, family: "system-ui, sans-serif" },
    margin: { l: 50, r: 14, t: 28, b: 40 },
    legend: { orientation: "h", y: -0.38, yanchor: "top", font: { size: 10 } },
    xaxis: { ...axis, ...(extra.xaxis || {}) }, yaxis: { ...axis, ...(extra.yaxis || {}) },
    ...Object.fromEntries(Object.entries(extra).filter(([k]) => k !== "xaxis" && k !== "yaxis")),
  };
}
const PLOT_CONFIG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d"] };

function plot(el, traces, layout) {
  Plotly.react(el, traces, plotLayout(layout), PLOT_CONFIG);
}

function renderRows(table, rows) {
  table.replaceChildren(...rows.map((r) => (r.length === 1
    ? h("tr", { class: "head" }, h("td", { colspan: 2 }, r[0]))
    : h("tr", {}, h("td", {}, r[0]), h("td", {}, r[1])))));
}

function renderWarnings(box, items, error) {
  box.replaceChildren(...(error ? [h("div", { class: "error" }, `Error: ${error}`)] : []),
    ...(items || []).map((w) => h("div", {}, `⚠ ${w}`)));
}

// ------------------------------------------------------------------ controls
function isaSpeedOfSound(hm, dT) {
  const h = Math.min(Math.max(hm, -1000), 84000);
  let T;
  if (h < 11000) T = 288.15 - 0.0065 * h;
  else if (h < 20000) T = 216.65;
  else if (h < 32000) T = 216.65 + 0.001 * (h - 20000);
  else if (h < 47000) T = 228.65 + 0.0028 * (h - 32000);
  else T = 270.65;
  return Math.sqrt(1.4 * 287.05 * Math.max(T + dT, 1));
}
const TO_MS = { "m/s": 1, "km/h": 1 / 3.6, knots: 0.514444, mph: 0.44704 };
const FLOW_MODELS = ["Auto (from Mach number)", "Incompressible", "Compressible"];
const CORRECTIONS = ["Karman-Tsien", "Prandtl-Glauert", "Laitone"];
const TRANSITIONS = ["Free (Michel criterion)", "Forced (trip strips)", "Fully turbulent", "Fully laminar"];

function buildControls(container, spec, onChange) {
  const inputs = {};
  const infos = {};
  for (const item of spec) {
    if (item.type === "group") { container.append(h("h3", {}, item.label)); continue; }
    if (item.type === "info") { infos[item.key] = h("div", { class: "info" }); container.append(infos[item.key]); continue; }
    const id = `${container.id}-${item.key || item.type}`;
    if (item.type === "check") {
      const cb = h("input", { type: "checkbox", id, checked: item.value });
      cb.addEventListener("change", onChange);
      inputs[item.key] = { get: () => cb.checked, el: cb };
      container.append(h("label", { class: "check", for: id }, cb, item.label));
      continue;
    }
    const field = h("div", { class: "field" }, h("label", { for: id }, item.label));
    if (item.type === "range") {
      const range = h("input", { type: "range", min: item.min, max: item.max, step: item.step, value: item.value });
      const num = h("input", { type: "number", id, min: item.min, max: item.max, step: item.step, value: item.value });
      range.addEventListener("input", () => { num.value = range.value; onChange(); });
      num.addEventListener("change", () => { range.value = num.value; onChange(); });
      field.append(h("div", { class: "row" }, range, num, item.unit ? h("span", {}, item.unit) : null));
      inputs[item.key] = { get: () => parseFloat(num.value), el: num };
    } else if (item.type === "number") {
      const num = h("input", { type: "number", id, min: item.min, max: item.max, step: item.step, value: item.value });
      num.addEventListener("change", onChange);
      field.append(h("div", { class: "row" }, num, item.unit ? h("span", {}, item.unit) : null));
      inputs[item.key] = { get: () => parseFloat(num.value), el: num };
    } else if (item.type === "select") {
      const sel = h("select", { id }, item.options.map((o) => h("option", { value: o, selected: o === item.value }, o)));
      sel.addEventListener("change", onChange);
      field.append(sel);
      inputs[item.key] = { get: () => sel.value, el: sel };
    } else if (item.type === "airfoil") {
      const inp = h("input", { type: "text", id, list: "airfoil-list", value: item.value, spellcheck: "false" });
      inp.addEventListener("change", onChange);
      field.append(inp, h("div", { class: "info" }, "Pick from ~1,000 built-in airfoils or type any NACA code (e.g. 4415, 23012, 0012-34)."));
      inputs[item.key] = { get: () => inp.value.trim(), el: inp };
    } else if (item.type === "speed") {
      const num = h("input", { type: "number", id, min: item.allowZero ? 0 : 0.1, step: "any", value: item.value });
      const unit = h("select", {}, ["m/s", "km/h", "knots", "mph", "Mach"].map((u) => h("option", { value: u }, u)));
      let last = "m/s";
      unit.addEventListener("change", () => {
        const a = isaSpeedOfSound(inputs.altitude.get(), inputs.dT.get());
        const ms = last === "Mach" ? num.value * a : num.value * TO_MS[last];
        num.value = unit.value === "Mach" ? (ms / a).toFixed(3) : (ms / TO_MS[unit.value]).toFixed(2);
        last = unit.value;
        onChange();
      });
      num.addEventListener("change", onChange);
      field.append(h("div", { class: "row" }, num, unit));
      inputs.speed = { get: () => parseFloat(num.value), el: num };
      inputs.unit = { get: () => unit.value, el: unit };
      container.append(field);
      continue;
    }
    container.append(field);
  }
  return {
    values: () => Object.fromEntries(Object.entries(inputs).map(([k, v]) => [k, v.get()])),
    info: (key, text) => { if (infos[key]) infos[key].textContent = text; },
  };
}

const flowSpec = (speed, allowZero = false) => [
  { type: "group", label: "Free stream" },
  { type: "number", key: "altitude", label: "Altitude", min: -500, max: 80000, step: 500, value: 0, unit: "m" },
  { type: "number", key: "dT", label: "ISA temperature offset", min: -60, max: 60, step: 1, value: 0, unit: "K" },
  { type: "speed", label: "Speed", value: speed, allowZero },
  { type: "info", key: "freestream" },
];
const modelSpec = (stallLabel, withFlowModel) => [
  { type: "group", label: "Physics model" },
  ...(withFlowModel ? [{ type: "select", key: "flow_model", label: "Flow model", options: FLOW_MODELS, value: FLOW_MODELS[0] }] : []),
  { type: "select", key: "correction", label: "Compressibility rule", options: CORRECTIONS, value: CORRECTIONS[0] },
  { type: "check", key: "viscous", label: "Viscous effects (boundary layer, drag)", value: true },
  { type: "check", key: "stall", label: stallLabel, value: true },
  { type: "select", key: "transition", label: "Transition", options: TRANSITIONS, value: TRANSITIONS[0] },
  { type: "number", key: "trip_u", label: "Trip x/c upper (forced transition)", min: 0, max: 1, step: 0.05, value: 0.1 },
  { type: "number", key: "trip_l", label: "Trip x/c lower (forced transition)", min: 0, max: 1, step: 0.05, value: 0.1 },
];

// ------------------------------------------------------------------ flow panel (canvas + probe)
const ALONG = [["Speed V/V∞", "speed_ratio", 1], ["Speed (m/s)", "speed", 1], ["Local Mach", "mach", 1],
  ["Pressure coefficient Cp", "cp", 1], ["Static pressure (kPa)", "pressure", 1e-3],
  ["Temperature (K)", "temperature", 1], ["Density (kg/m³)", "density", 1]];

function flowPanel(parent, { xLabel = "x/c", yLabel = "y/c", cp = true, onOptions } = {}) {
  const bg = h("select", {}, [["none", "None"], ["cp", "Pressure coefficient Cp"], ["speed_ratio", "Speed V/V∞"],
    ["mach", "Local Mach number"]].map(([v, t]) => h("option", { value: v, selected: v === "cp" }, t)));
  const nLines = h("input", { type: "number", min: 6, max: 80, value: 30 });
  const colorCb = h("input", { type: "checkbox" });
  const panBtn = h("button", { title: "Toggle pan mode (or Shift-drag)" }, "Pan");
  const canvasEl = h("canvas", { class: "flow" });
  const viewbar = h("div", { class: "viewbar" },
    h("label", {}, "Background ", bg), h("label", {}, "Streamlines ", nLines),
    h("label", {}, colorCb, " colour by speed"),
    h("button", { onclick: () => fc.zoom(1 / 1.3) }, "+"), h("button", { onclick: () => fc.zoom(1.3) }, "−"),
    panBtn, h("button", { onclick: () => fc.resetView() }, "Reset view"));
  const tabBar = h("div", { class: "subtabs" });
  const panels = h("div");
  parent.append(h("div", { class: "card center" }, viewbar, canvasEl, tabBar, panels));

  const tabs = [];
  function addTab(name) {
    const panel = h("div", { class: "subpanel" });
    const index = tabs.length;
    const btn = h("button", { onclick: () => select(index) }, name);
    tabs.push({ btn, panel });
    tabBar.append(btn);
    panels.append(panel);
    if (tabs.length === 1) select(0);
    return panel;
  }
  function select(i) {
    tabs.forEach((t, k) => { t.btn.classList.toggle("active", k === i); t.panel.classList.toggle("active", k === i); });
    tabs[i].panel.querySelectorAll(".js-plotly-plot").forEach((p) => Plotly.Plots.resize(p));
  }

  // probe tab
  const probeTab = addTab("Probe");
  const probeTable = h("table", { class: "results" });
  const qty = h("select", {}, ALONG.map(([t], i) => h("option", { value: i }, t)));
  const alongChart = h("div", { class: "chart" });
  probeTab.append(h("div", { class: "probe-grid" }, probeTable,
    h("div", {}, h("div", { class: "viewbar" }, "Along the selected streamline: ", qty), alongChart)));

  let cpChart = null, cpData = null, surfPoint = null;
  if (cp) {
    const cpTab = addTab("Pressure distribution");
    cpChart = h("div", { class: "chart" });
    cpTab.append(cpChart);
  }

  let lineKey = null, frame = 0, lastProbe = null, lastLine = null;
  const fc = new FlowCanvas(canvasEl, {
    xLabel, yLabel,
    onProbe: (st, line) => {
      lastProbe = st; lastLine = line;
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; updateProbe(); });
    },
  });

  function updateProbe(force = false) {
    const st = lastProbe, line = lastLine, f = fc.field;
    if (!st || !line) return;
    const v0 = f.freestream_speed, p0 = f.freestream_pressure;
    const rows = [["Probe position"], ["x, y", `${st.x.toFixed(3)}, ${st.y.toFixed(3)}`], ["Local flow"],
      ["Speed V", `${fmtNum(st.speed)} m/s`], ["V / V∞", fmtNum(st.speed_ratio)],
      ["Change from free stream", `${fmtNum(st.speed - v0)} m/s`], ["Local Mach number", fmtNum(st.mach)],
      ["Pressure coefficient Cp", fmtNum(st.cp)], ["Static pressure", `${fmtNum(st.pressure / 1000)} kPa`],
      ["Δp from free stream", `${fmtNum(st.pressure - p0)} Pa`], ["Total pressure", `${fmtNum(st.total_pressure / 1000)} kPa`],
      ["Temperature", `${st.temperature.toFixed(2)} K (${(st.temperature - 273.15).toFixed(2)} °C)`],
      ["Density", `${fmtNum(st.density)} kg/m³`]];
    if (st.mach >= 1) rows.push(["Note", "locally SUPERSONIC (M ≥ 1)"]);
    renderRows(probeTable, rows);
    const [name, key, scale] = ALONG[qty.value];
    const k = `${line.start}-${line.end}-${key}-${f.x.length}`;
    const xs = f.x.subarray(line.start, line.end);
    const ys = Array.from(f.state[key].subarray(line.start, line.end), (v) => v * scale);
    const marker = { x: [st.x], y: [st[key] * scale], mode: "markers", marker: { size: 10, color: "#ff2d2d", line: { color: "#fff", width: 2 } }, name: "probe" };
    if (force || k !== lineKey) {
      lineKey = k;
      plot(alongChart, [
        { x: xs, y: ys, mode: "lines", line: { color: cssVar("--accent"), width: 2 }, name },
        { x: [xs[0], xs[xs.length - 1]], y: [ys[0], ys[0]], mode: "lines", line: { dash: "dot", color: cssVar("--muted"), width: 1 }, name: "free stream" },
        marker,
      ], { xaxis: { title: { text: xLabel } }, yaxis: { title: { text: name }, autorange: key === "cp" ? "reversed" : true },
        showlegend: true, margin: { l: 56, r: 10, t: 10, b: 88 } });
    } else {
      Plotly.restyle(alongChart, { x: [marker.x], y: [marker.y] }, [2]);
    }
  }
  qty.addEventListener("change", () => updateProbe(true));

  function drawCp() {
    if (!cpChart || !cpData) return;
    const c = cpData, traces = [];
    const colors = { upper: "#1f6fd1", lower: "#d1491f" };
    for (const side of ["upper", "lower"]) {
      traces.push({ x: c[side].x, y: c[side].cp, mode: "lines", name: `${side} surface`, line: { color: colors[side], width: 2 } });
      if (c.model === "subsonic") traces.push({ x: c[side].x, y: c[side].cp0, mode: "lines", name: `${side}, incompressible`, line: { color: colors[side], width: 1, dash: "dash" } });
    }
    if (c.cp_crit != null) traces.push({ x: [0, 1], y: [c.cp_crit, c.cp_crit], mode: "lines", name: `Cp* (sonic) = ${c.cp_crit.toFixed(2)}`, line: { color: "purple", dash: "dot" } });
    if (surfPoint) traces.push({ x: [surfPoint.x], y: [surfPoint.cp], mode: "markers", name: "surface point", marker: { size: 9, color: "#ff2d2d", line: { color: "#fff", width: 1.5 } } });
    plot(cpChart, traces, { xaxis: { title: { text: "x/c" } }, yaxis: { title: { text: "Cp" }, autorange: "reversed" }, margin: { l: 50, r: 10, t: 10, b: 88 } });
  }

  const opts = () => ({ background: bg.value, n_lines: parseInt(nLines.value, 10) || 30 });
  bg.addEventListener("change", () => onOptions && onOptions());
  nLines.addEventListener("change", () => onOptions && onOptions());
  colorCb.addEventListener("change", () => { fc.colorBySpeed = colorCb.checked; fc.draw(); });
  panBtn.addEventListener("click", () => { fc.panMode = !fc.panMode; panBtn.style.borderColor = fc.panMode ? cssVar("--accent") : ""; });

  return {
    fc, addTab, select, options: opts,
    setField(field, keepView = true) { fc.setField(field, { keepView }); },
    setCp(data, point) { cpData = data; surfPoint = point || null; drawCp(); },
  };
}

// ------------------------------------------------------------------ layout helper
function tabLayout(name) {
  const section = h("section", { class: "tab", id: `tab-${name}` });
  const controls = h("aside", { class: "card controls", id: `ctl-${name}` });
  const center = h("div", { class: "center" });
  const warnings = h("div", { class: "warnings" });
  const table = h("table", { class: "results" });
  const right = h("aside", { class: "card results-col" }, warnings, table);
  section.append(controls, center, right);
  document.getElementById("app").append(section);
  return { section, controls, center, warnings, table };
}

function subTabs(parent, names) {
  const bar = h("div", { class: "subtabs" });
  const panels = names.map(() => h("div", { class: "subpanel" }));
  const btns = names.map((n, i) => h("button", { onclick: () => show(i) }, n));
  bar.append(...btns);
  parent.append(h("div", { class: "card" }, bar, ...panels));
  function show(i) {
    btns.forEach((b, k) => b.classList.toggle("active", k === i));
    panels.forEach((p, k) => p.classList.toggle("active", k === i));
    panels[i].querySelectorAll(".js-plotly-plot").forEach((p) => Plotly.Plots.resize(p));
  }
  show(0);
  return { panels, show };
}

// ------------------------------------------------------------------ 2-D airfoil tab
function airfoilTab() {
  const L = tabLayout("airfoil");
  const spec = [
    { type: "group", label: "Airfoil" },
    { type: "airfoil", key: "name", label: "Airfoil", value: "NACA 2412" },
    { type: "number", key: "n_panels", label: "Panels", min: 60, max: 300, step: 20, value: 160 },
    { type: "group", label: "Angle of attack" },
    { type: "range", key: "alpha", label: "α", min: -20, max: 25, step: 0.5, value: 4, unit: "°" },
    ...flowSpec(50),
    { type: "group", label: "Model size" },
    { type: "number", key: "chord", label: "Chord c", min: 0.01, max: 50, step: 0.1, value: 1, unit: "m" },
    { type: "number", key: "span", label: "Span (for total forces)", min: 0.01, max: 100, step: 0.5, value: 1, unit: "m" },
    ...modelSpec("Stall model (trailing-edge separation)", true),
    { type: "group", label: "Surface point" },
    { type: "range", key: "surf_x", label: "x/c", min: 0, max: 1, step: 0.01, value: 0.25 },
    { type: "select", key: "side", label: "Side", options: ["Upper surface", "Lower surface"], value: "Upper surface" },
  ];
  let geometryChanged = true;
  const ctl = buildControls(L.controls, spec, (e) => {
    if (e && e.target && (e.target.id.endsWith("-name") || e.target.id.endsWith("-n_panels"))) geometryChanged = true;
    runner.schedule();
  });
  const fp = flowPanel(L.center, { onOptions: () => runner.schedule() });
  const polarTab = fp.addTab("Polar");
  const polarBtn = h("button", { class: "btn primary" }, "Compute polar (sweep angle of attack)");
  const polarCharts = [0, 1, 2, 3].map(() => h("div", { class: "chart small" }));
  polarTab.append(polarBtn, h("div", { class: "grid2" }, polarCharts));

  const params = () => {
    const v = ctl.values();
    return { ...v, surf_upper: v.side === "Upper surface", ...fp.options() };
  };
  const runner = new Runner("airfoil_run", params, (r) => {
    fp.setField(r.field, !geometryChanged);
    geometryChanged = false;
    fp.setCp(r.cp, r.surface_point);
    renderRows(L.table, r.rows);
    renderWarnings(L.warnings, r.warnings);
    ctl.info("freestream", r.freestream);
  }, (err) => renderWarnings(L.warnings, [], err.message.split("\n").slice(-2).join(" ")));

  polarBtn.addEventListener("click", async () => {
    polarBtn.disabled = true;
    polarBtn.textContent = "Computing…";
    setBusy(1);
    try {
      const p = await call("airfoil_polar", params());
      const ld = Array.from(p.cl, (c, i) => (p.cd[i] > 0 ? c / p.cd[i] : NaN));
      const imax = p.cl.indexOf(Math.max(...p.cl));
      plot(polarCharts[0], [{ x: p.alpha, y: p.cl, mode: "lines+markers", name: "viscous", marker: { size: 4 } },
        { x: p.alpha, y: p.cl_inviscid, mode: "lines", name: "inviscid", line: { dash: "dash", color: "#888" } }],
        { title: { text: `CL max ≈ ${p.cl[imax].toFixed(2)} at ${p.alpha[imax]}°`, font: { size: 12 } }, xaxis: { title: { text: "α [°]" } }, yaxis: { title: { text: "CL" } } });
      plot(polarCharts[1], [{ x: p.cd, y: p.cl, mode: "lines+markers", marker: { size: 4 } }], { xaxis: { title: { text: "CD" } }, yaxis: { title: { text: "CL" } }, showlegend: false });
      plot(polarCharts[2], [{ x: p.alpha, y: p.cm, mode: "lines+markers", marker: { size: 4, color: "#7a3fd1" }, line: { color: "#7a3fd1" } }], { xaxis: { title: { text: "α [°]" } }, yaxis: { title: { text: "Cm (c/4)" } }, showlegend: false });
      plot(polarCharts[3], [{ x: p.alpha, y: ld, mode: "lines+markers", marker: { size: 4, color: "#2a9d4a" }, line: { color: "#2a9d4a" } }], { xaxis: { title: { text: "α [°]" } }, yaxis: { title: { text: "L/D" } }, showlegend: false });
    } catch (err) {
      renderWarnings(L.warnings, [], err.message);
    } finally {
      setBusy(-1);
      polarBtn.disabled = false;
      polarBtn.textContent = "Compute polar (sweep angle of attack)";
    }
  });
  return { section: L.section, start: () => runner.run() };
}

// ------------------------------------------------------------------ 3-D wing tab
function wingTab() {
  const L = tabLayout("wing");
  const spec = [
    { type: "group", label: "Wing section" },
    { type: "airfoil", key: "name", label: "Airfoil", value: "NACA 2412" },
    { type: "group", label: "Planform" },
    { type: "number", key: "span_b", label: "Span b", min: 0.2, max: 80, step: 0.5, value: 10, unit: "m" },
    { type: "number", key: "root", label: "Root chord", min: 0.05, max: 20, step: 0.1, value: 1.6, unit: "m" },
    { type: "range", key: "taper", label: "Taper ratio (tip / root)", min: 0.05, max: 1, step: 0.05, value: 0.5 },
    { type: "range", key: "sweep", label: "Sweep (quarter chord)", min: -45, max: 60, step: 1, value: 0, unit: "°" },
    { type: "range", key: "dihedral", label: "Dihedral", min: -15, max: 20, step: 1, value: 3, unit: "°" },
    { type: "range", key: "twist", label: "Tip twist (− = washout)", min: -10, max: 10, step: 0.5, value: -2, unit: "°" },
    { type: "number", key: "n_span", label: "Spanwise panels", min: 10, max: 60, step: 2, value: 30 },
    { type: "number", key: "n_chord", label: "Chordwise panels", min: 1, max: 8, step: 1, value: 4 },
    { type: "group", label: "Angle of attack" },
    { type: "range", key: "alpha", label: "α", min: -10, max: 25, step: 0.5, value: 5, unit: "°" },
    ...flowSpec(60),
    ...modelSpec("Viscous strip correction (wing stall)", false),
    { type: "group", label: "Display" },
    { type: "range", key: "station", label: "Section station 2y/b", min: 0, max: 0.98, step: 0.02, value: 0.5 },
    { type: "check", key: "vortices", label: "Show tip-vortex streamlines", value: true },
  ];
  const ctl = buildControls(L.controls, spec, () => runner.schedule(250));
  const st = subTabs(L.center, ["3D view", "Span loading", "Section at station"]);
  const view3d = h("div", { class: "chart tall" });
  st.panels[0].append(view3d);
  const spanCharts = [0, 1, 2, 3].map(() => h("div", { class: "chart small" }));
  st.panels[1].append(h("div", { class: "grid2" }, spanCharts));
  const fp = flowPanel(st.panels[2], { onOptions: () => runner.schedule() });

  const params = () => ({ ...ctl.values(), ...fp.options() });
  const runner = new Runner("wing_run", params, (r) => {
    draw3d(r);
    drawSpan(r);
    fp.setField(r.section.field, true);
    fp.setCp(r.section.cp);
    renderRows(L.table, r.rows);
    renderWarnings(L.warnings, r.warnings);
    ctl.info("freestream", r.freestream);
  }, (err) => renderWarnings(L.warnings, [], err.message.split("\n").slice(-2).join(" ")));

  function draw3d(r) {
    const P = r.panels, n = P.dcp.length;
    const i = [], j = [], k = [];
    for (let q = 0; q < n; q++) { i.push(4 * q, 4 * q); j.push(4 * q + 1, 4 * q + 2); k.push(4 * q + 2, 4 * q + 3); }
    const intensity = [];
    for (let q = 0; q < n; q++) intensity.push(P.dcp[q], P.dcp[q]);
    const traces = [{ type: "mesh3d", x: P.x, y: P.y, z: P.z, i, j, k, intensity, intensitymode: "cell",
      colorscale: "Viridis", colorbar: { title: { text: "ΔCp" }, len: 0.6 }, flatshading: true, name: "wing", hoverinfo: "skip" }];
    r.vortices.forEach((v, idx) => traces.push({ type: "scatter3d", mode: "lines", x: v[0], y: v[1], z: v[2],
      line: { color: "#d03030", width: 3 }, showlegend: idx === 0, name: "tip-vortex streamlines", hoverinfo: "skip" }));
    const b = r.span_b;
    const s = r.summary;
    plot(view3d, traces, {
      title: { text: `CL = ${s.CL.toFixed(3)}, CDi = ${s.CDi.toFixed(4)}, e = ${s.e.toFixed(3)}`, font: { size: 12 } },
      uirevision: "wing", margin: { l: 0, r: 0, t: 30, b: 0 },
      scene: { aspectmode: "manual", aspectratio: { x: 1.2, y: 1.2, z: 0.45 },
        xaxis: { title: { text: "x [m] downstream" }, range: [-0.15 * b, 1.05 * b] },
        yaxis: { title: { text: "y [m]" }, range: [-0.6 * b, 0.6 * b] },
        zaxis: { title: { text: "z [m]" }, range: [-0.22 * b, 0.22 * b] },
        camera: { eye: { x: -1.3, y: -1.5, z: 0.9 } } },
    });
  }

  function drawSpan(r) {
    const S = r.span, eta = S.eta, sta = r.station_eta;
    const vline = (y0, y1) => [{ type: "line", x0: sta, x1: sta, y0, y1, yref: "paper", line: { color: "#ff2d2d", width: 1 } }];
    const stalledEta = [], stalledCl = [];
    S.stalled.forEach((s, q) => { if (s) { stalledEta.push(eta[q]); stalledCl.push(S.cl[q]); } });
    const common = { xaxis: { title: { text: "2y/b" } }, shapes: vline(0, 1), margin: { l: 52, r: 10, t: 26, b: 88 } };
    plot(spanCharts[0], [{ x: eta, y: S.cl, mode: "lines+markers", marker: { size: 3 }, name: "local cl" },
      { x: stalledEta, y: stalledCl, mode: "markers", marker: { symbol: "x", size: 8, color: "#d03030" }, name: "stalled" },
      { x: [-1, 1], y: [S.CL, S.CL], mode: "lines", line: { dash: "dot", color: "#888" }, name: "wing CL" }],
    { ...common, title: { text: "Section lift coefficient", font: { size: 12 } } });
    plot(spanCharts[1], [{ x: eta, y: S.loading, mode: "lines", name: "this wing" },
      { x: eta, y: S.elliptic, mode: "lines", line: { dash: "dash", color: "#888" }, name: "elliptic (same CL)" }],
    { ...common, title: { text: "Span loading c·cl / c̄", font: { size: 12 } } });
    plot(spanCharts[2], [{ x: eta, y: S.alpha_i, mode: "lines", name: "induced angle αi", line: { color: "#d1491f" } },
      { x: eta, y: S.alpha_eff, mode: "lines", name: "effective angle α_eff", line: { color: "#2a9d4a" } },
      { x: [-1, 1], y: [S.alpha, S.alpha], mode: "lines", line: { dash: "dot", color: "#888" }, name: "geometric α" }],
    { ...common, title: { text: "Angles [°]", font: { size: 12 } } });
    plot(spanCharts[3], [{ x: eta, y: S.chord, mode: "lines", name: "chord [m]", line: { color: "#7a3fd1" } },
      { x: eta, y: S.cd, mode: "lines", name: "profile cd", yaxis: "y2", line: { color: "#c08000" } }],
    { ...common, title: { text: "Chord and section drag", font: { size: 12 } },
      yaxis2: { overlaying: "y", side: "right", gridcolor: "rgba(0,0,0,0)", color: cssVar("--text") } });
  }
  return { section: L.section, start: () => runner.run() };
}

// ------------------------------------------------------------------ propeller tab
function propTab() {
  const L = tabLayout("prop");
  const spec = [
    { type: "group", label: "Blade section" },
    { type: "airfoil", key: "name", label: "Airfoil", value: "NACA 4412" },
    { type: "group", label: "Propeller geometry" },
    { type: "number", key: "diameter", label: "Diameter", min: 0.05, max: 8, step: 0.05, value: 1.8, unit: "m" },
    { type: "number", key: "hub", label: "Hub radius / R", min: 0.05, max: 0.5, step: 0.01, value: 0.15 },
    { type: "number", key: "blades", label: "Number of blades", min: 1, max: 8, step: 1, value: 2 },
    { type: "range", key: "pitch", label: "Geometric pitch", min: 0.1, max: 6, step: 0.05, value: 1.5, unit: "m" },
    { type: "number", key: "c_root", label: "Root chord / R", min: 0.02, max: 0.4, step: 0.01, value: 0.12 },
    { type: "number", key: "c_tip", label: "Tip chord / R", min: 0.01, max: 0.4, step: 0.01, value: 0.06 },
    { type: "number", key: "n_el", label: "Blade elements", min: 10, max: 60, step: 2, value: 24 },
    { type: "group", label: "Operating point" },
    { type: "range", key: "rpm", label: "Rotational speed", min: 100, max: 30000, step: 50, value: 2400, unit: "rpm" },
    ...flowSpec(40, true),
    ...modelSpec("Stall model in section polar", false),
    { type: "group", label: "Display" },
    { type: "range", key: "station", label: "Blade section r/R", min: 0.2, max: 0.98, step: 0.01, value: 0.75 },
  ];
  const ctl = buildControls(L.controls, spec, () => runner.schedule(250));
  const st = subTabs(L.center, ["Slipstream", "Blade loading", "Blade section", "Performance map"]);
  const slip = flowPanel(st.panels[0], { xLabel: "x / R", yLabel: "r / R", cp: false, onOptions: () => runner.schedule() });
  const loadCharts = [0, 1, 2, 3].map(() => h("div", { class: "chart small" }));
  st.panels[1].append(h("div", { class: "grid2" }, loadCharts));
  const blade = flowPanel(st.panels[2], { onOptions: () => runner.schedule() });
  const mapBtn = h("button", { class: "btn primary" }, "Compute performance map (sweep advance ratio J)");
  const mapCharts = [0, 1, 2].map(() => h("div", { class: "chart small" }));
  st.panels[3].append(mapBtn, h("div", { class: "grid2" }, mapCharts));

  const params = () => {
    const s = blade.options();
    return { ...ctl.values(), ...slip.options(), sec_background: s.background, sec_n_lines: s.n_lines };
  };
  const runner = new Runner("prop_run", params, (r) => {
    slip.setField(r.slip, true);
    blade.setField(r.section.field, true);
    blade.setCp(r.section.cp);
    drawLoading(r);
    renderRows(L.table, r.rows);
    renderWarnings(L.warnings, r.warnings);
    ctl.info("freestream", r.freestream);
  }, (err) => renderWarnings(L.warnings, [], err.message.split("\n").slice(-2).join(" ")));

  function drawLoading(r) {
    const d = r.dist;
    const common = { xaxis: { title: { text: "r / R" } }, margin: { l: 56, r: 10, t: 26, b: 88 },
      shapes: [{ type: "line", x0: r.station_x, x1: r.station_x, y0: 0, y1: 1, yref: "paper", line: { color: "#ff2d2d", width: 1 } }] };
    plot(loadCharts[0], [{ x: d.x, y: d.dT, mode: "lines", name: "dT/dr" }], { ...common, title: { text: "Thrust per unit radius [N/m]", font: { size: 12 } }, showlegend: false });
    plot(loadCharts[1], [{ x: d.x, y: d.dQ, mode: "lines", line: { color: "#d1491f" }, name: "dQ/dr" }], { ...common, title: { text: "Torque per unit radius [N·m/m]", font: { size: 12 } }, showlegend: false });
    plot(loadCharts[2], [{ x: d.x, y: d.alpha, mode: "lines", name: "angle of attack α" }, { x: d.x, y: d.phi, mode: "lines", name: "inflow angle φ" },
      { x: d.x, y: d.beta, mode: "lines", line: { dash: "dash" }, name: "blade angle β" }], { ...common, title: { text: "Angles [°]", font: { size: 12 } } });
    plot(loadCharts[3], [{ x: d.x, y: d.mach, mode: "lines", line: { color: "#7a3fd1" }, name: "section Mach" },
      { x: [d.x[0], d.x[d.x.length - 1]], y: [0.85, 0.85], mode: "lines", line: { dash: "dot", color: "#d03030" }, name: "0.85 (losses grow)" }],
    { ...common, title: { text: "Relative Mach number W/a", font: { size: 12 } } });
  }

  mapBtn.addEventListener("click", async () => {
    mapBtn.disabled = true;
    mapBtn.textContent = "Computing…";
    setBusy(1);
    try {
      const m = await call("prop_map", params());
      const common = { xaxis: { title: { text: "advance ratio J" } }, showlegend: false, margin: { l: 52, r: 10, t: 26, b: 44 } };
      plot(mapCharts[0], [{ x: m.J, y: m.CT, mode: "lines+markers", marker: { size: 4 } }], { ...common, title: { text: "Thrust coefficient CT", font: { size: 12 } } });
      plot(mapCharts[1], [{ x: m.J, y: m.CP, mode: "lines+markers", marker: { size: 4, color: "#d1491f" }, line: { color: "#d1491f" } }], { ...common, title: { text: "Power coefficient CP", font: { size: 12 } } });
      plot(mapCharts[2], [{ x: m.J, y: m.eta, mode: "lines+markers", marker: { size: 4, color: "#2a9d4a" }, line: { color: "#2a9d4a" } }], { ...common, title: { text: "Efficiency η", font: { size: 12 } }, yaxis: { range: [0, 1] } });
    } catch (err) {
      renderWarnings(L.warnings, [], err.message);
    } finally {
      setBusy(-1);
      mapBtn.disabled = false;
      mapBtn.textContent = "Compute performance map (sweep advance ratio J)";
    }
  });
  return { section: L.section, start: () => runner.run() };
}

// ------------------------------------------------------------------ start-up
async function main() {
  await ready;
  const cat = await call("catalog", {});
  document.body.append(h("datalist", { id: "airfoil-list" }, cat.names.map((n) => h("option", { value: n }))));
  const tabs = { airfoil: airfoilTab(), wing: wingTab(), prop: propTab() };
  const started = new Set();
  function showTab(name) {
    if (!tabs[name]) name = "airfoil";
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    Object.entries(tabs).forEach(([k, t]) => t.section.classList.toggle("active", k === name));
    if (!started.has(name)) { started.add(name); tabs[name].start(); }
    tabs[name].section.querySelectorAll(".js-plotly-plot").forEach((p) => Plotly.Plots.resize(p));
    history.replaceState(null, "", `#${name}`);
  }
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  showTab(location.hash.slice(1));
  document.getElementById("loading").remove();
}
main();
