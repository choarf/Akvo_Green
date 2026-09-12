// Generic threshold check driven by each sensor's own config (target/min/max),
// since the real device list (config.json) has no fixed named process variables
// to hang special-cased rules off of. Distance from target is measured as a
// fraction of the half-span so the same thresholds work across very different
// units/ranges (°C vs MPa vs %).
export function evaluate(readings, sensors) {
  const alarms = [];
  sensors.forEach(s => {
    const v = readings[s.id];
    if (!Number.isFinite(v)) return;
    const label = `${s.title} (${s.device})`;
    const halfSpan = (s.max - s.min) / 2;
    const dist = Math.abs(v - s.target) / halfSpan;
    if (v >= s.max || v <= s.min || dist >= 0.7) alarms.push({ priority: 'Critical', message: `${label} fuera de rango seguro` });
    else if (dist >= 0.35) alarms.push({ priority: 'Warning', message: `${label} alejado del objetivo` });
  });
  return alarms;
}
