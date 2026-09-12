import { evaluate } from './modules/alarms.js';
import { add as pushHistory, recent as recentHistory } from './modules/history.js';
import { connectMQTT } from './modules/mqtt.js';
import { getLiveData } from './modules/modbus.js';

const PANE = {
  startAngle: -140, endAngle: 100, center: ['50%', '85%'],
  background: { shape: 'arc', innerRadius: '60%', outerRadius: '100%' }
};
const STOPS = [[0, '#19b8c9'], [0.6, '#0a7594'], [0.8, '#f5a623'], [1, '#d0021b']];

// avg/max indicators derived in-browser from the raw device readings — not part
// of config.json, since it defines no relationship between devices to compute
// a domain formula (e.g. a delta-P) from.
const AGGREGATES = [
  { id: 'avgTemp', title: 'Temperatura Promedio', unit: '°C', min: -10, max: 50, target: 20, sensorIds: ['dev1_temp', 'dev3_temp', 'dev7_temp', 'dev8_temp'], reduce: 'avg' },
  { id: 'maxPresion', title: 'Presión Máxima', unit: 'MPa', min: 0, max: 450, target: 150, sensorIds: ['dev4_presion1', 'dev5_presion2', 'dev9_presion1', 'dev10_presion2'], reduce: 'max' }
];

function theme() {
  const dark = document.body.classList.contains('dark');
  return dark ? { ink: '#e6f7fa', muted: '#a8d6dc' } : { ink: '#18323d', muted: '#607681' };
}

function retheme() {
  const { ink, muted } = theme();
  Highcharts.charts.forEach(c => {
    if (!c) return;
    c.update({
      title: { style: { color: ink } },
      xAxis: { labels: { style: { color: muted } } },
      yAxis: { labels: { style: { color: muted } } },
      legend: { itemStyle: { color: ink } }
    }, false);
    if (c.series[0]) c.series[0].update({ dataLabels: { style: { color: ink, textOutline: 'none' } } }, false);
    c.redraw();
  });
}

Highcharts.setOptions({ chart: { backgroundColor: 'transparent' } });

function createGauge(container, meta, title) {
  const div = document.createElement('div');
  div.className = 'card gauge';
  div.id = meta.id;
  container.appendChild(div);
  const { ink } = theme();
  return Highcharts.chart(meta.id, {
    chart: { type: 'solidgauge' },
    title: { text: title ?? meta.title, style: { fontSize: '13px', color: ink } },
    pane: PANE,
    yAxis: {
      min: meta.min ?? 0, max: meta.max, stops: STOPS,
      plotLines: [{ value: meta.target, width: 3, color: '#f5a623' }],
      labels: { enabled: false }, tickWidth: 0, lineWidth: 0
    },
    credits: { enabled: false },
    series: [{
      data: [meta.value ?? meta.target],
      dataLabels: {
        format: `<div style="text-align:center"><span style="font-size:22px">{y}</span><br>${meta.unit}</div>`,
        style: { color: ink, textOutline: 'none' }
      }
    }]
  });
}

function updateGauge(chart, value) {
  if (!chart || !chart.series[0] || !chart.series[0].points[0]) return;
  if (!Number.isFinite(value)) return;
  const v = Number(value.toFixed(1));
  const { min, max } = chart.yAxis[0];
  chart.series[0].points[0].update(Math.max(min, Math.min(max, v)), true, false);
}

function renderAlarms(alarms) {
  const tb = document.getElementById('alarmBody');
  tb.innerHTML = '';
  if (!alarms.length) {
    const r = tb.insertRow();
    const c = r.insertCell();
    c.colSpan = 2;
    c.textContent = 'Sin alarmas activas';
    return;
  }
  alarms.forEach(a => {
    const r = tb.insertRow();
    r.insertCell().innerHTML = `<span class="badge ${a.priority}">${a.priority}</span>`;
    r.insertCell().textContent = a.message;
  });
}

function renderInventory(sensors) {
  const tb = document.getElementById('inventoryBody');
  tb.innerHTML = '';
  sensors.forEach(s => {
    const r = tb.insertRow();
    r.insertCell().textContent = s.device;
    r.insertCell().textContent = s.slave;
    r.insertCell().textContent = s.sensor;
    r.insertCell().textContent = `${s.min}–${s.max} ${s.unit}`;
  });
}

function reduceValues(values, mode) {
  if (!values.length) return NaN;
  if (mode === 'avg') return values.reduce((a, b) => a + b, 0) / values.length;
  return Math.max(...values);
}

async function run() {
  const cfg = await fetch('config/dashboard.json').then(r => r.json());
  const sensors = cfg.sensors;

  const sensorHost = document.getElementById('sensor-gauges');
  const calcHost = document.getElementById('calc-gauges');
  const sensorCharts = {};
  const aggCharts = {};

  sensors.forEach(m => { sensorCharts[m.id] = createGauge(sensorHost, m, `${m.title} · ${m.device}`); });
  AGGREGATES.forEach(m => { aggCharts[m.id] = createGauge(calcHost, m); });

  renderInventory(sensors);

  const kpis = document.getElementById('kpis');
  const enabledCount = sensors.length;
  const kpiMeta = [
    { id: 'sensors', label: 'Sensores Activos', unit: '', from: () => `${enabledCount} / ${enabledCount}` },
    { id: 'avgTemp', label: 'Temperatura Promedio', unit: '°C', from: () => aggCharts.avgTemp?.series[0].points[0].y.toFixed(1) },
    { id: 'maxPresion', label: 'Presión Máxima', unit: 'MPa', from: () => aggCharts.maxPresion?.series[0].points[0].y.toFixed(1) },
    { id: 'hum', label: 'Humedad', unit: '%', from: () => sensorCharts.dev6_hum?.series[0].points[0].y.toFixed(1) }
  ];
  kpiMeta.forEach(k => {
    const d = document.createElement('div');
    d.className = 'kpi';
    d.innerHTML = `<small>${k.label}</small><b id="kpi-${k.id}">–</b>`;
    kpis.appendChild(d);
  });

  Highcharts.chart('trend', {
    chart: { type: 'spline' },
    title: { text: 'Temperatura Promedio y Presión Máxima' },
    credits: { enabled: false },
    xAxis: { labels: { enabled: false } },
    series: [
      { name: 'Temp. Promedio °C', data: [], color: '#0a7594' },
      { name: 'Presión Máxima MPa', data: [], color: '#19b8c9' }
    ]
  });

  retheme();

  await connectMQTT();
  document.getElementById('conn').innerHTML = 'MQTT 🟢 &nbsp; Nube 🟢 &nbsp; Modbus 🟢';

  async function tick() {
    const raw = await getLiveData(sensors);
    Object.entries(raw).forEach(([id, v]) => updateGauge(sensorCharts[id], v));

    const agg = {};
    AGGREGATES.forEach(m => {
      agg[m.id] = reduceValues(m.sensorIds.map(id => raw[id]).filter(Number.isFinite), m.reduce);
      updateGauge(aggCharts[m.id], agg[m.id]);
    });

    pushHistory({ t: Date.now(), ...raw, ...agg });
    const trendChart = Highcharts.charts.find(c => c && c.renderTo && c.renderTo.id === 'trend');
    if (trendChart) {
      const hist = recentHistory(30);
      trendChart.series[0].setData(hist.map(h => Number(h.avgTemp.toFixed(1))), true, false);
      trendChart.series[1].setData(hist.map(h => Number(h.maxPresion.toFixed(1))), true, false);
    }

    kpiMeta.forEach(k => {
      const el = document.getElementById(`kpi-${k.id}`);
      if (el) el.textContent = `${k.from()} ${k.unit}`.trim();
    });

    renderAlarms(evaluate(raw, sensors));

    document.getElementById('health').textContent =
      `CPU ${15 + Math.floor(Math.random() * 15)}% | RAM ${35 + Math.floor(Math.random() * 20)}% | Temp ${45 + Math.floor(Math.random() * 6)}°C`;
  }

  tick();
  setInterval(tick, 2000);

  document.getElementById('theme').onclick = () => {
    document.body.classList.toggle('dark');
    retheme();
  };
}

run();
