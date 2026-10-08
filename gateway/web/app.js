// Local history dashboard - talks only to history_web.py on the same Pi.
import { lineChart, sparkline } from "./chart.js";

const $ = s => document.querySelector(s);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const HOUR = 3600e3, DAY = 24 * HOUR;
const PRESETS = {
  hist: [["1 h", HOUR], ["6 h", 6 * HOUR], ["24 h", DAY], ["7 d", 7 * DAY], ["30 d", 30 * DAY]],
  alarm: [["24 h", DAY], ["7 d", 7 * DAY], ["30 d", 30 * DAY]],
  sys: [["24 h", DAY], ["7 d", 7 * DAY], ["30 d", 30 * DAY]],
};
const EXPORT_MAX = 7 * DAY;

const state = { info: null, sensors: [], view: "actual", hist: { span: DAY }, alarm: { span: DAY }, sys: { span: DAY } };
let timer = null;

// ---------------------------------------------------------------- helpers
async function api(path) {
  const r = await fetch(path, { cache: "no-store" });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}
function showError(e) {
  const p = $("#error");
  p.hidden = !e;
  p.textContent = e ? `No se pudieron leer los datos: ${e.message || e}` : "";
}
const tz = () => (state.info && state.info.city) || undefined;
const fmtTime = (t, opts = { dateStyle: "short", timeStyle: "medium" }) =>
  new Intl.DateTimeFormat("es-MX", { timeZone: tz(), ...opts }).format(new Date(t));
function fmtNum(v, unit) {
  if (v == null) return "—";
  const a = Math.abs(v);
  const d = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return v.toLocaleString("es-MX", { maximumFractionDigits: d, minimumFractionDigits: 0 }) + (unit ? ` ${unit}` : "");
}
function fmtDuration(s) {
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
}
function fmtAgo(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 90) return `${s} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} días`;
}
// Status never by color alone: icon + label, colored icon only.
function statusOf(latest) {
  if (!latest) return ["none", "–", "Sin datos"];
  if (latest.status !== "OK") return ["serious", "✕", "Error de lectura"];
  if (latest.alarm === "HIGH") return ["critical", "▲", "Alto"];
  if (latest.alarm === "LOW") return ["critical", "▼", "Bajo"];
  return ["good", "●", "Normal"];
}
function statusChip(latest) {
  const [cls, icon, label] = statusOf(latest);
  const s = el("span", `status ${cls}`);
  s.append(el("i", null, icon), document.createTextNode(label));
  return s;
}
const sensorLabel = s => `${s.name} · ${s.device}`;

// Wall time in the gateway's time zone <-> epoch ms, for datetime-local inputs.
function tzOffset(ms) {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: tz(), hourCycle: "h23",
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
    .formatToParts(new Date(ms)).map(x => [x.type, x.value]));
  return Date.UTC(+p.year, p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - Math.floor(ms / 1000) * 1000;
}
function toInput(ms) {
  const d = new Date(ms + tzOffset(ms));
  return d.toISOString().slice(0, 16);
}
function fromInput(v) {
  if (!v) return null;
  const guess = Date.parse(v + ":00Z");
  return guess - tzOffset(guess - tzOffset(guess));
}

// ---------------------------------------------------------------- header
async function refreshInfo() {
  const info = state.info = await api("/api/info");
  $("#gw").textContent = info.gateway_id || "gateway";
  document.title = `Historial local · ${info.gateway_id}`;
  $("#sim").hidden = !info.simulated;
  const fresh = $("#fresh");
  const last = info.db.last ? Date.parse(info.db.last) : null;
  const age = last ? Date.parse(info.now) - last : null;
  const ok = age != null && age < 3 * info.poll_interval * 1000;
  fresh.className = `badge ${ok ? "ok" : "stale"}`;
  fresh.textContent = last ? `Último dato hace ${fmtAgo(age)}` : "Sin datos todavía";
}
const DB_OFF = new Error("la base de datos local está apagada (database_enabled=0 en system.csv); solo se muestra lo ya guardado");

// ---------------------------------------------------------------- Actual
async function viewActual() {
  const { sensors } = await api("/api/overview");
  const box = $("#cards");
  box.replaceChildren();
  for (const s of sensors) {
    const [cls] = statusOf(s.latest);
    const card = el("div", `card${cls === "critical" ? " alarm" : cls === "serious" ? " err" : ""}`);
    const head = el("div", "row");
    head.append(el("h3", null, s.name), statusChip(s.latest));
    const value = el("div", "value", s.latest && s.latest.value != null ? fmtNum(s.latest.value) : "—");
    if (s.unit) value.appendChild(el("small", null, s.unit));
    const sub = el("div", "sub", `${s.device} · límites ${fmtNum(s.min)} – ${fmtNum(s.max)}${s.latest ? " · " + fmtTime(Date.parse(s.latest.ts), { timeStyle: "medium" }) : ""}`);
    const spark = el("div", "spark");
    card.append(head, value, sub, spark);
    box.appendChild(card);
    sparkline(spark, s.spark, css("--series-1"));
  }
}

// ---------------------------------------------------------------- Históricos
function buildPresets() {
  for (const box of document.querySelectorAll(".presets")) {
    const target = box.dataset.target;
    for (const [label, span] of PRESETS[target]) {
      const b = el("button", span === state[target].span ? "on" : "", label);
      b.type = "button";
      b.addEventListener("click", () => {
        state[target].span = span;
        state[target].start = state[target].end = null;
        box.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
        if (target === "hist") { setHistInputs(); viewHistoricos(); }
        else render();
      });
      box.appendChild(b);
    }
  }
}
function histRange() {
  const h = state.hist;
  const end = h.end || Date.now();
  return [h.start || end - h.span, end];
}
function setHistInputs() {
  const [a, b] = histRange();
  $("#h-start").value = toInput(a);
  $("#h-end").value = toInput(b);
}
function selectedKeys() {
  return [...document.querySelectorAll("#h-sensors input:checked")].map(i => i.value);
}
function updateCount() {
  const keys = selectedKeys();
  $("#h-count").textContent = keys.length;
  const [a, b] = histRange();
  const csv = $("#h-csv");
  csv.disabled = !keys.length || b - a > EXPORT_MAX;
  csv.title = b - a > EXPORT_MAX ? "El CSV admite como máximo 7 días" : "";
}
function buildSensorPicker() {
  const list = $("#h-sensors");
  list.replaceChildren();
  state.sensors.forEach((s, i) => {
    const lab = el("label");
    const cb = el("input");
    cb.type = "checkbox";
    cb.value = s.key;
    cb.checked = i < 4;
    cb.addEventListener("change", updateCount);
    lab.append(cb, document.createTextNode(`${sensorLabel(s)}${s.unit ? ` (${s.unit})` : ""}`));
    list.appendChild(lab);
  });
  updateCount();
}
async function viewHistoricos() {
  const keys = selectedKeys();
  updateCount();
  const out = $("#h-charts");
  if (!keys.length) {
    out.replaceChildren(el("p", "hint", "Elige al menos un sensor."));
    return;
  }
  const [a, b] = histRange();
  const data = await api(`/api/history?sensors=${encodeURIComponent(keys.join(","))}&start=${Math.round(a)}&end=${Math.round(b)}`);
  const step = data.step_s;
  $("#h-note").textContent = `${fmtTime(a, { dateStyle: "medium", timeStyle: "short" })} – ${fmtTime(b, { dateStyle: "medium", timeStyle: "short" })} · ` +
    `cada punto es el promedio de ${step < 120 ? step + " s" : Math.round(step / 60) + " min"}; la franja muestra el mínimo y máximo de ese intervalo.`;
  out.replaceChildren();
  for (const s of data.series) {
    const pts = s.points;
    const vals = pts.filter(p => p[1] != null);
    const card = el("div", "card");
    card.appendChild(el("h3", null, `${sensorLabel(s)}${s.unit ? ` (${s.unit})` : ""}`));
    const stats = el("div", "stats");
    if (vals.length) {
      const lo = Math.min(...vals.map(p => p[2])), hi = Math.max(...vals.map(p => p[3]));
      const avg = vals.reduce((t, p) => t + p[1], 0) / vals.length;
      const nAlarm = pts.reduce((t, p) => t + p[4], 0);
      for (const [k, v] of [["mín", fmtNum(lo)], ["prom", fmtNum(avg)], ["máx", fmtNum(hi)], ["muestras en alarma", nAlarm.toLocaleString("es-MX")]]) {
        const x = el("span", null, `${k} `);
        x.appendChild(el("b", null, v));
        stats.appendChild(x);
      }
    }
    const chart = el("div");
    card.append(stats, chart);
    // table view (accessibility + exact numbers)
    const det = el("details");
    det.appendChild(el("summary", null, "Ver tabla"));
    det.addEventListener("toggle", () => {
      if (!det.open || det.querySelector("table")) return;
      const wrap = el("div", "table-wrap");
      const t = el("table");
      const head = el("tr");
      for (const h of ["Hora", "Prom", "Mín", "Máx", "En alarma"]) head.appendChild(el("th", h === "Hora" ? "" : "num", h));
      t.appendChild(el("thead")).appendChild(head);
      const body = t.appendChild(el("tbody"));
      for (const p of pts.slice().reverse()) {
        const tr = el("tr");
        tr.appendChild(el("td", null, fmtTime(p[0], { dateStyle: "short", timeStyle: "short" })));
        for (const v of [p[1], p[2], p[3]]) tr.appendChild(el("td", "num", fmtNum(v)));
        tr.appendChild(el("td", "num", String(p[4])));
        body.appendChild(tr);
      }
      wrap.appendChild(t);
      det.appendChild(wrap);
    });
    card.appendChild(det);
    out.appendChild(card);
    const alarmAt = new Map(pts.map(p => [p[0], p[4]]));
    lineChart(chart, {
      series: [{ name: "promedio", color: css("--series-1"), points: pts.map(p => [p[0], p[1]]) }],
      band: pts.map(p => [p[0], p[2], p[3]]),
      limits: [{ value: s.max, label: `máx ${fmtNum(s.max)}` }, { value: s.min, label: `mín ${fmtNum(s.min)}` }],
      unit: s.unit, xDomain: [a, b], gapMs: 2.5 * step * 1000, tz: tz(), height: 200,
      fmt: v => fmtNum(v), ariaLabel: `Tendencia de ${s.name}`,
      extra: t => alarmAt.get(t) ? `${alarmAt.get(t)} muestra(s) en alarma` : "",
    });
  }
}

// ---------------------------------------------------------------- Alarmas
async function viewAlarmas() {
  const end = Date.now(), start = end - state.alarm.span;
  const { episodes } = await api(`/api/alarms?start=${start}&end=${end}`);
  const sel = $("#a-sensor");
  if (sel.options.length === 1) {
    for (const s of state.sensors) sel.appendChild(new Option(sensorLabel(s), s.key));
  }
  const eps = sel.value ? episodes.filter(e => e.key === sel.value) : episodes;
  const byKey = Object.fromEntries(state.sensors.map(s => [s.key, s]));
  const counts = {};
  for (const e of eps) counts[e.key] = (counts[e.key] || 0) + 1;
  const top = Object.entries(counts).sort((x, y) => y[1] - x[1]).slice(0, 3)
    .map(([k, n]) => `${byKey[k] ? byKey[k].name : k} (${n})`).join(", ");
  $("#a-summary").textContent = eps.length
    ? `${episodes.length >= 500 ? "Más de 500 eventos de alarma; se muestran los 500 más recientes" : `${eps.length} eventos de alarma`}. Más frecuentes: ${top}. ` +
      "Un evento agrupa muestras seguidas fuera de límites del mismo sensor."
    : "Sin alarmas en este rango.";
  const body = $("#a-table tbody");
  body.replaceChildren();
  for (const e of eps) {
    const s = byKey[e.key] || { name: e.key, device: "", unit: "" };
    const tr = el("tr");
    tr.appendChild(el("td", null, sensorLabel(s)));
    const type = el("td");
    type.appendChild(statusChip({ status: "OK", alarm: e.alarm }));
    tr.appendChild(type);
    tr.appendChild(el("td", null, fmtTime(Date.parse(e.start))));
    tr.appendChild(el("td", null, fmtTime(Date.parse(e.end))));
    tr.appendChild(el("td", null, e.samples > 1 ? fmtDuration(e.duration_s) : "1 muestra"));
    tr.appendChild(el("td", "num", String(e.samples)));
    tr.appendChild(el("td", "num", fmtNum(e.peak, s.unit)));
    tr.style.cursor = "pointer";
    tr.title = "Ver la tendencia alrededor de este evento";
    tr.addEventListener("click", () => openTrend(e.key, Date.parse(e.start), Date.parse(e.end)));
    body.appendChild(tr);
  }
}
function openTrend(key, t0, t1) {
  const pad = Math.max(30 * 60e3, (t1 - t0));
  state.hist.start = t0 - pad;
  state.hist.end = Math.min(Date.now(), t1 + pad);
  document.querySelectorAll('.presets[data-target="hist"] button').forEach(b => b.classList.remove("on"));
  document.querySelectorAll("#h-sensors input").forEach(i => { i.checked = i.value === key; });
  setHistInputs();
  location.hash = "#historicos";
}

// ---------------------------------------------------------------- Sistema
function fillDl(dl, rows) {
  dl.replaceChildren();
  for (const [k, v] of rows) { dl.appendChild(el("dt", null, k)); dl.appendChild(el("dd", null, v ?? "—")); }
}
async function viewSistema() {
  const end = Date.now(), start = end - state.sys.span;
  const [sys] = await Promise.all([api(`/api/system?start=${start}&end=${end}`), refreshInfo()]);
  const info = state.info, l = sys.latest || {};
  fillDl($("#s-gw"), [
    ["Gateway", info.gateway_id], ["IP", l.ip_address], ["Plataforma", l.platform_type], ["Sistema", l.os],
    ["Zona horaria", info.city], ["Lectura cada", `${info.poll_interval} s`],
    ["Modo Modbus", info.simulated ? "⚠ simulado (datos sintéticos)" : "sensores reales"],
    ["Último reporte", l.ts ? fmtTime(Date.parse(l.ts)) : null],
  ]);
  const db = info.db;
  fillDl($("#s-db"), [
    ["Tamaño", `${(db.size_bytes / 1048576).toLocaleString("es-MX", { maximumFractionDigits: 1 })} MB`],
    ["Lecturas", db.readings.toLocaleString("es-MX")], ["Telemetría", db.system_rows.toLocaleString("es-MX")],
    ["Desde", db.first ? fmtTime(Date.parse(db.first)) : null], ["Hasta", db.last ? fmtTime(Date.parse(db.last)) : null],
    ["Se conservan", `${info.retention_days} días`],
  ]);
  const p = sys.points;
  lineChart($("#s-chart"), {
    series: [
      { name: "CPU", color: css("--series-1"), points: p.map(x => [x[0], x[1]]) },
      { name: "RAM", color: css("--series-2"), points: p.map(x => [x[0], x[2]]) },
      { name: "Disco", color: css("--series-3"), points: p.map(x => [x[0], x[3]]) },
    ],
    unit: "%", xDomain: [start, end], yMin: 0, yMax: 100, gapMs: 2.5 * sys.step_s * 1000,
    tz: tz(), height: 220, legend: true, fmt: v => fmtNum(v), ariaLabel: "CPU, RAM y disco",
  });
}

// ---------------------------------------------------------------- theme
// Follows the OS until the button is used; the choice is then kept in this
// browser (localStorage) and set as <html data-theme> (also read in index.html).
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
const isDark = () => (document.documentElement.dataset.theme || (darkQuery.matches ? "dark" : "light")) === "dark";
function paintThemeButton() {
  const b = $("#theme"), dark = isDark();
  b.querySelector("span").textContent = dark ? "☀" : "☾";
  b.querySelector("b").textContent = dark ? "Modo claro" : "Modo oscuro";
  b.setAttribute("aria-pressed", String(dark));
  b.title = dark ? "Cambiar a fondo blanco" : "Cambiar a fondo oscuro";
}
function toggleTheme() {
  const next = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("akvo-theme", next); } catch (e) { /* private mode: just not remembered */ }
  paintThemeButton();
  render(); // charts take their colors from the theme when drawn
}

// ---------------------------------------------------------------- routing
const VIEWS = { actual: viewActual, historicos: viewHistoricos, alarmas: viewAlarmas, sistema: viewSistema };
async function render() {
  clearTimeout(timer);
  try {
    await VIEWS[state.view]();
    showError(state.info && !state.info.database_enabled ? DB_OFF : null);
  } catch (e) {
    showError(e);
  }
  // Actual refreshes itself; the other views refresh on demand.
  if (state.view === "actual") timer = setTimeout(render, Math.max(10, state.info ? state.info.poll_interval : 20) * 1000);
}
function route() {
  const v = location.hash.slice(1);
  state.view = VIEWS[v] ? v : "actual";
  for (const k of Object.keys(VIEWS)) $(`#view-${k}`).hidden = k !== state.view;
  document.querySelectorAll(".tabs a").forEach(a => a.classList.toggle("active", a.hash === `#${state.view}`));
  render();
}

async function init() {
  paintThemeButton();
  $("#theme").addEventListener("click", toggleTheme);
  darkQuery.addEventListener("change", () => {
    if (document.documentElement.dataset.theme) return; // a manual choice wins
    paintThemeButton();
    render();
  });
  buildPresets();
  try {
    await refreshInfo();
    state.sensors = (await api("/api/sensors")).sensors;
  } catch (e) {
    showError(e);
  }
  buildSensorPicker();
  setHistInputs();
  $("#h-all").addEventListener("click", e => { e.preventDefault(); document.querySelectorAll("#h-sensors input").forEach(i => { i.checked = true; }); updateCount(); });
  $("#h-none").addEventListener("click", e => { e.preventDefault(); document.querySelectorAll("#h-sensors input").forEach(i => { i.checked = false; }); updateCount(); });
  $("#h-run").addEventListener("click", () => {
    const a = fromInput($("#h-start").value), b = fromInput($("#h-end").value);
    if (a && b) {
      state.hist.start = a;
      state.hist.end = b;
      document.querySelectorAll('.presets[data-target="hist"] button').forEach(x => x.classList.remove("on"));
    }
    viewHistoricos().then(() => showError(null), showError);
  });
  $("#h-csv").addEventListener("click", () => {
    const [a, b] = histRange();
    location.href = `/api/export.csv?sensors=${encodeURIComponent(selectedKeys().join(","))}&start=${Math.round(a)}&end=${Math.round(b)}`;
  });
  $("#a-sensor").addEventListener("change", render);
  setInterval(() => refreshInfo().catch(() => {}), 30e3);
  window.addEventListener("hashchange", route);
  route();
}
init();
