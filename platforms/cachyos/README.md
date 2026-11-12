# Linux Tablet on CachyOS — Plasma 6.8, Wayland and Waydroid

**Languages:** English · [Русская инструкция](README.ru.md)

A real-world project to bring a low-cost 11.6-inch convertible back to life as a **Linux laptop + touchscreen tablet + Android environment**. The touchscreen was originally unusable because the Silead controller reported a folded / nonlinear X axis. A custom userspace filter, orientation sensors and KDE input controls made it usable again.

> **Status: successfully used on one specific device, 24 October 2026.** This is a documented, device-specific working configuration — **not** a tested one-click installer or a guarantee of compatibility with other tablets. The CachyOS changes are proposed in a separate draft pull request; the older Linux Mint setup remains intact.

## Tested hardware and software

| Component | Confirmed setup |
| --- | --- |
| CPU / display | Intel Celeron N4020 convertible, 11.6-inch, 1366×768, `eDP-1` |
| OS | CachyOS, Linux `6.18.50-1-cachyos-lts` |
| Desktop | KDE Plasma **6.8.1** on **Wayland** |
| Touch controller | Silead MSSL1680, corrected by `silead-unfold.py` v10 |
| Touch firmware | `mssl1680.fw`, 38,544 bytes, MD5 `86e979b61f02ebad6b20ac66a7708c29` |
| Inertial sensors | `i2c-KIOX020A:00` and `i2c-KIOX010A:00` |
| Built-in keyboard | `AT Translated Set 2 keyboard` |
| Touchpad | `HTIX5288:00 0911:5288 Touchpad` |
| Android integration | Waydroid, with shared Linux folders |

**What works on the tested tablet:** accurate touch, long-press right-click, screen rotation in all four orientations, keyboard/touchpad switching at a **190° hinge angle**, automatic tablet/laptop mode, an on-screen keyboard with a tray toggle, Waydroid, and shared files between Android and Linux. The shared-folder mounts were independently confirmed from Android after a reboot.

### Device-specific warnings

1. **Never assume that this Silead firmware or the two-half X calibration suits another device.** Diagnose your own controller and board first. See [touch diagnostics](../../docs/diagnostics.md) and the [main project README](../../README.md).
2. The root-level `install.sh` is for the older **Linux Mint / Cinnamon / X11** implementation. **Do not run it as a CachyOS installer.** It uses `apt` and X11/Cinnamon commands.
3. The KDE scripts disable real input devices when run with `--apply`. Test detection first and keep a recovery method available (for example, an external USB keyboard).
4. Do not upload your local backup archives, Android data, personal Plasma settings, or machine-specific permission exports to a public repository.

## Repository layout

Files in this folder:

| File | Purpose |
| --- | --- |
| [`scripts/linux-tablet-hinge-kde.py`](scripts/linux-tablet-hinge-kde.py) | Two-sensor hinge-angle detection and physical input switching through KWin D-Bus |
| [`scripts/linux-tablet-autorotate-kde.py`](scripts/linux-tablet-autorotate-kde.py) | Tablet-only display rotation using `monitor-sensor` and `kscreen-doctor` |
| [`scripts/linux-tablet-keyboard-tray.py`](scripts/linux-tablet-keyboard-tray.py) | Plasma Keyboard tray control visible only when folded |
| [`tools/install-waydroid-folders-cachyos-v3.sh`](tools/install-waydroid-folders-cachyos-v3.sh) | Systemd watcher for Android-visible `LinuxShared` folders |
| [`tools/backup-linux-tablet-cachyos-v2-fixed.sh`](tools/backup-linux-tablet-cachyos-v2-fixed.sh) | Local configuration backup with checksums |

The common [Silead v10 filter](../../scripts/silead-unfold.py), [long-press helper](../../scripts/silead-longpress.py), [services](../../systemd) and [udev rule](../../udev/99-silead-ignore.rules) already exist at repository root.

## 1. Prerequisites and inspection

Use a **Plasma Wayland** session and install the needed packages on CachyOS/Arch:

```bash
sudo pacman -S --needed git python-evdev python-pyqt6 qt6-tools iio-sensor-proxy plasma-keyboard acl
```

KDE provides `kscreen-doctor` and KWin D-Bus; systemd, `busctl`, `uinput` and `monitor-sensor` must be available. Waydroid is installed and configured separately.

**Before changing anything**, inspect your own hardware:

```bash
echo "$XDG_SESSION_TYPE"      # expected: wayland
kscreen-doctor -o             # expected display name on this tablet: eDP-1
grep -i silead /proc/bus/input/devices
for f in /sys/bus/iio/devices/iio:device*/name; do
    [ -f "$f" ] && printf '%s: %s\n' "$f" "$(cat "$f")"
done
busctl --user tree org.kde.KWin --list | grep '/org/kde/KWin/InputDevice/event'
```

These commands are **read-only**. Do not adopt any hardcoded `eventN` numbers: they may change at each reboot.

## 2. Touchscreen stack — tested board only

The working tablet uses the repository's Silead v10 filter and long-press service. **Only if you have confirmed that your controller/firmware/calibration match the test device**, you can stage these components from the repository root:

```bash
# On a machine with the verified controller only:
echo '86e979b61f02ebad6b20ac66a7708c29  firmware/mssl1680.fw' | md5sum -c -

sudo install -Dm644 firmware/mssl1680.fw /usr/lib/firmware/silead/mssl1680.fw
sudo install -Dm755 scripts/silead-unfold.py /usr/local/bin/silead-unfold.py
sudo install -Dm755 scripts/silead-longpress.py /usr/local/bin/silead-longpress.py
sudo install -Dm644 udev/99-silead-ignore.rules /etc/udev/rules.d/99-silead-ignore.rules
sudo install -Dm644 systemd/silead-unfold.service /etc/systemd/system/silead-unfold.service
sudo install -Dm644 systemd/silead-longpress.service /etc/systemd/system/silead-longpress.service

sudo modprobe uinput
printf 'uinput\n' | sudo tee /etc/modules-load.d/uinput.conf
sudo udevadm control --reload
sudo systemctl daemon-reload
sudo systemctl enable silead-unfold.service silead-longpress.service
```

Restart the machine *after saving your work* so that firmware and udev settings take effect. Then check:

```bash
systemctl is-active silead-unfold silead-longpress
grep -E 'Name="(silead|Silead)' /proc/bus/input/devices
sudo journalctl -b -u silead-unfold -u silead-longpress -n 35 --no-pager
```

On the confirmed system, the raw `silead_ts` input is hidden from libinput, the corrected `silead_ts_unfolded touchscreen` device is used, and `silead_longpress_mouse` emits a right-click after approximately **0.75 seconds** of stationary one-finger touch.

## 3. Tablet/laptop hinge and automatic rotation

Install the three KDE scripts **from the repository root**:

```bash
mkdir -p ~/.local/bin ~/.config/autostart
install -m755 platforms/cachyos/scripts/linux-tablet-hinge-kde.py \
    ~/.local/bin/linux-tablet-hinge-kde
install -m755 platforms/cachyos/scripts/linux-tablet-autorotate-kde.py \
    ~/.local/bin/linux-tablet-autorotate-kde
install -m755 platforms/cachyos/scripts/linux-tablet-keyboard-tray.py \
    ~/.local/bin/linux-tablet-keyboard-tray
```

Start with **observation-only checks** (stop the first two with `Ctrl+C` before moving to the next):

```bash
~/.local/bin/linux-tablet-hinge-kde
~/.local/bin/linux-tablet-autorotate-kde
~/.local/bin/linux-tablet-keyboard-tray --check
```

The original device uses a calibrated Kionix transformation `(X,Y,Z) → (Y,X,-Z)` for the base sensor, with signed `atan2` angle calculation. The confirmed mode rule is:

- **Hinge < 190°** → laptop mode: built-in keyboard and touchpad enabled; normal horizontal display.
- **Hinge ≥ 190°** → tablet mode: built-in keyboard and touchpad disabled; screen orientation follows the accelerometer.
- Both switch after about **0.70 seconds** of stable posture.

The confirmed `monitor-sensor` mapping is:

| Sensor orientation | KDE output rotation |
| --- | --- |
| `bottom-up` | `normal` |
| `normal` | `inverted` |
| `left-up` | `left` |
| `right-up` | `right` |

Only after your measurements match the tested behavior, start the controls:

```bash
~/.local/bin/linux-tablet-hinge-kde --apply
~/.local/bin/linux-tablet-autorotate-kde --apply
```

Run these in **separate terminals** for initial testing; each command remains active. They use KWin D-Bus and `kscreen-doctor`, **not** X11 `xinput`/`xrandr`.

### On-screen keyboard

In **System Settings → Keyboard → Virtual Keyboard**, choose **Plasma Keyboard**. The tray helper uses the Plasma 6.8 KWin virtual-keyboard D-Bus `mode` property:

- Laptop: `mode = 0` (disabled and tray icon hidden).
- Tablet: `mode = 1` (shown when touching a text input; visible tray icon toggles it manually).

Start the tray helper after verifying the hinge script:

```bash
~/.local/bin/linux-tablet-keyboard-tray
```

If two on-screen keyboard icons appear, hide the stock *Input Method* item using the Plasma system tray settings.

### Start automatically with Plasma

**After the manual tests pass**, the tested installation uses three independent desktop autostart entries in `~/.config/autostart/`. One safe way to create these for your own account:

```bash
mkdir -p ~/.config/autostart
for spec in \
  'linux-tablet-hinge:linux-tablet-hinge-kde --apply' \
  'linux-tablet-autorotate:linux-tablet-autorotate-kde --apply' \
  'linux-tablet-keyboard:linux-tablet-keyboard-tray'; do
    name="${spec%%:*}"
    run="${spec#*:}"
    cat > "$HOME/.config/autostart/$name.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$name
Exec=$HOME/.local/bin/$run
Terminal=false
OnlyShowIn=KDE;
EOF
done
```

Check for older conflicting autostart entries before enabling these. **Do not run two copies of the same controller.** Reboot and test both unfolded and folded starts before treating the configuration as stable.

## 4. Waydroid shared folders — the Android FUSE issue

The working Linux directories have **Russian names**:

| Linux path | Android path |
| --- | --- |
| `~/Загрузки` | `/sdcard/Download/LinuxShared` |
| `~/Документы` | `/sdcard/Documents/LinuxShared` |
| `~/Изображения` | `/sdcard/Pictures/LinuxShared` |

**Why subfolders?** A direct Linux bind mount over Android's `/data/media/0/Download` was visible at the low-level path but **not** through `/sdcard/Download` in Android's FUSE layer. A nested `LinuxShared` folder became visible to Android.

The [v3 installation script](tools/install-waydroid-folders-cachyos-v3.sh) expects the exact Russian Linux folder names above. Users of English or other locales **must review and change both the Linux directory mappings in the script before running it**. Do not let the script create or overwrite unexpected folders.

Once Waydroid itself is working:

```bash
bash platforms/cachyos/tools/install-waydroid-folders-cachyos-v3.sh
systemctl is-active waydroid-bind-folders
sudo journalctl -u waydroid-bind-folders -n 35 --no-pager
```

After starting Android, test that each folder is visible **through Android FUSE**:

```bash
for d in Download Documents Pictures; do
    sudo waydroid shell -- cat "/sdcard/$d/LinuxShared/.linux-tablet-waydroid-v3.check"
done
```

The watcher is enabled at Linux boot, waits for Android, checks the FUSE-visible markers, and reconnects folders when needed. On the tested device all three became visible again after reboot. **During startup, several reconnect attempts were observed**, so check logs and avoid editing important files during reconnects.

### Android → Linux write permissions

A Linux directory owned by UID 1000 with mode `755` was only readable from Android. A dedicated `777` *test directory* proved Android could write, but **do not use recursive `chmod 777` for your real home folders**.

The tested Android app wrote files as **UID 10141**. Targeted POSIX ACL entries for UID 1000 (Linux user) and UID 10141 (tested Android app) permitted read/write in both directions, including inheritance for newly created files. Other Android apps can use **different UIDs**; determine their actual IDs and test them individually.

**The v3 installer does not grant ACL permissions.** Configure folder permissions consciously, keep an ACL backup, and verify both file creation and file reading from ordinary Android applications. SELinux was disabled in the tested Waydroid, but that is not a universal requirement or recommendation.

## 5. Back up the working configuration

The tested tablet saved its configuration archive with a successful SHA-256 verification and created **Snapper snapshot #37**, described as `Linux Tablet CachyOS GOLD - 24.10.2026`.

```bash
bash platforms/cachyos/tools/backup-linux-tablet-cachyos-v2-fixed.sh
```

The archive is intended for **private storage** (ideally a separate drive). It may contain machine-local settings and ACL metadata and **must not be committed to a public repository**. It is **not** a full-disk image, does not back up Android apps/data, and a root Btrfs/Snapper snapshot may not include `@home`.

## Known limits and contribution notes

- Successful use on **one N4020 machine** is established; a clean install performed exclusively using these repo instructions is **not yet validated**.
- The Silead firmware/calibration and 190° hinge mapping are device-specific.
- Linux folder names, Android app UIDs and Plasma versions vary; adapt after inspection rather than running scripts blindly.
- Waydroid FUSE can require repeated reconnection after Android startup.
- The existing Mint setup and its separate pull request remain untouched.

**Credits:** a collaborative project between the device owner, who performed the hardware measurements and hands-on acceptance tests, and ChatGPT, which assisted with investigation, code and documentation. Contributions and constructive reproducibility reports are welcome.
