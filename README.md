# Reviving a no-name Chinese tablet under Linux

**From a "brick" with a broken touchscreen to a snappy Linux tablet: touch, auto-rotation, gestures, right-click.**

This is the story of how a no-name Chinese 11.6" convertible tablet
(board `HJC-BI-11.6-S8`, Silead MSSL1680 touch controller) — whose
touchscreen was completely dead under Windows and mirrored / "folded" under
Linux — became a usable touch device on Linux Mint. The current work is
improving tablet-mode interaction while keeping the working desktop system.

The real value of this repo is **not a ready-made recipe for one specific
board** (there are maybe a handful of those owners in the world). It's the
**method**: how to diagnose your own touchscreen, understand what's actually
broken, and pick the right fix. If you have a different Silead-based tablet,
start with [docs/diagnostics.md](docs/diagnostics.md).

---

## Current status — 24 September 2026

The main device is the **Intel Celeron N4020** tablet. Its current baseline is
**Linux Mint 22.3 Zena, Cinnamon 6.6.9, X11**. The project Mint image was
restored, the system was updated, and the owner confirmed that it works after
rebooting. Touch input is working with the project's Silead correction stack.

The owner has tested the keyboard migration and confirmed that its mode
switching and manual activation work. The intended interaction is now working:

- **Laptop mode:** the on-screen keyboard and its panel icon are hidden.
- **Tablet mode:** a keyboard icon toggles Cinnamon's native keyboard.
- **Input focus:** selecting a text field or spreadsheet cell does not need to
  open the keyboard.

**Remaining issue:** when the tablet is turned on its side, there is a large
unused gap in the area reserved for the keyboard; the visible keyboard takes
roughly half that space. Matching the reserved area to the keyboard geometry
is the next task.

The migration installer is available in
[PR #1](https://github.com/papaes64-hub/china-tablet-linux-/pull/1), with
[installation and checks](https://github.com/papaes64-hub/china-tablet-linux-/blob/1cf0655ba132a63a1f3ecb806b783e361cb5cf08/docs/cinnamon-keyboard.md).
The `scripts/linux-tablet-autorotate` shipped on `main` still contains the older
Onboard integration; the migration patches the installed copy. Reinstalling
that old script would undo the keyboard integration.

Linux Mint documents the redesigned native keyboard in its
[Cinnamon 6.6 release notes](https://linuxmint.com/rel_zena_whatsnew.php).

## Working components and their limits

- **Touchscreen** — accurate response with both finger and stylus in every
  orientation (a nonlinearly "folded" X axis is corrected by a custom
  coordinate handler).
- **Screen auto-rotation** via the accelerometer, with touch coordinates
  rotating together with the screen. The published script also switches the
  physical keyboard and touchpad according to its orientation-to-mode mapping.
- **Right-click on long press** — otherwise there's no way to reach the context
  menu on a button-less tablet.
- **Gestures** were tested during the project, but a complete gesture profile
  is not included here. Scrolling, zoom and application behavior still need
  checking in the current Mint installation.

The published rotation script maps `bottom-up` to laptop mode and the other
three recognized orientations to tablet mode. It does **not** measure the
hinge angle using both accelerometers. Later implementations on other
desktops must not be confused with this particular script.

## Requirements

- Linux Mint 22.3 with **Cinnamon, X11 and systemd** for the installation
  commands and session script below. The diagnostic method can be reused on
  other distributions; their desktop and service integration must be adapted.
- A Silead touch controller (check: `dmesg | grep -i silead`).
- Packages:

```bash
sudo apt install python3-evdev iio-sensor-proxy touchegg
```

(tested versions: `python3-evdev 1.7.0`, `iio-sensor-proxy 3.5`, `touchegg 2.0.16`)

These are versions recorded during development, not a fresh inventory of the
restored system. The current rotation script also calls `xinput`, `xrandr`,
`setxkbmap`, Cinnamon/Nemo settings and Onboard. `install.sh --deps` only
installs the three packages listed above; it does not configure gestures or
complete the keyboard migration.

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
sudo mkdir -p /lib/firmware/silead
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
mkdir -p ~/.config/autostart
cp autostart/linux-tablet-autorotate.desktop ~/.config/autostart/

# 6. Reboot
sudo reboot
```

After rebooting, check: finger/stylus taps land precisely, rotating the tablet
turns the screen and touch together, and a long press produces a right-click.

There's also a helper script: `bash install.sh` puts the scripts and service
files in place. Run `bash install.sh --firmware` only if your hardware is
compatible (see the diagnostics doc).

On an existing project installation, compare and back up the installed scripts
before running the installer: it overwrites them with the repository copies.

## Tuning for your own screen

`linux-tablet-autorotate` assumes the output `eDP-1` and the virtual touch
device `silead_ts_unfolded touchscreen`. Check your screen name
(`xrandr | grep " connected"`) and adjust the `SCREEN` variable at the top of
the script if needed.

The calibration curves in `silead-unfold.py` (`LEFT_RAW_TO_OUT` /
`RIGHT_RAW_TO_OUT`) were measured for one specific panel. How to take your own
is described in [docs/diagnostics.md](docs/diagnostics.md).

## Kernel changes: record and compare

Earlier experiments included kernel rollback and package holds. That is a
record of troubleshooting on this device, not evidence that newer kernels
are generally slower or that every installation should freeze its kernel.
The running kernel after the latest Mint update has not yet been recorded
here. Collect the actual state before applying an old workaround:

```bash
uname -r
apt-mark showhold
```

Tie any rollback to a reproduced regression and a tested alternative. The old
`6.8.0-136` example is not a requirement for the current setup.

## Other systems tested during the project

These are results from separate installations on the N4020. This repository
does not contain a complete installer for each of them.

| System | Recorded result | Current role |
| --- | --- | --- |
| antiX, X11/IceWM, runit | Touch, rotation, physical input switching and Onboard worked after a cold start. | Historical working setup; different service integration. |
| MX based on Debian 13, KDE Plasma 6.3.6, Wayland, SysVinit | Touch, long press, tablet mode, rotation and virtual keyboard worked after a cold start. | Historical working setup; KDE and init scripts differ from Mint. |
| Ubuntu 26.04.1, GNOME Wayland | Tablet features and startup in both folded and laptop positions were confirmed. | Historical working setup; GNOME integration differs from Mint. |
| Ubuntu with Waydroid | Android ran, but rotation, window geometry and reliable shared-folder access remained unresolved. | Unfinished experiment. |
| ChromeOS/Brunch on N4020 | Tested builds had Android/ARC failures, missing sound and an undetected touchscreen. | Not a completed replacement for Mint. |
| FydeOS on N4020 | After installation, sound, keyboard, touchpad and Google web apps worked; the touchscreen and Android setup remained unresolved. | Unfinished experiment. |

A separate successful ChromeOS/Brunch experiment on an **N95** device is not
evidence of compatibility with this N4020 tablet.

The current focus is improving Mint's keyboard and touch gestures. Results
from previous systems remain useful, but each change needs checking in the
desktop session where it will actually run.

---

## Credits

Built with the patience of one grandpa and two AI assistants, over a month of
evenings. If this repo helped bring your hardware back to life — that was the
whole point.

## License

Scripts: MIT. The firmware belongs to the controller's manufacturer and is
provided as-is, for owners of compatible hardware only.
