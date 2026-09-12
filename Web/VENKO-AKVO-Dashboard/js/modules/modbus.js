// Modbus REST — tries a real gateway first, falls back to a simulator so the
// dashboard runs standalone until /api/live exists.
export async function getLiveData(sensors) {
  try {
    const r = await fetch('/api/live');
    if (!r.ok) throw new Error('no gateway');
    return await r.json();
  } catch {
    return simulate(sensors);
  }
}

// Mean-reverting random walk (not independent-per-tick jitter) so values drift
// smoothly and only occasionally wander far enough from target to trip an alarm.
const state = {};

function simulate(sensors) {
  const out = {};
  sensors.forEach(s => {
    const span = s.max - s.min;
    const prev = state[s.id] ?? s.target;
    const reversion = (s.target - prev) * 0.05;
    const noise = (Math.random() - 0.5) * span * 0.05;
    const next = Math.max(s.min, Math.min(s.max, prev + reversion + noise));
    state[s.id] = next;
    out[s.id] = next;
  });
  return out;
}
