# Reviving a no-name Chinese tablet under Linux

**From a "brick" with a broken touchscreen to a snappy Linux tablet: touch, auto-rotation, gestures, right-click.**

This is the story of how a no-name Chinese 11.6" convertible tablet
(board `HJC-BI-11.6-S8`, Silead MSSL1680 touch controller) — whose
touchscreen was completely dead under Windows and mirrored / "folded" under
Linux — became a fully working touch device on Linux Mint.

The real value of this repo is **not a ready-made recipe for one specific
board** (there are maybe a handful of those owners in the world). It's the
**method**: how to diagnose your own touchscreen, understand what's actually
broken, and pick the right fix. If you have a different Silead-based tablet,
start with [docs/diagnostics.md](docs/diagnostics.md).

---

## What works in the end

- **Touchscreen** — accurate response with both finger and stylus in every
  orientation (a nonlinearly "folded" X axis is corrected by a custom
  coordinate handler).
- **Screen auto-rotation** via the accelerometer, with the touch coordinates
  rotating together with the screen and the keyboard disabled in tablet mode.
- **Right-click on long press** — otherwise there's no way to reach the context
  menu on a button-less tablet.
- **Multi-touch gestures** (swipe, zoom) via touchégg.
- **Kernel pinning** to a fast, stable version — on weak hardware a newer
  kernel is *not* better (see below).

## Requirements

- Linux Mint 22.3 (Cinnamon) or another Ubuntu 24.04-based distribution.
  The general approach also applies to Debian/MX — see notes in the text.
- A Silead touch controller (check: `dmesg | grep -i silead`).
- Packages:

```bash
sudo apt install python3-evdev iio-sensor-proxy touchegg
```

(tested versions: `python3-evdev 1.7.0`, `iio-sensor-proxy 3.5`, `touchegg 2.0.16`)

## Repository layout

```
firmware/mssl1680.fw                        firmware that worked for this controller
scripts/silead-unfold.py                    coordinate handler (unfolds the X axis)
scripts/silead-longpress.py                 long press -> right click
scripts/linux-tablet-autorotate             screen + touch auto-rotation
systemd/silead-unfold.service               autostart for the handler (system)
systemd/silead-longpress.service            autostart for right-click (system)
autostart/linux-tablet-autorotate.desktop   autostart for rotation (user session)
udev/99-silead-ignore.rules                 hides the "raw" sensor from libinput
```

## Important warning about the firmware

The file `firmware/mssl1680.fw` (38544 bytes, md5 `86e979b61f02ebad6b20ac66a7708c29`)
matched **this specific** touchscreen. On a different tablet it will most
likely produce wrong geometry. Firmware doesn't physically "brick" the sensor,
but **do not flash someone else's firmware without reading**
[docs/diagnostics.md](docs/diagnostics.md) first — it explains how to tell
whether a given firmware is right for you, and where to look for yours
(the `onitake/gsl-firmware` repository, or extraction from the stock
Windows driver).

## Installation (for the same or a compatible board)

> If you're not sure the hardware matches — do the diagnostics first.

```bash
# 1. Controller firmware
sudo cp firmware/mssl1680.fw /lib/firmware/silead/mssl1680.fw

# 2. Scripts
sudo cp scripts/silead-unfold.py      /usr/local/bin/
sudo cp scripts/silead-longpress.py   /usr/local/bin/
sudo cp scripts/linux-tablet-autorotate /usr/local/bin/
sudo chmod +x /usr/local/bin/silead-unfold.py \
              /usr/local/bin/silead-longpress.py \
              /usr/local/bin/linux-tablet-autorotate

# 3. udev rule (hides the raw sensor so it doesn't double up)
sudo cp udev/99-silead-ignore.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger

# 4. System services (touch + right-click)
sudo cp systemd/silead-unfold.service    /etc/systemd/system/
sudo cp systemd/silead-longpress.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now silead-unfold.service silead-longpress.service

# 5. Auto-rotation (runs in the user session, not system-wide)
cp autostart/linux-tablet-autorotate.desktop ~/.config/autostart/

# 6. Reboot
sudo reboot
```

After rebooting, check: finger/stylus taps land precisely, rotating the tablet
turns the screen and touch together, and a long press produces a right-click.

There's also a helper script: `./install.sh` puts everything in place
automatically. Run `./install.sh --firmware` only if your hardware is
compatible (see the diagnostics doc).

## Tuning for your own screen

`linux-tablet-autorotate` assumes the output `eDP-1` and the virtual touch
device `silead_ts_unfolded touchscreen`. Check your screen name
(`xrandr | grep " connected"`) and adjust the `SCREEN` variable at the top of
the script if needed.

The calibration curves in `silead-unfold.py` (`LEFT_RAW_TO_OUT` /
`RIGHT_RAW_TO_OUT`) were measured for one specific panel. How to take your own
is described in [docs/diagnostics.md](docs/diagnostics.md).

## Weak hardware: pin your kernel

On low-power tablets (Intel Atom and similar) newer kernels are often **slower**
than older ones and may pull in buggy drivers (Wi-Fi, in our case). The fix is
to roll back to a stable kernel and pin it so updates don't bring a "newer" one
back:

```bash
# see the current kernel
uname -r

# pin the working kernel and meta-packages (example for 6.8.0-136)
sudo apt-mark hold linux-image-6.8.0-136-generic linux-headers-6.8.0-136-generic \
                   linux-image-generic linux-headers-generic linux-generic

# verify
apt-mark showhold
```

For Debian/MX the package name differs (`linux-image-6.12.90+deb13-amd64` and
the meta-package `linux-image-amd64`) — check `dpkg -l | grep linux-image`.

---

## Credits

Built with the patience of one grandpa and two AI assistants, over a month of
evenings. If this repo helped bring your hardware back to life — that was the
whole point.

## License

Scripts: MIT. The firmware belongs to the controller's manufacturer and is
provided as-is, for owners of compatible hardware only.
