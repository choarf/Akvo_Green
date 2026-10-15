#!/usr/bin/env python3
"""
OLED setup check: draws a few test screens so you can confirm the wiring,
the I2C address and the controller type (driver) of a 128x64 I2C OLED.

Run on the Pi, with the akvo-oled service stopped (two programs writing to
the display at once garble it):

    sudo systemctl stop akvo-oled
    ~/Akvo_Green/.venv/bin/python3 ~/Akvo_Green/tools/oled_test.py              # tries every driver
    ~/Akvo_Green/.venv/bin/python3 ~/Akvo_Green/tools/oled_test.py --driver sh1106
    sudo systemctl start akvo-oled

SH1106 boards also differ in which 128 of the chip's 132 columns the panel
shows: stray lines or garbage at the left/right edge mean the column offset
is wrong. --offsets sweeps them (default 2 is luma's):

    ~/Akvo_Green/.venv/bin/python3 ~/Akvo_Green/tools/oled_test.py --driver sh1106 --offsets 0,1,2,3,4

Pick the driver whose screens look clean (frame touches all 4 edges, X
corner to corner, text sharp), put it in system.csv's display_driver,
rebuild config.json and push. Boards sold as "SSD1309" are often SH1106.
"""
import argparse
import subprocess
import sys
import time

DRIVERS = ("ssd1309", "ssd1306", "sh1106")


def service_active(name: str) -> bool:
    try:
        r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
        return r.stdout.strip() == "active"
    except OSError:
        return False


def open_device(driver: str, port: int, address: int, offset=None):
    from luma.core.interface.serial import i2c
    from luma.oled import device as luma_device
    kw = {} if offset is None else {"page_address_offset": offset}
    dev = getattr(luma_device, driver)(i2c(port=port, address=address), width=128, height=64, **kw)
    if driver == "sh1106":
        wipe_sh1106_ram(dev)
    return dev


def wipe_sh1106_ram(dev):
    """Zero all 132 columns of every page: the driver only writes 128 of them,
    so the rest would keep power-on garbage that may be visible at an edge."""
    for page in range(8):
        dev.command(0xB0 + page, 0x00, 0x10)
        dev.data([0] * 132)


def screens(dev, driver: str, hold: float):
    from luma.core.render import canvas
    w, h = dev.width, dev.height
    steps = [
        ("all pixels ON (whole screen lit, no dark bands)",
         lambda c: c.rectangle((0, 0, w - 1, h - 1), fill="white")),
        ("all pixels OFF (screen completely dark)", lambda c: None),
        ("frame on all 4 edges + X corner to corner",
         lambda c: (c.rectangle((0, 0, w - 1, h - 1), outline="white"),
                    c.line((0, 0, w - 1, h - 1), fill="white"),
                    c.line((0, h - 1, w - 1, 0), fill="white"))),
        ("8x8 checkerboard",
         lambda c: [c.rectangle((x, y, x + 7, y + 7), fill="white")
                    for y in range(0, h, 8) for x in range(0, w, 8) if (x // 8 + y // 8) % 2 == 0]),
        ("text: driver name, 'TOP LEFT', 'BOTTOM RIGHT'",
         lambda c: (c.rectangle((0, 0, w - 1, h - 1), outline="white"),
                    c.text((3, 2), "TOP LEFT", fill="white"),
                    c.text((w // 2 - 3 * len(driver), h // 2 - 4), driver.upper(), fill="white"),
                    c.text((w - 75, h - 11), "BOTTOM RIGHT", fill="white"))),
    ]
    for i, (what, draw) in enumerate(steps, 1):
        print(f"  {i}/{len(steps)} should show: {what}", flush=True)
        with canvas(dev) as c:
            draw(c)
        time.sleep(hold)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OLED setup check (128x64 I2C)")
    ap.add_argument("--driver", choices=DRIVERS + ("all",), default="all",
                    help="controller to try (default: all, one after the other)")
    ap.add_argument("--address", default="0x3C", help="I2C address (default 0x3C; some boards 0x3D)")
    ap.add_argument("--port", type=int, default=1, help="I2C bus (default 1 = /dev/i2c-1)")
    ap.add_argument("--hold", type=float, default=4.0, help="seconds per screen (default 4)")
    ap.add_argument("--offsets", help="sh1106 only: comma-separated column offsets to sweep, e.g. 0,1,2,3,4")
    args = ap.parse_args(argv)
    address = int(args.address, 0)

    try:
        import luma.oled  # noqa: F401
    except ImportError:
        print("luma.oled is not installed: run this with ~/Akvo_Green/.venv/bin/python3", file=sys.stderr)
        return 2
    if service_active("akvo-oled"):
        print("akvo-oled is running and will overwrite the test - stop it first:\n"
              "  sudo systemctl stop akvo-oled", file=sys.stderr)
        return 2

    if args.offsets:
        if args.driver != "sh1106":
            print("--offsets needs --driver sh1106", file=sys.stderr)
            return 2
        runs = [("sh1106", int(o)) for o in args.offsets.split(",")]
    else:
        runs = [(d, None) for d in (DRIVERS if args.driver == "all" else (args.driver,))]
    dev = None
    for drv, offset in runs:
        label = drv if offset is None else f"{drv} off{offset}"
        print(f"\n== {label} at I2C 0x{address:02X} on bus {args.port}", flush=True)
        try:
            dev = open_device(drv, args.port, address, offset)
        except Exception as e:  # no device at that address, I2C off, permissions...
            print(f"  can't open the display: {e!r}\n"
                  f"  check: I2C enabled (sudo raspi-config nonint do_i2c 0), wiring, "
                  f"'i2cdetect -y {args.port}' shows {address:02x}", file=sys.stderr)
            return 1
        screens(dev, label, args.hold)

    # leave the last screen up so it can still be checked after the program ends
    dev.persist = True
    print("\nThe driver whose screens all looked clean is the one to use:"
          "\n  system.csv display_driver=<driver>, rebuild config.json, push, restart akvo-oled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
