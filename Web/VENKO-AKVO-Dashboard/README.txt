VENKO-AKVO Dashboard
Merged from AKVO-Dashboard-V6, V6_1_V7_V8 and V8-Scaffold, restyled to the GHOTRIX (ghotrix.com.mx) brand.
config/dashboard.json's sensor list mirrors the real edge-gateway device inventory in
Akvo_Green/src/config.json (devices/slaves/sensors, units and min/max) — update that file
first when the physical device list changes, then mirror the change here.
Aggregated indicators (avg temperature, max pressure), the alarm manager and the trend chart
are computed client-side from live/simulated readings. MQTT/Modbus modules are swappable data
sources (currently simulated, since no relationship between devices is defined to compute a
process formula like delta-P from).
