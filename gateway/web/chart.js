// Minimal SVG time-series chart, no dependencies (the Pi page must work offline).
// lineChart(el, {series:[{name, color, points:[[ms, v], ...]}], band, limits, unit,
//                xDomain:[ms, ms], gapMs, height, tz, fmt, yMin, yMax, legend})
// - band: [[ms, lo, hi], ...] drawn as a wash behind series[0]
// - limits: [{value, label}] horizontal reference lines (alarm min/max)
// - a gap longer than gapMs (or a null value) breaks the line instead of bridging it
const NS = "http://www.w3.org/2000/svg";
const M = { top: 10, right: 14, bottom: 24, left: 52 };

function svg(tag, attrs = {}, parent) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}

function niceTicks(lo, hi, count = 5) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) ticks.push(+v.toFixed(10));
  return { ticks, lo: Math.min(lo, ticks[0]), hi: Math.max(hi, ticks[ticks.length - 1]) };
}

function timeTicks(x0, x1, count) {
  const steps = [60e3, 300e3, 900e3, 1800e3, 3600e3, 3 * 3600e3, 6 * 3600e3, 12 * 3600e3, 864e5, 2 * 864e5, 7 * 864e5];
  const step = steps.find(s => (x1 - x0) / s <= count) || steps[steps.length - 1];
  const out = [];
  for (let t = Math.ceil(x0 / step) * step; t <= x1; t += step) out.push(t);
  return out;
}

function nearest(points, t) {
  let lo = 0, hi = points.length - 1;
  if (hi < 0) return -1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid][0] < t) lo = mid + 1; else hi = mid;
  }
  if (lo > 0 && Math.abs(points[lo - 1][0] - t) < Math.abs(points[lo][0] - t)) lo--;
  return lo;
}

export function lineChart(el, opts) {
  const draw = () => render(el, opts);
  draw();
  if (!el._ro) {
    el._ro = new ResizeObserver(() => { if (el.clientWidth !== el._w) draw(); });
    el._ro.observe(el);
  }
}

function render(el, o) {
  el.replaceChildren();
  el.classList.add("chart");
  const W = el._w = el.clientWidth || 300, H = o.height || 180;
  const series = o.series.filter(s => s.points.length);
  const fmt = o.fmt || (v => v.toLocaleString("es-MX", { maximumFractionDigits: 2 }));
  if (!series.length && !(o.band || []).length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "Sin datos en este rango";
    el.appendChild(p);
    return;
  }

  // domains
  const all = series.flatMap(s => s.points.map(p => p[0]));
  const [x0, x1] = o.xDomain || [Math.min(...all), Math.max(...all)];
  const vals = series.flatMap(s => s.points.map(p => p[1])).filter(v => v != null);
  for (const b of o.band || []) { if (b[1] != null) vals.push(b[1], b[2]); }
  for (const l of o.limits || []) if (l.value != null) vals.push(l.value);
  if (o.yMin != null) vals.push(o.yMin);
  if (o.yMax != null) vals.push(o.yMax);
  const yt = niceTicks(Math.min(...vals), Math.max(...vals));
  const iw = W - M.left - M.right, ih = H - M.top - M.bottom;
  const X = t => M.left + (x1 === x0 ? iw / 2 : ((t - x0) / (x1 - x0)) * iw);
  const Y = v => M.top + ih - ((v - yt.lo) / (yt.hi - yt.lo)) * ih;

  const root = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": o.ariaLabel || series.map(s => s.name).join(", "), tabindex: 0 }, el);

  // grid + axes (hairline, solid, recessive)
  for (const v of yt.ticks) {
    svg("line", { x1: M.left, x2: W - M.right, y1: Y(v), y2: Y(v), class: "grid" }, root);
    svg("text", { x: M.left - 6, y: Y(v) + 4, class: "tick", "text-anchor": "end" }, root).textContent = fmt(v);
  }
  svg("line", { x1: M.left, x2: W - M.right, y1: M.top + ih, y2: M.top + ih, class: "axis" }, root);
  const span = x1 - x0;
  const tf = new Intl.DateTimeFormat("es-MX", span > 2 * 864e5
    ? { timeZone: o.tz, day: "numeric", month: "short" }
    : span > 864e5 ? { timeZone: o.tz, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }
      : { timeZone: o.tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  for (const t of timeTicks(x0, x1, Math.max(2, Math.floor(iw / 90)))) {
    svg("text", { x: X(t), y: H - 6, class: "tick", "text-anchor": "middle" }, root).textContent = tf.format(t);
  }

  // alarm limits
  for (const l of o.limits || []) {
    if (l.value == null) continue;
    svg("line", { x1: M.left, x2: W - M.right, y1: Y(l.value), y2: Y(l.value), class: "limit" }, root);
    svg("text", { x: W - M.right - 2, y: Y(l.value) - 4, class: "tick limit-label", "text-anchor": "end" }, root).textContent = l.label;
  }

  const gap = o.gapMs || Infinity;
  const segments = pts => {
    const segs = [];
    let cur = [];
    pts.forEach((p, i) => {
      if (p[1] == null || (i && p[0] - pts[i - 1][0] > gap)) { if (cur.length) segs.push(cur); cur = []; }
      if (p[1] != null) cur.push(p);
    });
    if (cur.length) segs.push(cur);
    return segs;
  };

  // min-max band (wash, ~10%)
  if (o.band && o.band.length && series[0]) {
    for (const seg of segments(o.band.map(b => [b[0], b[1], b[2]]))) {
      const d = seg.map((p, i) => `${i ? "L" : "M"}${X(p[0])},${Y(p[2])}`).join("") +
        seg.slice().reverse().map(p => `L${X(p[0])},${Y(p[1])}`).join("") + "Z";
      svg("path", { d, fill: series[0].color, "fill-opacity": 0.12, stroke: "none" }, root);
    }
  }
  for (const s of series) {
    for (const seg of segments(s.points)) {
      if (seg.length === 1) {
        svg("circle", { cx: X(seg[0][0]), cy: Y(seg[0][1]), r: 2.5, fill: s.color }, root);
        continue;
      }
      svg("path", { d: seg.map((p, i) => `${i ? "L" : "M"}${X(p[0])},${Y(p[1])}`).join(""),
        class: "line", stroke: s.color }, root);
    }
  }

  if (o.legend && series.length > 1) {
    const lg = document.createElement("div");
    lg.className = "legend";
    for (const s of series) {
      const item = document.createElement("span");
      const key = document.createElement("i");
      key.style.background = s.color;
      item.append(key, document.createTextNode(s.name));
      lg.appendChild(item);
    }
    el.prepend(lg);
  }

  // crosshair + tooltip: snaps to the nearest bucket, lists every series
  const cross = svg("line", { y1: M.top, y2: M.top + ih, class: "crosshair", visibility: "hidden" }, root);
  const dots = series.map(s => svg("circle", { r: 4, fill: s.color, class: "dot", visibility: "hidden" }, root));
  const tip = document.createElement("div");
  tip.className = "tooltip";
  tip.hidden = true;
  el.appendChild(tip);
  const full = new Intl.DateTimeFormat("es-MX", { timeZone: o.tz, dateStyle: "medium", timeStyle: "short" });
  const ref = series[0] ? series[0].points : [];
  let idx = -1;

  const show = i => {
    if (i < 0 || i >= ref.length) return hide();
    idx = i;
    const t = ref[i][0];
    cross.setAttribute("x1", X(t));
    cross.setAttribute("x2", X(t));
    cross.setAttribute("visibility", "visible");
    tip.replaceChildren();
    const head = document.createElement("div");
    head.className = "tip-time";
    head.textContent = full.format(t);
    tip.appendChild(head);
    series.forEach((s, k) => {
      const p = s.points[nearest(s.points, t)];
      const row = document.createElement("div");
      row.className = "tip-row";
      const key = document.createElement("i");
      key.style.background = s.color;
      const val = document.createElement("b");
      val.textContent = p && p[1] != null ? `${fmt(p[1])} ${o.unit || ""}`.trim() : "—";
      const name = document.createElement("span");
      name.textContent = s.name;
      row.append(key, val, name);
      tip.appendChild(row);
      if (p && p[1] != null) {
        dots[k].setAttribute("cx", X(p[0]));
        dots[k].setAttribute("cy", Y(p[1]));
        dots[k].setAttribute("visibility", "visible");
      } else dots[k].setAttribute("visibility", "hidden");
    });
    const b = o.band && o.band[nearest(o.band, t)];
    if (b && b[1] != null) {
      const row = document.createElement("div");
      row.className = "tip-sub";
      row.textContent = `mín ${fmt(b[1])} · máx ${fmt(b[2])}`;
      tip.appendChild(row);
    }
    if (o.extra) { const x = o.extra(t); if (x) { const r = document.createElement("div"); r.className = "tip-sub"; r.textContent = x; tip.appendChild(r); } }
    tip.hidden = false;
    const left = X(t) + 12 + tip.offsetWidth > W ? X(t) - 12 - tip.offsetWidth : X(t) + 12;
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = `${M.top}px`;
  };
  const hide = () => {
    idx = -1;
    tip.hidden = true;
    cross.setAttribute("visibility", "hidden");
    dots.forEach(d => d.setAttribute("visibility", "hidden"));
  };
  const hit = svg("rect", { x: M.left, y: 0, width: iw, height: H, fill: "transparent" }, root);
  hit.addEventListener("pointermove", e => {
    const r = root.getBoundingClientRect();
    show(nearest(ref, x0 + ((e.clientX - r.left - M.left) / iw) * (x1 - x0)));
  });
  hit.addEventListener("pointerleave", hide);
  root.addEventListener("keydown", e => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      show(Math.min(ref.length - 1, Math.max(0, (idx < 0 ? ref.length - 1 : idx) + (e.key === "ArrowRight" ? 1 : -1))));
    } else if (e.key === "Escape") hide();
  });
  root.addEventListener("blur", hide);
}

export function sparkline(el, points, color) {
  el.replaceChildren();
  const W = el.clientWidth || 160, H = el.clientHeight || 32;
  const pts = points.filter(p => p[1] != null);
  if (pts.length < 2) return;
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  let [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const root = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, "aria-hidden": "true" }, el);
  svg("path", { class: "line", stroke: color, d: pts.map((p, i) =>
    `${i ? "L" : "M"}${((p[0] - x0) / (x1 - x0 || 1)) * (W - 4) + 2},${H - 3 - ((p[1] - y0) / (y1 - y0)) * (H - 6)}`).join("") }, root);
}
