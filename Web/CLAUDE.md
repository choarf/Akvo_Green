# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

This is not a single application — it's a workspace containing several iterations of a static, client-side industrial dashboard ("AKVO Dashboard") for monitoring a reverse-osmosis (RO) water treatment process. There is no git repo, no build tooling, and no package manager anywhere in this tree; every dashboard is plain HTML/CSS/JS loaded directly by the browser, with charts rendered via the Highcharts CDN.

Top-level directories:

- **`AKVO-Dashboard-V6/`** — the baseline dashboard. Self-contained: `index.html`, `css/dashboard.css`, `js/app.js`, `config/dashboard.json`. Single-page layout (sidebar + header + KPI cards + gauges + trend/CIP charts + alarm table + system health panel), all data simulated client-side.
- **`AKVO-Dashboard-V6_1_V7_V8/AKVO-Dashboard-V6/`** — a rebranded/localized fork of V6 (Spanish labels, "Ghotrix-Semca Dashboard" branding, extra gauges, tweaked gauge arc angles, formatted/expanded `app.js`, and a `updateCalculated()` helper for derived gauges). Same file layout as V6.
- **`AKVO-Dashboard-V8-Scaffold/`** — the next architectural step: still contains a full copy of the V6 dashboard under `AKVO-Dashboard-V6/`, plus new top-level `config/formulas.json`, `docs/ROADMAP.md`, and `js/modules/` — small ES modules (`formula-engine.js`, `mqtt.js`, `modbus.js`, `alarms.js`, `history.js`) that are scaffolded but **not yet wired into** `index.html`/`app.js`.
- **`VENKO-AKVO-Dashboard/`** — a newer dashboard that merges the three packages above into one working app, restyled to the real GHOTRIX brand (ghotrix.com.mx: navy `#07364a`/blue `#0a7594`/cyan `#19b8c9`/aqua `#dff6f7`, Inter font, "GHO`TRIX`" wordmark, tagline "Convertimos el agua en una ventaja operativa"). Unlike V8-Scaffold, its alarms, history and MQTT/Modbus modules are actually wired into the UI, and its sensor list mirrors the real edge-gateway config in `../Akvo_Green/src/config.json` rather than invented RO variables — see its own architecture notes below.

When making a change, check which of these trees the user actually means — they are independent copies, not branches, and edits in one do not propagate to the others. If a fix or feature clearly applies to all of them (e.g. a real bug in the shared gauge-rendering logic), say so and confirm before touching every copy.

## Running / previewing a dashboard

There is no dev server or build step. Open the relevant `index.html` directly in a browser, or serve the directory statically, e.g.:

```bash
cd AKVO-Dashboard-V6 && python3 -m http.server 8000
```

then visit `http://localhost:8000`. `fetch('config/dashboard.json')` requires serving over HTTP (not `file://`) in most browsers.

There are no automated tests, linters, or CI in this repository.

## Architecture notes (V6 baseline, and its V6_1_V7_V8 fork)

- `index.html` is a minimal shell: a `<aside>` sidebar, a `<header>`, a KPI `<section>`, an empty `#gauges` section, and a `.grid` of cards (trend chart, cleaning chart, alarm table, system health). All Highcharts libraries (`highcharts.js`, `highcharts-more.js`, `modules/solid-gauge.js`) are loaded from `code.highcharts.com`.
- `js/app.js` does all the work in one `run()` function, called on load:
  - Fetches `config/dashboard.json` and dynamically creates one solid-gauge Highcharts chart per entry in `gauges[]` (each entry has `id`, `title`, `unit`, `value`, `max`, `target`).
  - Builds two more static charts: `#trend` (24h flow) and `#cleaning` (CIP before/after).
  - Populates the alarm table from a hardcoded `alarms` array (no real alarm engine).
  - Runs a `setInterval` every second that randomly jitters every chart's first data point (simulated live data) and randomizes the "System Health" CPU/RAM/Temp text.
  - Wires the theme toggle button to `document.body.classList.toggle('dark')`; dark-mode colors are CSS variables in `dashboard.css` (`:root` vs `.dark`).
- `config/dashboard.json` is the single source of truth for which gauges render and their scale/target — add/remove a gauge by editing this file; `app.js` needs no changes to pick it up (it just creates a `<div id="x.id">` per entry).
- The V6_1_V7_V8 fork keeps this exact structure but reformats `app.js`, localizes UI strings to Spanish, changes gauge pane angles (`startAngle`/`endAngle`), adds more gauge entries to `dashboard.json` (pressures 5/6, delta-P, feed/permeate flow, fouling), and adds an unused `updateCalculated(refs)` helper intended to derive Recovery/DeltaP/Fouling from raw gauge chart objects rather than from formulas.json.

## Architecture notes (V8-Scaffold)

`AKVO-Dashboard-V8-Scaffold/js/modules/` scaffolds the next generation of the app as small ES modules, per `docs/ROADMAP.md`:

- `formula-engine.js` — `calculate(data)` computes `recovery`, `deltaP`, `saltRejection`, `fouling` from raw process values (`permflow`, `feedflow`, `presion1`, `presion4`, `permCond`, `feedCond`). These formulas are duplicated as strings in `config/formulas.json` (not yet parsed/evaluated dynamically — the JSON and the JS are two independent representations of the same math and can drift).
- `mqtt.js` / `modbus.js` — connection placeholders (`connectMQTT()` just logs; `getLiveData()` fetches a `/api/live` REST endpoint that doesn't exist yet). Real-time data acquisition (MQTT for live values, Modbus REST for polled values) is not implemented.
- `alarms.js` — `evaluate(d)` derives alarm objects from computed values (currently only checks `deltaP > 20` and `recovery < 70`); this is not yet connected to the UI's alarm table.
- `history.js` — an in-memory ring buffer (`ringBuffer`, capped at 3600 samples) for trend history; not yet persisted (roadmap calls for IndexedDB) or connected to any chart.

Per `docs/ROADMAP.md`, remaining integration work is: swap simulated values for real MQTT/Modbus data, load formulas from `formulas.json` instead of hardcoding them in `formula-engine.js`, connect `alarms.js` to the UI, persist history to IndexedDB, and add Highcharts Stock for historical trends. None of these modules are imported by an `index.html`/`app.js` in the scaffold yet — treat them as building blocks to be wired up, not working features.

## Architecture notes (VENKO-AKVO-Dashboard)

This is the only package where the module boundary actually does something at runtime. `js/app.js` (loaded as `type="module"`) imports from `js/modules/` and orchestrates everything from one `run()`.

- **Sensors are config-driven from real hardware, not invented RO chemistry.** `config/dashboard.json`'s `sensors[]` array is a hand-mirrored copy of the actual edge-gateway device inventory in `../Akvo_Green/src/config.json` (and its `devices.csv`/`system.csv` siblings) — one entry per `{device, slave, sensor}` triple (`DEV_1`…`DEV_10`, sensors `Temp`/`Presion1`/`Presion2`/`Hum`), carrying that config's real `unit`/`min`/`max`. **When the physical device list in `Akvo_Green/src/config.json` changes, update `dashboard.json` to match — it is not derived automatically.** `target` values in `dashboard.json` are placeholder midpoints (the real config only defines calibration min/max, not operating setpoints); there's no `formulas.json`/formula-engine here (an earlier version invented flow/conductivity fields like `feedflow`/`recovery` that don't exist on the real hardware — dropped for the same reason: no such sensors are wired up, and no documented relationship between devices — e.g. which pressure pair belongs to which train — exists to justify a cross-device formula like delta-P).
- **`js/modules/modbus.js`** — `getLiveData(sensors)` tries `fetch('/api/live')` first and falls back to an in-module `simulate(sensors)` if that gateway isn't there (it never is, in this static setup). `simulate()` is a mean-reverting random walk per sensor (5% pull back toward `target` + small noise each tick, persisted in a module-level `state` object) rather than independent-per-tick jitter — this keeps gauges quiet most of the time with only occasional excursions, instead of noise that's symmetric around `target` and trips "above target" alarms on roughly half of all ticks. Swapping in a real Modbus REST gateway later is a one-line change.
- **`js/modules/alarms.js`** — `evaluate(readings, sensors)` is generic: for every sensor it computes `dist = |value - target| / halfSpan` and fires Warning at `dist >= 0.35`, Critical at `dist >= 0.7` (or outright outside `[min, max]`). This works uniformly across unrelated units (°C, MPa, %) because it's relative to each sensor's own span — there are no named per-field rules like the old `deltaP > 20`.
- **Aggregated indicators** (`AGGREGATES` in `app.js`, not sensor-driven config): `avgTemp` (mean of the four `Temp` sensors) and `maxPresion` (max across the four `Presion1`/`Presion2` sensors) are computed client-side each tick and rendered as two more solid-gauge cards under "Indicadores Agregados", plus feed the `#trend` spline. These are safe generic reductions (avg/max) — not a fabricated cross-device process formula.
- **`js/modules/history.js`** — same ring-buffer shape as V8-Scaffold, plus a `recent(n)` accessor; `app.js`'s `tick()` pushes each raw-reading + aggregate sample and redraws `#trend` from the last 30 samples.
- **Device inventory table** — the "Inventario de Dispositivos" card lists every `sensors[]` entry's device/slave/sensor/range straight from `dashboard.json`, giving a quick way to see the full config-driven device list without opening the JSON.
- **`tick()`** (called immediately, then every 2s) is the whole live loop: `getLiveData(sensors)` → update sensor gauges → reduce `AGGREGATES` and update those gauges → push raw+aggregate sample to history → redraw trend → update KPI cards → `evaluate(raw, sensors)` → render alarms → randomize the System Health text.
- **Theming**: `css/dashboard.css` defines the GHOTRIX palette as CSS variables (`:root` vs `.dark`, using the site's own dark navy tones for the dark variant). Because Highcharts paints its own opaque background and SVG-text colors independently of surrounding CSS, `app.js` calls `Highcharts.setOptions({chart:{backgroundColor:'transparent'}})` once up front and defines `retheme()` (called after initial chart creation and again from the theme-toggle handler) to push the current ink/muted colors into every chart's title, axis labels, legend, and data-label styles. If you add a new chart type here, make sure it's swept by `retheme()` or dark mode will show it with unreadable text.
- MQTT (`connectMQTT()`) is still a placeholder that only logs and flips the header's status dot; there's no real broker integration.
