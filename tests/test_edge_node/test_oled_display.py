"""
oled_display.py: screen content, scrolling, the screen sequence and the
rendered frames - all without the hardware (luma.oled isn't needed).
"""

import json
import time
from datetime import UTC, datetime, timedelta

import pytest

import oled_display as od

F35, F57 = od.FONTS["3x5"], od.FONTS["5x7"]
NOW = 1_800_000_000.0

CONFIG = {
    "gateway": {"gateway_id": "AKVO_t", "city": "America/Mexico_City", "poll_interval": 20},
    "devices": [
        {"id": "DEV_1", "enabled": True, "sensors": [{"name": "Pt100Sensor", "unit": "°C", "min": 0, "max": 55}]},
        {"id": "DEV_2", "enabled": True, "sensors": [{"name": "PhSensor", "unit": "unit", "min": 0, "max": 14},
                                                     {"name": "TDSCondSensor", "unit": "uS/cm", "min": 10, "max": 5000}]},
        {"id": "DEV_3", "enabled": False, "sensors": [{"name": "Off", "unit": "x", "min": 0, "max": 1}]},
    ],
}


def status(**over):
    st = {"available": True, "age_s": 16, "stale": False,
          "network": {"connected": True, "check": "8.8.8.8:53"},
          "mqtt": {"connected": True, "since": NOW - 3600, "last_publish": NOW - 16},
          "modbus": {"connected": True, "port": "/dev/ttySC0", "simulated": False,
                     "sensors": 3, "read_errors": 0, "alarms": 0},
          "readings": {"DEV_1": {"Pt100Sensor": {"val": 24.0, "status": "OK", "alarm": "NORMAL"}}}}
    st.update(over)
    return st


def lefts(lines):
    return [left for left, _, _ in lines]


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("font", [F35, F57])
def test_every_character_the_screens_use_has_a_glyph(font):
    text = ("SISTEMA SENSORES Gateway Activo Inactivo Sin estado Red AWS Database Desactivada sin archivo "
            "MB GB sens días Modbus Simulado Pto cerrado No responde Errores Alarmas activas Ninguna "
            "Cliente SSID Sin WiFi sensores fuera de límites 0123456789 .:/-%°µ> ●•■▲▼✕⚠ GHOTRIX AKVO Green")
    question = font.glyphs["?"]
    assert [ch for ch in text if font.glyph(ch) is question] == []


def test_3x5_falls_back_to_plain_capitals():
    assert F35.glyph("á") == F35.glyph("A") and F35.glyph("ñ") == F35.glyph("N") and F35.glyph("g") == F35.glyph("G")


def test_font_grids():
    assert (F35.cols, F35.rows) == (32, 9) and (F57.cols, F57.rows) == (21, 7)


# ---------------------------------------------------------------------------
# Sistema rows (same states as web/app.js connectionRows)
# ---------------------------------------------------------------------------

DB = {"enabled": True, "size": 59_000_000, "disk_pct": 78.4}


def test_system_lines_all_ok():
    lines = od.system_lines(status(), NOW, "192.168.1.5", wide=True, db=DB, ssid="Vocho")
    assert lefts(lines) == ["Gateway Activo > 16 s", "SSID    Vocho", "Red     Activo > 192.168.1.5",
                            "AWS     Activo > 16 s", "Database 59 MB - 78%", "Modbus  Activo > 3 sens",
                            "Alarmas Ninguna"]
    assert not any(fault for _, _, fault in lines)


def test_system_lines_no_db_row_without_db_info():
    assert not [ln for ln in lefts(od.system_lines(status(), NOW, None, wide=True)) if ln.startswith("Database")]


def test_system_lines_narrow_font_short_labels_and_right_detail():
    lines = od.system_lines(status(), NOW, "192.168.1.5", wide=False, db=DB, ssid="Vocho")
    assert lines[0] == ("GW  Activo", "16s", False)
    assert lines[1] == ("Red Activo", "Vocho", False) and lines[2][0] == "  192.168.1.5"
    assert od.system_lines(status(), NOW, "192.168.1.5", wide=False)[1][1] == "cable"
    assert ("DB  59 MB", "78%", False) in lines


def test_system_lines_missing_status_file():
    lines = od.system_lines({"available": False}, NOW, None, wide=True)
    assert lines[0] == ("Gateway ✕Sin estado", "", True)


def test_system_lines_faults_are_inverted_rows_with_downtime():
    st = status(stale=True, age_s=312, network={"connected": False, "since": NOW - 3725, "check": "8.8.8.8:53"},
                mqtt={"connected": False, "since": NOW - 120})
    lines = od.system_lines(st, NOW, None, wide=True)
    assert lines[0] == ("Gateway ✕Inactivo > 00:05:12", "", True)
    assert lines[1] == ("SSID    Sin WiFi", "", False)    # no WiFi is not a fault: the Pi may be on a cable
    assert lines[2] == ("Red     ✕Inactivo > 01:02:05", "", True)
    assert lines[3] == ("AWS     ✕Inactivo > 00:02:00", "", True)


def test_system_lines_down_without_since_has_no_duration():
    st = status(network={"connected": False, "check": "8.8.8.8:53"})
    assert od.system_lines(st, NOW, None, wide=True)[2] == ("Red     ✕Inactivo", "", True)


def test_system_lines_aws_without_a_send_yet():
    st = status(mqtt={"connected": True, "since": NOW - 5})
    assert od.system_lines(st, NOW, None, wide=True)[3][0] == "AWS     Activo"


@pytest.mark.parametrize("db, left, fault", [
    ({"enabled": False}, "Database Desactivada", False),
    ({"enabled": True, "size": 3_400_000, "disk_pct": 41.0}, "Database 3.4 MB - 41%", False),
    ({"enabled": True, "size": None, "disk_pct": 41.0}, "Database sin archivo - 41%", False),
    ({"enabled": True, "size": 1_250_000_000, "disk_pct": 93.6}, "Database ✕1.2 GB - 94%", True),
])
def test_system_lines_database(db, left, fault):
    row = [ln for ln in od.system_lines(status(), NOW, None, wide=True, db=db) if ln[0].startswith("Database")]
    assert row == [(left, "", fault)]


@pytest.mark.parametrize("modbus, left, fault", [
    ({"simulated": True, "connected": True}, "Modbus  ⚠Simulado", False),
    ({"connected": False}, "Modbus  ✕Pto cerrado", True),
    ({"connected": True, "sensors": 3, "read_errors": 3}, "Modbus  ✕No responde", True),
    ({"connected": True, "sensors": 3, "read_errors": 1}, "Modbus  ⚠Errores > 1/3", False),
])
def test_system_lines_modbus_states(modbus, left, fault):
    row = [ln for ln in od.system_lines(status(modbus=modbus), NOW, None, wide=True) if ln[0].startswith("Modbus")]
    assert row[0] == (left, "", fault)


def test_system_lines_alarms_add_a_detail_row_and_blink():
    st = status(modbus={"connected": True, "sensors": 3, "read_errors": 0, "alarms": 2})
    on = lefts(od.system_lines(st, NOW, None, wide=True, blink_on=True))
    off = lefts(od.system_lines(st, NOW, None, wide=True, blink_on=False))
    assert on[-2:] == ["Alarmas ▲ 2 activas", "  sensores fuera de límites"]
    assert off[-2] == "Alarmas   2 activas"


@pytest.mark.parametrize("font, wide", [(F35, True), (F57, False)])
def test_every_system_line_fits_the_width(font, wide):
    st = status(stale=True, age_s=360000, network={"connected": False, "since": NOW - 360000, "check": "8.8.8.8:53"},
                mqtt={"connected": False, "since": NOW - 360000},
                modbus={"connected": True, "sensors": 15, "read_errors": 4, "alarms": 12})
    ok = status(modbus={"connected": True, "sensors": 15, "read_errors": 0, "alarms": 0})
    big = {"enabled": True, "size": 999_000_000, "disk_pct": 100.0}
    for s in (st, ok):
        for left, right, _ in od.system_lines(s, NOW, "192.168.100.200", wide=wide, db=big, ssid="Planta"):
            assert len(left) + (len(right) + 1 if right else 0) <= font.cols, left
    alarms = status(modbus={"connected": True, "sensors": 15, "read_errors": 0, "alarms": 3})
    for s in (st, alarms):
        assert len(od.system_lines(s, NOW, "192.168.100.200", wide=wide, db=big, ssid="Planta")) <= font.rows


def test_system_lines_client_row_first_when_set():
    lines = od.system_lines(status(), NOW, "192.168.1.5", wide=True, db=DB, ssid="Vocho", client="Ghotrix")
    assert lines[0] == ("Cliente Ghotrix", "", False) and lines[1][0].startswith("Gateway")
    assert lefts(od.system_lines(status(), NOW, None, wide=False, client="Ghotrix"))[0].startswith("GW")


def test_system_screen_with_client_and_alarms_fills_but_fits_9_rows():
    st = status(modbus={"connected": True, "sensors": 15, "read_errors": 0, "alarms": 3})
    lines = od.system_lines(st, NOW, "192.168.100.200", wide=True, db=DB, ssid="Vocho", client="x" * 24)
    assert len(lines) == F35.rows and all(len(left) <= F35.cols for left, _, _ in lines)


def test_wifi_ssid_reads_the_active_network(monkeypatch):
    class Done:
        stdout = "no:Oficina\nyes:Planta\\: 2\nno:Vecino\n"
    monkeypatch.setattr(od.subprocess, "run", lambda *a, **k: Done())
    assert od.wifi_ssid() == "Planta: 2"
    Done.stdout = "no:Oficina\n"
    assert od.wifi_ssid() is None


def test_wifi_ssid_without_nmcli(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("nmcli")
    monkeypatch.setattr(od.subprocess, "run", missing)
    assert od.wifi_ssid() is None


def test_hms_and_size():
    assert od.hms(0) == "00:00:00" and od.hms(3725) == "01:02:05" and od.hms(100 * 3600) == "100:00:00"
    assert od.size_mb(950_000) == "0.9 MB" and od.size_mb(59_400_000) == "59 MB"


def test_database_info(tmp_path):
    db = tmp_path / "history.db"
    assert od.database_info({}, db)["size"] is None
    db.write_bytes(b"x" * 100)
    (tmp_path / "history.db-wal").write_bytes(b"x" * 20)
    info = od.database_info({"database": {"enabled": True}}, db)
    assert info["enabled"] and info["size"] == 120 and 0 < info["disk_pct"] <= 100


# ---------------------------------------------------------------------------
# Sensor rows
# ---------------------------------------------------------------------------

def test_sensors_from_config_skips_disabled_devices_and_shortens_labels():
    sensors = od.sensors_from_config(CONFIG)
    assert [(s["label"], s["unit"]) for s in sensors] == [("Pt100", "°C"), ("Ph", "pH"), ("TDSCond", "µS")]


@pytest.mark.parametrize("reading, val, flag", [
    ({"val": 24.0, "status": "OK", "alarm": "NORMAL"}, "24.0", "■"),
    ({"val": 57.25, "status": "OK", "alarm": "HIGH"}, "57.2", "▲"),
    ({"val": -12.0, "status": "OK", "alarm": "LOW"}, "-12.0", "▼"),
    ({"status": "EXCEPTION"}, "----", "✕"),
    (None, "--", " "),
])
def test_sensor_line_value_and_flag(reading, val, flag):
    s = {"label": "Pt100", "unit": "°C"}
    line = od.sensor_line(s, reading, F35)
    assert len(line) == 31 and line.split()[1] == val.strip() and line[-1] == flag


def test_sensor_line_alarm_flag_blinks_off():
    line = od.sensor_line({"label": "X", "unit": ""}, {"val": 1, "status": "OK", "alarm": "HIGH"}, F35, blink_on=False)
    assert line[-1] == " "


def test_fmt_val_fits_the_column():
    assert od.fmt_val(-1500.0, 6) == "-1500" and od.fmt_val(1650.04, 6) == "1650.0"
    assert len(od.fmt_val(123456789.0, 6)) <= 6 and od.fmt_val(None, 6) == "?"


def test_5x7_sensor_line_is_21_columns():
    assert len(od.sensor_line({"label": "TempCondSensor", "unit": "mg/L"}, None, F57)) == 21


# ---------------------------------------------------------------------------
# Scrolling and the screen sequence
# ---------------------------------------------------------------------------

def test_scroll_waits_moves_and_stops():
    # 15 rows of 6 px = 90 px under a 57 px view -> 33 px to scroll
    assert od.scroll_max(15, F35) == 33
    assert od.scroll_offset(1.0, 15, F35) == 0
    assert od.scroll_offset(1.5 + 2.0, 15, F35) == 12
    assert od.scroll_offset(100, 15, F35) == 33
    assert od.screen_seconds("sensors", 15, F35) == pytest.approx(3 + 33 / 6)


def test_a_list_that_fits_does_not_scroll():
    assert od.scroll_max(9, F35) == 0 and od.screen_seconds("sensors", 9, F35) == od.SCREEN_SECONDS


def test_sequence_logo_then_system_and_two_sensor_screens():
    seq, kind, k = [], "logo", 0
    for _ in range(7):
        kind, k = od.next_screen(kind, k, 2)
        seq.append(kind)
    assert seq == ["system", "sensors", "sensors", "system", "sensors", "sensors", "system"]


def test_sequence_logo_every_n_rounds():
    seq, kind, k, rounds = [], "logo", 0, 0
    for _ in range(11):
        kind, k = od.next_screen(kind, k, 1, rounds, logo_every=2)
        rounds += kind == "system"
        seq.append(kind)
    assert seq == ["system", "sensors", "system", "sensors", "logo",
                   "system", "sensors", "system", "sensors", "logo", "system"]


def test_screen_time_and_scroll_speed_are_settable():
    assert od.screen_seconds("system", 15, F35, screen_s=8) == 8
    assert od.screen_seconds("sensors", 15, F35, speed=11) == pytest.approx(3 + 33 / 11)
    assert od.scroll_offset(1.5 + 2.0, 15, F35, speed=3) == 6


def test_display_settings_defaults_and_overrides():
    ds = od.display_settings({"display": {}})
    assert (ds["screen_seconds"], ds["scroll_speed"], ds["logo_every"], ds["contrast"], ds["burn_in_shift"]) == \
        (od.SCREEN_SECONDS, od.SCROLL_SPEED, 0, None, True)
    ds = od.display_settings({"display": {"screen_seconds": 4, "logo_every": 2, "contrast": 50, "burn_in_shift": False}})
    assert (ds["screen_seconds"], ds["logo_every"], ds["contrast"], ds["burn_in_shift"]) == (4.0, 2, 50, False)


def test_sensor_pages_spread_evenly_like_the_simulator():
    assert od.sensor_pages(15, F35) == (2, 8)          # 9 rows fit -> 2 pages of 8 + 7
    assert od.sensor_pages(15, F35, per_page=5) == (3, 5)
    assert od.sensor_pages(15, F35, per_page=4) == (4, 4)
    assert od.sensor_pages(3, F35) == (1, 3)
    assert od.sensor_pages(0, F35) == (1, 0)


def test_pages_mode_steps_through_every_page_each_pass(monkeypatch, tmp_path):
    clock = {"t": 0.0}
    monkeypatch.setattr(od.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(od, "local_ip", lambda: "10.0.0.2")
    cfg = {**CONFIG, "display": {"sensor_list": "pages", "per_page": 1, "screen_seconds": 1}}
    d = od.Display(cfg, tmp_path / "missing.json", F35, 2)
    n = len(d.sensors)
    assert (d.pages, d.per) == (n, 1) and not d.scrolling
    headers = []
    for t in range(0, 60):
        clock["t"] = t + 0.5
        d.tick()
        if d.kind == "sensors":
            headers.append(d.page_of(d.k)[0])
    # Sistema, then every page twice (sensor_repeats 2), then Sistema again
    assert headers[:2 * n] == list(range(n)) * 2


def test_pages_mode_draws_one_page_with_its_header():
    v = view()
    v.page = (1, 2, 2)
    fr = od.render("sensors", 0, v)
    assert lit(fr, 0, 0, od.W, F35.head_h) > 0
    v.page = None
    assert od.render("sensors", 0, v).px != fr.px


def test_contrast_percent_maps_to_the_register():
    assert (od.contrast_level(100), od.contrast_level(50), od.contrast_level(1)) == (255, 128, 3)


def test_burn_in_shift_can_be_turned_off(tmp_path):
    d = od.Display({**CONFIG, "display": {"burn_in_shift": False}}, tmp_path / "missing.json", F35, 2)
    d.rounds = 1
    assert d.shift == (0, 0)
    d = od.Display(CONFIG, tmp_path / "missing.json", F35, 2)
    d.rounds = 1
    assert d.shift == od.SHIFTS[1]


def test_display_tick_follows_the_clock(monkeypatch, tmp_path):
    clock = {"t": 0.0}
    monkeypatch.setattr(od.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(od, "local_ip", lambda: "10.0.0.2")
    d = od.Display(CONFIG, tmp_path / "missing.json", F35, 2)
    seen = []
    for t in range(0, 30):
        clock["t"] = float(t)
        d.tick()
        seen.append(d.kind)
    assert seen[0] == "logo" and seen[5] == "logo" and seen[6] == "system" and seen[11] == "sensors"
    assert "logo" not in seen[6:]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def view(st=None, font=F35):
    return od.View(font, st or status(), od.sensors_from_config(CONFIG), "AKVO_t", "17:42:10", NOW, "10.0.0.2")


def lit(frame, x0, y0, x1, y1):
    return sum(frame.px[y * od.W + x] for y in range(y0, y1) for x in range(x0, x1))


def test_header_is_a_lit_bar():
    fr = od.render("system", 0, view())
    bar = lit(fr, 0, 0, od.W, F35.head_h)
    assert bar > od.W * F35.head_h * 0.7   # mostly lit, dark text cut out of it


def test_fault_row_is_inverted():
    ok = od.render("system", 0, view())
    bad = od.render("system", 0, view(status(stale=True)))
    y = F35.head_h   # first text row: Gateway
    assert lit(bad, 0, y, od.W, y + F35.row_h) > 2 * lit(ok, 0, y, od.W, y + F35.row_h)


def test_text_logo_boot_bar_fills_with_progress():
    early = od.render("logo", 0.5, view())
    late = od.render("logo", 4.5, view())
    assert lit(late, 16, 52, od.W - 16, 55) > lit(early, 16, 52, od.W - 16, 55)


def test_boot_line_writes_three_times_then_flashes_three_times():
    states = [od.boot_line_state(t) for t in (0.6, 1.3, 3.5, 3.7, 3.95, 4.2, 4.45, 5.0, 5.1)]
    assert states[0] == (pytest.approx(0.5), True)          # pass 1, half written
    assert states[1][0] == pytest.approx(0.1 / 1.2)         # pass 2 starts over
    assert states[2][0] > 0.9                               # pass 3 nearly at 100%
    assert [lit for _, lit in states[3:]] == [False, True, False, True, True, True]
    assert all(written == 1.0 for written, _ in states[3:])


def test_boot_line_band_is_the_logo_s_last_row_of_ink():
    logo = od.load_logo(od.LOGO_PATH)
    x0, y0, x1, y1 = od.boot_line_band(logo)
    assert y0 > 25 and y1 <= 60 and x1 - x0 > 60            # the "Venko -> Akvo" line
    assert od.boot_line_band(bytes(od.W * 60)) is None


def test_logo_line_is_drawn_up_to_the_written_part():
    v = view()
    v.logo = od.load_logo(od.LOGO_PATH)
    x0, y0, x1, y1 = od.boot_line_band(v.logo)
    half = od.render("logo", 0.6, v)
    full = od.render("logo", od.LOGO_SECONDS, v)
    off = od.render("logo", 3.7, v)
    mid = (x0 + x1) // 2
    assert lit(half, x0, y0, mid - 2, y1) > 0 and lit(half, mid + 2, y0, x1, y1) == 0
    assert lit(full, mid + 2, y0, x1, y1) > 0 and lit(off, x0, y0, x1, y1) == 0
    assert lit(off, 0, 0, od.W, y0) == lit(full, 0, 0, od.W, y0)   # GHOTRIX never changes


def test_logo_third_row_is_the_gateway_name():
    v = view()
    v.logo = od.load_logo(od.LOGO_PATH)
    _, _, _, y1 = od.boot_line_band(v.logo)
    rows = lambda fr: lit(fr, 0, y1 + 8, od.W, y1 + 16)
    named = od.render("logo", 0.6, v)
    v.gateway_id = ""
    unnamed = od.render("logo", 0.6, v)
    assert rows(named) > 0 and rows(unnamed) == 0
    assert y1 + 8 + 7 <= od.H                                      # fits on the screen


def test_shifted_moves_pixels_right_and_down():
    fr = od.Frame(F35)
    fr.fill(0, 0, 1, 1)
    out = fr.shifted(1, 1)
    assert out[1 * od.W + 1] == 1 and out[0] == 0 and sum(out) == 1


def test_image_is_1_bit_128x64():
    img = od.render("sensors", 0, view()).image()
    assert img.mode == "1" and img.size == (od.W, od.H)


# ---------------------------------------------------------------------------
# main(): PNG previews, off by config, and a missing display
# ---------------------------------------------------------------------------

def write_files(tmp_path, display=None):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({**CONFIG, **({"display": display} if display is not None else {})}))
    st = tmp_path / "status.json"
    ts = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    st.write_text(json.dumps({**status(), "ts": ts}))
    return cfg, st


def test_main_png_writes_every_screen(tmp_path):
    cfg, st = write_files(tmp_path)
    assert od.main(["--config", str(cfg), "--status", str(st), "--png", str(tmp_path / "png"), "--scale", "2"]) == 0
    names = sorted(p.name for p in (tmp_path / "png").iterdir())
    assert names == ["oled_logo.png", "oled_logo_100.png", "oled_sensores_fin.png", "oled_sensores_inicio.png",
                     "oled_sensores_medio.png", "oled_sistema.png"]


def test_main_exits_quietly_when_disabled(tmp_path):
    cfg, st = write_files(tmp_path, {"enabled": False})
    assert od.main(["--config", str(cfg), "--status", str(st)]) == 0


def test_main_fails_when_the_display_cant_be_opened(tmp_path, monkeypatch):
    cfg, st = write_files(tmp_path, {"enabled": True})

    def no_i2c(driver, address, column_offset):
        raise FileNotFoundError("/dev/i2c-1")

    monkeypatch.setattr(od, "open_device", no_i2c)
    assert od.main(["--config", str(cfg), "--status", str(st)]) == 1


def test_main_passes_the_column_offset(tmp_path, monkeypatch):
    cfg, st = write_files(tmp_path, {"enabled": True, "driver": "sh1106", "column_offset": 3})
    seen = {}

    def fake_open(driver, address, column_offset):
        seen.update(driver=driver, offset=column_offset)
        raise FileNotFoundError("/dev/i2c-1")   # stop before the display loop

    monkeypatch.setattr(od, "open_device", fake_open)
    assert od.main(["--config", str(cfg), "--status", str(st)]) == 1
    assert seen == {"driver": "sh1106", "offset": 3}
    assert od.main(["--config", str(cfg), "--status", str(st), "--column-offset", "0"]) == 1
    assert seen["offset"] == 0


def test_display_settings_column_offset_defaults_to_luma_s():
    assert od.display_settings({"display": {"driver": "sh1106"}})["column_offset"] == 2


def test_run_sends_only_changed_frames(monkeypatch, tmp_path):
    class Device:
        def __init__(self):
            self.frames = 0

        def display(self, img):
            self.frames += 1

    class Stop(Exception):
        pass

    d = od.Display(CONFIG, tmp_path / "missing.json", F35, 2)
    monkeypatch.setattr(d, "tick", lambda: od.Frame(F35))   # always the same (blank) frame
    calls = {"n": 0}

    def sleep(_):
        calls["n"] += 1
        if calls["n"] == 5:
            raise Stop

    monkeypatch.setattr(od.time, "sleep", sleep)
    dev = Device()
    with pytest.raises(Stop):
        od.run(d, dev)
    assert dev.frames == 1
