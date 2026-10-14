# OLED screen: spec

Status display for the AKVO Green gateway (Raspberry Pi). The simulator
[`oled_simulator.html`](oled_simulator.html), in this folder, implements this spec: open it in a
browser. The implementation plan is in `docs/plans/oled_display.md` in the sibling `Akvo_Green` repo.

## Hardware

- 1.54" OLED module, 128×64 px, monochrome, **SSD1309** driver, I2C (GND/VCC/SCL/SDA,
  address `0x3C`).
- On the Pi's header: VCC → 3.3 V (pin 1), GND → pin 6, SDA → GPIO 2 (pin 3),
  SCL → GPIO 3 (pin 5).

## Screen sequence

1. **GHOTRIX logo**, at start-up only: the "Venko → Akvo" line writes itself on 3 times (100%), then flashes 3 times (5.1 s), with the gateway name (`gateway_id`) as a third row under it.
2. **Sistema** (5 s).
3. **Sensores**: the sensor list, scrolling.
4. **Sensores** again.
5. Back to step 2: Sistema → Sensores → Sensores → … forever.

Each screen has an inverted header bar: the title on the left, the time `HH:MM:SS` on the right.

## Font

3×5 bitmap font, uppercase only, in a 4×6 px cell: **32 columns × 10 rows** (header + 9 text
rows). Accents are dropped (ENVIO, LIMITES). A 5×7 font (21 × 8) is the fallback if the 3×5
is too small to read on the real module.

## Sistema screen

The same information as the local web page's *Sistema* → Conexiones.

the screen shall show
SISTEMA                 17:42:10
CLIENTE Ghotrix
GATEWAY Activo > 16 s \\ Inactivo > HH:MM:SS
SSID Vocho
RED     Activo > IP 192.168.68.129 \\ Inactivo > HH:MM:SS
AWS     Activo > 16 s  \\Inactivo > HH:MM:SS
DATABASE size XX MB - 78%
MODBUS  Avtivo \\ !simulado
ALARMAS ▲ 5 ACTIVAS
  SENSORES FUERA DE LIMITES


| Row | Normal | Fault (shown as an inverted bar) |
|---|---|---|
| Gateway | `ACTIVO` + age of the status file | `DETENIDO` (no update in 3 poll intervals) |
| Red / Internet | `CONECTADO` + Pi's IP | `SIN CONEXION` + "desde hace …" |
| AWS | `CONECTADO` + "envío hace N s" | `DESCONECTADO` + "desde hace …" |
| Modbus | `OK` + sensor count; `SIMULADO`; `ERRORES n/total` | `PTO CERRADO`, `NO RESPONDE` |
| Alarmas | `N ACTIVAS` + "sensores fuera de límites", or `NINGUNA` | — |

## Sensores screen

One row per sensor: **name, value, unit, flag**.

```
SENSORES 1-9/15         17:42:15
PT100           24.0  °C      ■
AMBIENT         57.0  °C      ▲
ORP           -356.0  MV      ■
TEMPCOD         ----  °C      ✕
```

- **Flag:**
  - `■` normal;
  - `▲` high, i.e. over the sensor's `max` (blinks);
  - `▼` low, i.e. under the sensor's `min` (blinks);
  - `✕` read error / out of range.
- **Names and units** come from the site's `config.json`:
  - a trailing `Sensor` is dropped from the name (12 characters max);
  - units are shortened to 6 characters max (`uS/cm` → `µS`).
- **Scroll:** 1 px at a time, about 6 px/s (one row per second):
  - it waits 1.5 s at the top and 1.5 s at the bottom;
  - a scrollbar on the right edge shows the position, and the header shows the visible range;
  - a list that fits on one screen doesn't scroll and stays up for 5 s.

## Burn-in

- Screens rotate and the list scrolls.
- The image shifts by 1 px each round.
- Optional: lower contrast after some minutes, screen off at night.

## Data source

The gateway's status file `/dev/shm/akvo-green-status.json`, rewritten every poll cycle. It holds:

- network, AWS/MQTT and Modbus state;
- read/alarm counts;
- `readings`: each sensor's `val`, `status` and `alarm`.

Sensor names, units and limits come from `config.json`.

## Simulator

`oled_simulator.html` is self-contained (no dependencies):

- **Display:** pixel-accurate 128×64, with the 3×5 and 5×7 fonts.
- **Sequence:** adjustable timing and repeats (*Sensores por vuelta*, logo boot-only or every
  N rounds); scroll or page mode for the sensor list.
- **Failure states:** gateway, network, AWS and Modbus can be switched to theirs; each sensor
  can be forced to high, low or error.
- **Sensors:** loaded from any site's `config.json` (cmd4 by default).
- **Display options:** color, real-size zoom, contrast, burn-in shift.
- **Output:** custom logo upload (converted to 1-bit); *Exportar PNG* saves the current screen
  as a 128×64 image to compare against the real display code.

## References
-/add-dir /home/chris/git_reps/claude/projects/Venko/Akvo_Green
- Read access to `~/git_reps/claude/projects/Venko` and its subfolders.
- Akvo local web page *Sistema* rows: `Akvo_Green/gateway/web/app.js` (`connectionRows`).
