"""
OLED status display: 1.54" 128x64 monochrome SSD1309 module over I2C.

Runs as its own process/systemd service (akvo-oled), separate from
edge_node_improved.py on purpose, like history_web.py: it only reads the
gateway's status file (rewritten every poll cycle, see
EdgeNode.write_status()) and config.json, so a display or I2C problem can
never stall Modbus polling or MQTT publishing.

Screens (visual spec: docs/OLED/oled_simulator.html):
  at start-up   GHOTRIX logo; its "Venko -> Akvo" line writes itself on 3
                times (100%) and then flashes 3 times (5.1 s); the
                gateway_id below it
  then forever  Sistema (5 s) -> Sensores (scrolling list) x sensor_repeats

Everything is drawn in plain Python into a 128x64 framebuffer (same fonts
and layout as the simulator); luma.oled only ships the finished frame to the
module. So the screens can be rendered and tested without the hardware:

  python3 oled_display.py                      # drives the display (display.enabled)
  python3 oled_display.py --png /tmp/oled      # writes each screen as PNG and exits

Enabled by system.csv's display_* columns (config.json "display" section).
Read once at startup: a change needs a service restart (push_site.sh does it).
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import math
import shutil
import signal
import socket
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from history_web import DEFAULT_CONFIG, DEFAULT_DB, DEFAULT_STATUS, read_status

BASE_DIR = Path(__file__).resolve().parent
LOGO_PATH = BASE_DIR / "oled_logo.png"   # optional 1-bit artwork; text logo when absent

W, H = 128, 64
DRIVERS = ("ssd1309", "ssd1306", "sh1106")
DEFAULT_ADDRESS = 0x3C
SH1106_COLUMN_OFFSET = 2   # luma's default; display.column_offset overrides it per board
BOOT_PASSES = 3           # the logo's boot line writes itself on left to right this many times...
BOOT_PASS_SECONDS = 1.2
BOOT_FLASHES = 3          # ...then flashes (off, on) this many times
BOOT_FLASH_SECONDS = 0.5
LOGO_SECONDS = BOOT_PASSES * BOOT_PASS_SECONDS + BOOT_FLASHES * BOOT_FLASH_SECONDS   # 5.1 s
SCREEN_SECONDS = 5.0
SCROLL_PAUSE = 1.5        # s still at the top and at the bottom of the sensor list
SCROLL_SPEED = 6.0        # px/s: one 3x5 text row per second
STATUS_EVERY = 1.0        # s between status-file reads
SHIFTS = ((0, 0), (1, 0), (1, 1), (0, 1))   # burn-in: whole image moves 1 px per round

logger = logging.getLogger("oled_display")


# ----------------------------------------------------------------------
# Fonts - copied from the simulator, so both draw exactly the same pixels
# ----------------------------------------------------------------------

# 5x7: ASCII 0x20-0x7E, then extras; 5 column bytes per glyph, LSB = top row, bit 7 = descender.
_F57_ASCII = (
    "0000000000 00005F0000 0007000700 147F147F14 242A7F2A12 2313086462 3649552250 0005030000 "
    "001C224100 0041221C00 082A1C2A08 08083E0808 0050300000 0808080808 0060600000 2010080402 "
    "3E5149453E 00427F4000 4261514946 2141454B31 1814127F10 2745454539 3C4A494930 0171090503 "
    "3649494936 064949291E 0036360000 0056360000 0814224100 1414141414 0041221408 0201510906 "
    "324979413E 7E1111117E 7F49494936 3E41414122 7F4141221C 7F49494941 7F09090101 3E41415132 "
    "7F0808087F 00417F4100 2040413F01 7F08142241 7F40404040 7F0204027F 7F0408107F 3E4141413E "
    "7F09090906 3E4151215E 7F09192946 4649494931 01017F0101 3F4040403F 1F2040201F 7F2018207F "
    "6314081463 0304780403 6151494543 007F414100 0204081020 0041417F00 0402010204 4040404040 "
    "0001020400 2054545478 7F48444438 3844444420 384444487F 3854545418 087E090102 18A4A4A47C "
    "7F08040478 00447D4000 4080847D00 007F102844 00417F4000 7C04180478 7C08040478 3844444438 "
    "FC24242418 18242424FC 7C08040408 4854545420 043F444020 3C4040207C 1C2040201C 3C4030403C "
    "4428102844 1CA0A0A07C 4464544C44 0008364100 00007F0000 0041360800 0804081008 "
).split()
_F57_EXTRA = {
    "°": "0006090906", "µ": "FC4040207C", "á": "2054565578", "é": "3854565518", "í": "00447E4100", "ó": "3844464538",
    "ú": "3C4042217C", "ñ": "7C0A050679", "Á": "7C1213117C", "É": "7C54565544", "▲": "60787E7860", "▼": "030F3F0F03",
    "●": "1C3E3E3E1C", "•": "001C1C1C00", "■": "001C1C1C00", "✕": "2214081422", "⚠": "6078527860", "·": "0000180000", "…": "4000400040",
}
# 3x5 small caps: 5 octal digits per glyph = rows top to bottom, 3 bits each (MSB = left).
_F35_ROWS = {
    " ": "00000", "0": "75557", "1": "26227", "2": "71747", "3": "71317", "4": "55711", "5": "74717", "6": "74757",
    "7": "71122", "8": "75757", "9": "75717", "A": "25755", "B": "65656", "C": "34443", "D": "65556", "E": "74647",
    "F": "74644", "G": "34553", "H": "55755", "I": "72227", "J": "11152", "K": "55655", "L": "44447", "M": "57755",
    "N": "65555", "O": "25552", "P": "65644", "Q": "25573", "R": "65655", "S": "34216", "T": "72222", "U": "55557",
    "V": "55552", "W": "55572", "X": "55255", "Y": "55222", "Z": "71247", ".": "00002", ",": "00024", ":": "02020",
    "-": "00700", "+": "02720", "/": "11244", "%": "51245", "(": "12221", ")": "42224", "_": "00007", "!": "22202",
    "?": "71202", "'": "22000", "\"": "55000", "=": "07070", "<": "12421", ">": "42124", "°": "25200", "µ": "55574",
    "▲": "00270", "▼": "00720", "●": "07770", "•": "07770", "■": "07770", "✕": "05250", "⚠": "22202", "·": "00200",
}


def _f57_glyphs() -> dict[str, list[int]]:
    cols = lambda h: [int(h[i:i + 2], 16) for i in range(0, 10, 2)]
    glyphs = {chr(0x20 + i): cols(h) for i, h in enumerate(_F57_ASCII)}
    glyphs.update({ch: cols(h) for ch, h in _F57_EXTRA.items()})
    return glyphs


def _f35_glyphs() -> dict[str, list[int]]:
    glyphs = {}
    for ch, octal in _F35_ROWS.items():
        rows = [int(d, 8) for d in octal]
        glyphs[ch] = [sum(((r >> (2 - c)) & 1) << i for i, r in enumerate(rows)) for c in range(3)]
    return glyphs


@dataclass(frozen=True)
class Font:
    glyphs: dict
    cw: int        # cell width (glyph + spacing)
    gw: int        # glyph width
    gh: int        # glyph height incl. descender row
    row_h: int     # text row pitch
    head_h: int    # inverted header bar height
    head_y: int    # text y inside the header
    text_dy: int   # text y inside a row
    sens: tuple    # sensor row: name, value, unit widths (columns), spacer

    @property
    def cols(self) -> int:
        return W // self.cw                            # 32 (3x5) or 21 (5x7)

    @property
    def rows(self) -> int:
        return (H - self.head_h) // self.row_h         # text rows under the header: 9 or 7

    def glyph(self, ch: str) -> list[int]:
        """Exact glyph, else the unaccented / uppercase letter (3x5 has
        capitals only: á -> A, ñ -> N), else '?'."""
        if ch in self.glyphs:
            return self.glyphs[ch]
        base = unicodedata.normalize("NFD", ch)[0]
        return self.glyphs.get(base) or self.glyphs.get(base.upper()) or self.glyphs["?"]


FONTS = {
    "3x5": Font(_f35_glyphs(), cw=4, gw=3, gh=5, row_h=6, head_h=7, head_y=1, text_dy=1, sens=(12, 8, 6, "  ")),
    "5x7": Font(_f57_glyphs(), cw=6, gw=5, gh=8, row_h=8, head_h=8, head_y=0, text_dy=0, sens=(8, 6, 4, " ")),
}


# ----------------------------------------------------------------------
# Framebuffer
# ----------------------------------------------------------------------

def fit(s: str, n: int) -> str:
    return s[:n]


def pad_end(s: str, n: int) -> str:
    return fit(s, n).ljust(n)


def pad_start(s: str, n: int) -> str:
    return fit(s, n).rjust(n)


class Frame:
    """One 128x64 1-bit frame. px[y * W + x] is 1 for a lit pixel."""

    def __init__(self, font: Font):
        self.font = font
        self.px = bytearray(W * H)

    def fill(self, x: int, y: int, w: int, h: int, v: int = 1) -> None:
        x0, x1 = max(x, 0), min(x + w, W)
        if x0 >= x1:
            return
        for j in range(max(y, 0), min(y + h, H)):
            self.px[j * W + x0:j * W + x1] = bytes([v]) * (x1 - x0)

    def box(self, x: int, y: int, w: int, h: int) -> None:
        self.fill(x, y, w, 1)
        self.fill(x, y + h - 1, w, 1)
        self.fill(x, y, 1, h)
        self.fill(x + w - 1, y, 1, h)

    def text_w(self, s: str, scale: int = 1, font: Font | None = None) -> int:
        f = font or self.font
        return len(s) * f.cw * scale - scale * (f.cw - f.gw) if s else 0

    def text(self, x: int, y: int, s: str, inv: bool = False, scale: int = 1, font: Font | None = None) -> None:
        f = font or self.font
        v = 0 if inv else 1
        for ch in s:
            g = f.glyph(ch)
            for c in range(f.gw):
                for r in range(f.gh):
                    if (g[c] >> r) & 1:
                        self.fill(x + c * scale, y + r * scale, scale, scale, v)
            x += f.cw * scale

    def center_x(self, s: str, scale: int = 1, font: Font | None = None) -> int:
        return round((W - self.text_w(s, scale, font)) / 2)

    def row_at(self, y: int, left: str, right: str = "", inv: bool = False) -> None:
        """A text row at pixel y; inv = lit bar with dark text (faults)."""
        f = self.font
        if inv:
            self.fill(0, y, W, f.row_h)
        self.text(1, y + f.text_dy, fit(left, f.cols), inv)
        if right:
            self.text(W - 1 - self.text_w(right), y + f.text_dy, right, inv)

    def row(self, r: int, left: str, right: str = "", inv: bool = False) -> None:
        """Text row r, 1 = first row under the header."""
        self.row_at(self.font.head_h + (r - 1) * self.font.row_h, left, right, inv)

    def header(self, title: str, clock: str) -> None:
        f = self.font
        self.fill(0, 0, W, f.head_h)
        self.text(1, f.head_y, fit(title, f.cols - 9), inv=True)
        self.text(W - 1 - self.text_w(clock), f.head_y, clock, inv=True)

    def shifted(self, dx: int, dy: int) -> bytes:
        """The pixels moved right/down by dx/dy (burn-in protection)."""
        if not (dx or dy):
            return bytes(self.px)
        out = bytearray(W * H)
        for y in range(H - dy):
            out[(y + dy) * W + dx:(y + dy + 1) * W] = self.px[y * W:(y + 1) * W - dx]
        return bytes(out)

    def image(self, shift: tuple[int, int] = (0, 0)):
        """1-bit Pillow image, as luma.oled's display() wants it."""
        from PIL import Image
        data = bytes(255 if p else 0 for p in self.shifted(*shift))
        return Image.frombytes("L", (W, H), data).convert("1")


# ----------------------------------------------------------------------
# Screen content - pure functions of the status file / config.json
# ----------------------------------------------------------------------

def ago_long(s: float) -> str:
    s = max(0, round(s))
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{round(s / 60)} min"
    if s < 172800:
        return f"{round(s / 3600)} h"
    return f"{round(s / 86400)} días"


def ago_short(s: float) -> str:
    s = max(0, round(s))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{round(s / 60)}m"
    if s < 172800:
        return f"{round(s / 3600)}h"
    return f"{round(s / 86400)}d"


def hms(s: float) -> str:
    """A duration as HH:MM:SS (hours keep counting past 24)."""
    s = max(0, round(s))
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def size_mb(b: int) -> str:
    mb = b / 1e6
    if mb >= 1000:
        return f"{mb / 1000:.1f} GB"
    return f"{mb:.1f} MB" if mb < 10 else f"{round(mb)} MB"


DISK_FULL_PCT = 90   # the DATABASE row turns into a fault bar from here
# Row labels: the 3x5 font has room for the full word, the 5x7 one doesn't.
LABELS = {"Gateway": "GW", "Red": "Red", "AWS": "AWS", "Database": "DB", "Modbus": "Mod", "Alarmas": "Alm"}


def system_lines(st: dict, now: float, ip: str | None, wide: bool, blink_on: bool = True,
                 db: dict | None = None, ssid: str | None = None, client: str = "") -> list[tuple]:
    """Sistema rows as (left, right, fault), one line per item (docs/OLED/init.md):

      CLIENTE GHOTRIX                       (system.csv client; no row without one)
      GATEWAY ACTIVO > 16 S                 / ✕INACTIVO > HH:MM:SS
      SSID    VOCHO                         / SIN WIFI (cable only, or WiFi not joined)
      RED     ACTIVO > 192.168.68.129       / ✕INACTIVO > HH:MM:SS
      AWS     ACTIVO > 16 S                 / ✕INACTIVO > HH:MM:SS
      DATABASE 59 MB - 78%                  (history.db size - disk use)
      MODBUS  ACTIVO > 15 SENS              / ⚠SIMULADO / ✕PTO CERRADO ...
      ALARMAS ▲ 5 ACTIVAS + SENSORES FUERA DE LIMITES

    ACTIVO shows how recent the last sign of life is (status file age, last
    AWS send) or the Pi's IP; INACTIVO how long it has been down. Fault rows
    are inverted bars and say INACTIVO in words, never by inverse video
    alone. wide = the 3x5 font's 32 columns: the detail goes inline after
    ">"; with the 21-column 5x7 font it's right-aligned after a short label.
    st is history_web.read_status()'s result, now epoch seconds, db
    database_info()'s result (None = no DATABASE row), ssid wifi_ssid()'s,
    client config.json's gateway.client (5x7: no room, not shown)."""
    def line(label, state, detail="", fault=False, icon=""):
        if wide:   # "DATABASE" fills the 8-column label: one space before its value
            return (f"{label:<7} " + icon + state + (f" > {detail}" if detail else ""), "", fault)
        return (LABELS[label].ljust(4) + ("" if fault else icon) + state, detail, fault)

    def down(label, since):
        dur = (hms(now - since) if wide else ago_short(now - since)) if since else ""
        return line(label, "Inactivo", dur, True, "✕")

    lines = []
    if wide and client:
        lines.append(line("Cliente", client))
    if not st.get("available"):
        lines.append(line("Gateway", "Sin estado", "", True, "✕"))
    elif st.get("stale"):
        lines.append(line("Gateway", "Inactivo", hms(st["age_s"]) if wide else ago_short(st["age_s"]), True, "✕"))
    else:
        lines.append(line("Gateway", "Activo", ago_long(st["age_s"]) if wide else ago_short(st["age_s"])))
    if wide:   # the WiFi the Pi is on; none isn't a fault (a Pi on a cable has none)
        lines.append(line("SSID", ssid or "Sin WiFi"))
    n = st.get("network")
    if n:
        if not n.get("connected"):
            lines.append(down("Red", n.get("since")))
        elif wide:
            lines.append(line("Red", "Activo", ip or ""))
        else:   # 5x7: the SSID goes on the state row, the IP on a row of its own
            lines.append(line("Red", "Activo", fit(ssid or "cable", 9)))
            if ip:
                lines.append(("  " + ip, "", False))
    m = st.get("mqtt")
    if m:
        if m.get("connected"):
            age = now - m["last_publish"] if m.get("last_publish") else None
            lines.append(line("AWS", "Activo", "" if age is None else ago_long(age) if wide else ago_short(age)))
        else:
            lines.append(down("AWS", m.get("since")))
    if db is not None:
        if not db.get("enabled"):
            lines.append(line("Database", "Desactivada"))
        else:
            pct = db.get("disk_pct")
            size = size_mb(db["size"]) if db.get("size") is not None else "sin archivo"
            full = pct is not None and pct >= DISK_FULL_PCT
            pct_s = f"{round(pct)}%" if pct is not None else ""
            lines.append(line("Database", size + (f" - {pct_s}" if wide and pct_s else ""),
                              "" if wide else pct_s, full, "✕" if full else ""))
    b = st.get("modbus")
    if b:
        if b.get("simulated"):
            lines.append(line("Modbus", "Simulado", icon="⚠"))
        elif not b.get("connected"):
            lines.append(line("Modbus", "Pto cerrado", "", True, "✕"))
        elif b.get("sensors") and b.get("read_errors") == b.get("sensors"):
            lines.append(line("Modbus", "No responde", "", True, "✕"))
        elif b.get("read_errors"):
            lines.append(line("Modbus", "Errores", f"{b['read_errors']}/{b.get('sensors', '?')}", icon="⚠"))
        else:
            lines.append(line("Modbus", "Activo", f"{b.get('sensors', 0)} sens"))
        if b.get("alarms"):
            lines.append(line("Alarmas", f"{b['alarms']} activas", icon="▲ " if blink_on else "  "))
            if wide:   # the 5x7 font has neither the width nor a spare row for it
                lines.append(("  sensores fuera de límites", "", False))
        else:
            lines.append(line("Alarmas", "Ninguna"))
    return lines


UNIT_SHORT = {"uS/cm": "µS", "uc/cm": "µS", "us/cm": "µS", "mS/cm": "mS"}


def short_name(name: str) -> str:
    for suffix in ("Sensort", "Sensor"):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def short_unit(unit: str, name: str) -> str:
    if unit == "unit":
        return "pH" if "ph" in name.lower() else ""
    return UNIT_SHORT.get(unit, unit)


def sensors_from_config(config: dict) -> list[dict]:
    """Enabled devices' sensors in config order, with display labels."""
    out = []
    for d in config.get("devices", []):
        if d.get("enabled", True) is False:
            continue
        for s in d.get("sensors", []):
            out.append({"device": d["id"], "name": s["name"], "label": short_name(s["name"]),
                        "unit": short_unit(s.get("unit", ""), s["name"])})
    return out


def fmt_val(v, width: int) -> str:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        return "?"
    s = f"{v:.1f}"
    if len(s) > width:
        s = f"{v:.0f}"
    if len(s) > width:
        s = f"{v:.0e}"
    return s


def sensor_line(sensor: dict, reading: dict | None, font: Font, blink_on: bool = True) -> str:
    """name value unit flag: 12+8+2+6+2+1 columns (3x5) or 8+6+1+4+1+1 (5x7).
    Flag: ■ normal, ▲/▼ high/low alarm (blinks), ✕ read error, blank = no data yet."""
    nw, vw, uw, sp = font.sens
    if reading is None:
        val, flag = "--", " "
    elif reading.get("status") != "OK":
        val, flag = "----", "✕"
    else:
        val = fmt_val(reading.get("val"), vw)
        alarm = reading.get("alarm")
        flag = {"HIGH": "▲", "LOW": "▼"}.get(alarm, "■")
        if alarm in ("HIGH", "LOW") and not blink_on:
            flag = " "
    return pad_end(sensor["label"], nw) + pad_start(val, vw) + sp + pad_end(sensor["unit"], uw) + sp + flag


def reading_for(st: dict, sensor: dict) -> dict | None:
    return (st.get("readings") or {}).get(sensor["device"], {}).get(sensor["name"])


# ----------------------------------------------------------------------
# Scrolling and the screen sequence
# ----------------------------------------------------------------------

def scroll_max(n_sensors: int, font: Font) -> int:
    """How far (px) the list has to move to show its last row."""
    return max(0, n_sensors * font.row_h - (H - font.head_h))


def scroll_offset(elapsed: float, n_sensors: int, font: Font) -> int:
    """Wait SCROLL_PAUSE, move at SCROLL_SPEED px/s, stop at the end."""
    return round(min(scroll_max(n_sensors, font), max(0.0, (elapsed - SCROLL_PAUSE) * SCROLL_SPEED)))


def screen_seconds(kind: str, n_sensors: int, font: Font) -> float:
    """The sensor list stays up as long as its scroll needs (a list that
    fits doesn't scroll and gets the normal time)."""
    if kind == "logo":
        return LOGO_SECONDS
    if kind == "sensors" and scroll_max(n_sensors, font):
        return 2 * SCROLL_PAUSE + scroll_max(n_sensors, font) / SCROLL_SPEED
    return SCREEN_SECONDS


def next_screen(kind: str, k: int, repeats: int) -> tuple[str, int]:
    """logo -> system -> sensors #1 .. #repeats -> system -> ..."""
    if kind == "sensors" and k < repeats:
        return "sensors", k + 1
    if kind == "system":
        return "sensors", 1
    return "system", 0


# ----------------------------------------------------------------------
# Drawing the screens
# ----------------------------------------------------------------------

@dataclass
class View:
    """Everything a screen needs that isn't the framebuffer."""
    font: Font
    status: dict
    sensors: list
    gateway_id: str
    clock: str
    now: float              # epoch s
    ip: str | None = None
    logo: bytes | None = None   # W * (H - 4) pixels from oled_logo.png
    db: dict | None = None      # database_info()
    ssid: str | None = None     # wifi_ssid()
    client: str = ""            # config.json gateway.client


def blink_on(now: float) -> bool:
    return (now % 1.0) < 0.65


@functools.lru_cache(maxsize=4)
def boot_line_band(logo: bytes) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) of the logo's last block of inked rows - the
    "Venko -> Akvo" line that animates at boot. None when the logo has only
    one block (then a plain progress bar is drawn instead)."""
    rows = len(logo) // W
    bands, start = [], None
    for y in range(rows + 1):
        ink = y < rows and any(logo[y * W:(y + 1) * W])
        if ink and start is None:
            start = y
        elif not ink and start is not None:
            bands.append((start, y))
            start = None
    if len(bands) < 2:
        return None
    y0, y1 = bands[-1]
    xs = [x for y in range(y0, y1) for x in range(W) if logo[y * W + x]]
    return min(xs), y0, max(xs) + 1, y1


def boot_line_state(elapsed: float) -> tuple[float, bool]:
    """(how much of the boot line is written 0..1, whether it's lit) at
    `elapsed` s: BOOT_PASSES write passes (the last one ends at 100%), then
    BOOT_FLASHES off/on flashes, then steady."""
    write = BOOT_PASSES * BOOT_PASS_SECONDS
    if elapsed < write:
        return (elapsed % BOOT_PASS_SECONDS) / BOOT_PASS_SECONDS, True
    t = elapsed - write
    if t >= BOOT_FLASHES * BOOT_FLASH_SECONDS:
        return 1.0, True
    return 1.0, (t % BOOT_FLASH_SECONDS) >= BOOT_FLASH_SECONDS / 2


def draw_logo(fr: Frame, v: View, elapsed: float) -> None:
    progress = elapsed / LOGO_SECONDS
    if v.logo:
        fr.px[: len(v.logo)] = v.logo
        band = boot_line_band(v.logo)
        if band is None:
            fr.fill(14, H - 3, round((W - 28) * min(1.0, progress)), 3)
            return
        x0, y0, x1, y1 = band
        written, lit = boot_line_state(elapsed)
        cut = x0 + round((x1 - x0) * written) if lit else x0
        fr.fill(cut, y0, x1 - cut, y1 - y0, 0)   # erase the part not written yet
        # row 3: this site's gateway name, steady under the animated line
        f57 = FONTS["5x7"]
        name = fit(v.gateway_id, f57.cols)
        fr.text(fr.center_x(name, font=f57), y1 + 8, name, font=f57)
        return
    f57 = FONTS["5x7"]
    fr.text(fr.center_x("GHOTRIX", 2, f57), 4, "GHOTRIX", scale=2, font=f57)
    fr.fill(18, 22, W - 36, 1)
    fr.text(fr.center_x("AKVO Green"), 28, "AKVO Green")
    sub = fit(f"Gateway {v.gateway_id}", fr.font.cols)
    fr.text(fr.center_x(sub), 38, sub)
    fr.box(14, 50, W - 28, 7)   # boot bar
    fr.fill(16, 52, round((W - 32) * min(1.0, progress)), 3)


def draw_system(fr: Frame, v: View) -> None:
    fr.header("SISTEMA", v.clock)
    lines = system_lines(v.status, v.now, v.ip, wide=fr.font.cols >= 30, blink_on=blink_on(v.now), db=v.db,
                         ssid=v.ssid, client=v.client)
    for i, (left, right, fault) in enumerate(lines[: fr.font.rows]):
        fr.row(i + 1, left, right, fault)


def draw_sensors(fr: Frame, v: View, elapsed: float) -> None:
    f, n = fr.font, len(v.sensors)
    off = scroll_offset(elapsed, n, f)
    on = blink_on(v.now)
    for i, s in enumerate(v.sensors):
        y = f.head_h + i * f.row_h - off
        if y + f.row_h > f.head_h and y < H:
            fr.row_at(y, sensor_line(s, reading_for(v.status, s), f, on))
    if not n:
        fr.row(1, "  sin sensores en config.json")
    top = scroll_max(n, f)
    if top:  # scrollbar in the free columns at the right edge
        view = H - f.head_h
        used = sum(f.sens[:3]) + 2 * len(f.sens[3]) + 1
        x = max(1 + used * f.cw, W - 2)
        thumb = max(4, round(view * view / (n * f.row_h)))
        for y in range(f.head_h, H, 2):
            fr.fill(W - 1, y, 1, 1)
        fr.fill(x, f.head_h + round((view - thumb) * off / top), W - x, thumb)
    # header last: it covers the rows scrolled up past the top
    first, last = off // f.row_h + 1, min(n, (off + H - f.head_h) // f.row_h)
    fr.header(f"SENSORES {first}-{last}/{n}" if f.cols >= 30 and n else "SENSORES", v.clock)


def render(kind: str, elapsed: float, v: View) -> Frame:
    fr = Frame(v.font)
    if kind == "logo":
        draw_logo(fr, v, elapsed)
    elif kind == "system":
        draw_system(fr, v)
    else:
        draw_sensors(fr, v, elapsed)
    return fr


# ----------------------------------------------------------------------
# Runtime: config, IP, logo, the device and the loop
# ----------------------------------------------------------------------

def display_settings(config: dict) -> dict:
    d = config.get("display") or {}
    return {"enabled": bool(d.get("enabled")), "driver": d.get("driver", "ssd1309"),
            "address": int(d.get("address", DEFAULT_ADDRESS)), "font": d.get("font", "3x5"),
            "sensor_repeats": int(d.get("sensor_repeats", 2)),
            "column_offset": int(d.get("column_offset", SH1106_COLUMN_OFFSET))}


def local_ip() -> str | None:
    """The address of the interface with the default route. A UDP connect
    sends nothing; it only makes the kernel pick the route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def wifi_ssid() -> str | None:
    """The SSID the Pi's WiFi is joined to, from NetworkManager's cached scan
    (--rescan no: never triggers a scan). None when not on WiFi (a Pi on a
    cable only, WiFi off) or nmcli isn't there."""
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi", "list", "--rescan", "no"],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for ln in out.splitlines():
        if ln.startswith("yes:"):
            return ln[4:].replace("\\:", ":").replace("\\\\", "\\") or None   # nmcli -t escapes ':' and '\\'
    return None


def database_info(config: dict, path: Path = DEFAULT_DB) -> dict:
    """The DATABASE row: is the history on (system.csv database_enabled),
    how big history.db is (plus its -wal file; None = not created yet) and
    how full the disk holding it is (the real risk on an SD card)."""
    files = [f for f in (path, path.with_name(path.name + "-wal")) if f.exists()]
    try:
        du = shutil.disk_usage(path.parent if path.parent.exists() else BASE_DIR)
        pct = 100 * du.used / (du.used + du.free)   # as df and psutil count it (root's reserve left out)
    except OSError:
        pct = None
    return {"enabled": bool(config.get("database", {}).get("enabled")),
            "size": sum(f.stat().st_size for f in files) if files else None, "disk_pct": pct}


def load_logo(path: Path = LOGO_PATH) -> bytes | None:
    """oled_logo.png fitted into 128x60, 1 bit: light pixels are lit. Its
    last row of ink (if it has two) is the animated boot line."""
    if not path.is_file():
        return None
    from PIL import Image
    img = Image.open(path).convert("L")
    img.thumbnail((W, H - 4))
    canvas = Image.new("L", (W, H - 4))
    canvas.paste(img, ((W - img.width) // 2, (H - 4 - img.height) // 2))
    return bytes(1 if p > 127 else 0 for p in canvas.tobytes())


def open_device(driver: str, address: int, column_offset: int = SH1106_COLUMN_OFFSET):
    from luma.core.interface.serial import i2c
    from luma.oled import device as luma_device
    if driver != "sh1106":
        return getattr(luma_device, driver)(i2c(port=1, address=address))
    # SH1106 RAM is 132 columns wide; which 128 the panel shows varies by
    # board (display.column_offset). luma writes only those 128, so zero all
    # 132 first or the 2-4 columns it never writes keep power-on garbage.
    device = luma_device.sh1106(i2c(port=1, address=address), page_address_offset=column_offset)
    for page in range(8):
        device.command(0xB0 + page, 0x00, 0x10)
        device.data([0] * 132)
    return device


class Display:
    """The screen sequence over time. tick() draws the current frame."""

    def __init__(self, config: dict, status_path: Path, font: Font, repeats: int, db_path: Path = DEFAULT_DB):
        gw = config.get("gateway", {})
        self.config, self.db_path = config, db_path
        self.status_path = status_path
        self.poll_interval = int(gw.get("poll_interval") or 20)
        self.tz = ZoneInfo(gw.get("city") or "UTC")
        self.gateway_id = gw.get("gateway_id", "")
        self.client = gw.get("client", "")
        self.sensors = sensors_from_config(config)
        self.font, self.repeats = font, max(1, repeats)
        self.logo = load_logo()
        self.kind, self.k, self.started, self.rounds = "logo", 0, time.monotonic(), 0
        self.status, self.status_read, self.ip, self.ip_read, self.db, self.ssid = {}, -1e9, None, -1e9, None, None

    def tick(self) -> Frame:
        mono, now = time.monotonic(), time.time()
        if mono - self.status_read >= STATUS_EVERY:
            self.status, self.status_read = read_status(self.status_path, self.poll_interval), mono
        if mono - self.ip_read >= 10:
            self.ip, self.ip_read = local_ip(), mono
            self.db, self.ssid = database_info(self.config, self.db_path), wifi_ssid()
        if mono - self.started >= screen_seconds(self.kind, len(self.sensors), self.font):
            self.kind, self.k = next_screen(self.kind, self.k, self.repeats)
            self.started = mono
            if self.kind == "system":
                self.rounds += 1
        clock = datetime.fromtimestamp(now, self.tz).strftime("%H:%M:%S")
        view = View(self.font, self.status, self.sensors, self.gateway_id, clock, now, self.ip, self.logo, self.db,
                    self.ssid, self.client)
        return render(self.kind, mono - self.started, view)

    @property
    def shift(self) -> tuple[int, int]:
        return SHIFTS[self.rounds % len(SHIFTS)]

    @property
    def scrolling(self) -> bool:
        return self.kind == "sensors" and bool(scroll_max(len(self.sensors), self.font))


def run(display: Display, device) -> None:
    last = None
    while True:
        frame = display.tick()
        img = frame.image(display.shift)
        data = img.tobytes()
        if data != last:   # an unchanged frame costs no I2C traffic
            device.display(img)
            last = data
        time.sleep(0.1 if display.scrolling else 0.25)


def write_pngs(config: dict, status_path: Path, font: Font, out: Path, scale: int) -> list[Path]:
    """Every screen as a PNG (sensor list at the start, middle and end of
    its scroll), from the real status file if there is one."""
    from PIL import Image
    d = Display(config, status_path, font, 1)
    poll = d.poll_interval
    now = time.time()
    view = View(font, read_status(status_path, poll), d.sensors, d.gateway_id,
                datetime.fromtimestamp(now, d.tz).strftime("%H:%M:%S"), now, local_ip(), d.logo,
                database_info(config, d.db_path), wifi_ssid(), d.client)
    end = screen_seconds("sensors", len(d.sensors), font)
    shots = [("logo", "logo", BOOT_PASS_SECONDS * 2.5), ("logo_100", "logo", LOGO_SECONDS), ("sistema", "system", 0),
             ("sensores_inicio", "sensors", 0), ("sensores_medio", "sensors", end / 2),
             ("sensores_fin", "sensors", end)]
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, kind, elapsed in shots:
        img = render(kind, elapsed, view).image()
        if scale > 1:
            img = img.resize((W * scale, H * scale), Image.NEAREST)
        path = out / f"oled_{name}.png"
        img.save(path)
        paths.append(path)
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--status", type=Path, default=DEFAULT_STATUS, help="gateway status file")
    ap.add_argument("--png", type=Path, metavar="DIR", help="write each screen as PNG to DIR and exit")
    ap.add_argument("--scale", type=int, default=4, help="PNG pixel size (with --png)")
    ap.add_argument("--driver", choices=DRIVERS, help="overrides display.driver")
    ap.add_argument("--address", type=lambda s: int(s, 0), help="overrides display.address, e.g. 0x3D")
    ap.add_argument("--font", choices=sorted(FONTS), help="overrides display.font")
    ap.add_argument("--column-offset", type=int, choices=range(5), help="overrides display.column_offset (sh1106)")
    ap.add_argument("--force", action="store_true", help="run even if display.enabled is off (testing)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    config = json.loads(args.config.read_text())
    ds = display_settings(config)
    font = FONTS[args.font or ds["font"]]

    if args.png:
        for p in write_pngs(config, args.status, font, args.png, args.scale):
            print(p)
        return 0
    if not (ds["enabled"] or args.force):
        # Exit 0 so systemd (Restart=on-failure) leaves it stopped.
        logger.info("OLED display is off (system.csv display_enabled) - exiting")
        return 0

    driver, address = args.driver or ds["driver"], args.address or ds["address"]
    offset = ds["column_offset"] if args.column_offset is None else args.column_offset
    try:
        device = open_device(driver, address, offset)
    except Exception as e:
        # Missing luma.oled, I2C off (no /dev/i2c-1), nothing at the address...
        # Exit 1: systemd retries (RestartSec), the gateway is unaffected.
        logger.error(f"Can't open the {driver} display at I2C 0x{address:02X}: {e!r}")
        return 1
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    if driver == "sh1106":
        driver = f"sh1106 (column offset {offset})"
    logger.info(f"OLED display: {driver} at I2C 0x{address:02X}, font {font.cols}x{font.rows + 1}, "
                f"status {args.status}")
    try:
        run(Display(config, args.status, font, ds["sensor_repeats"]), device)
    except KeyboardInterrupt:
        pass
    finally:
        device.cleanup()   # blank the panel rather than leave a frozen frame
    return 0


if __name__ == "__main__":
    sys.exit(main())
