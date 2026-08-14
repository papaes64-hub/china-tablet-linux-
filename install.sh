#!/usr/bin/env bash
#
# Installer for the touch stack of a Chinese Silead tablet.
#
#   WARNING: firmware/mssl1680.fw matched ONE SPECIFIC device.
#   If you are not sure your sensor is compatible, first read
#   docs/diagnostics.md and do NOT run with --firmware until you verify.
#
# Usage:
#   ./install.sh              - install scripts, services, udev, autostart
#   ./install.sh --firmware   - same + install firmware (compatible hardware only)
#   ./install.sh --deps       - install dependency packages first
#
set -euo pipefail

# directory where the repo itself lives
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WITH_FIRMWARE=0
WITH_DEPS=0
for arg in "$@"; do
    case "$arg" in
        --firmware) WITH_FIRMWARE=1 ;;
        --deps)     WITH_DEPS=1 ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

if [[ $EUID -eq 0 ]]; then
    echo "Do not run as root directly - the script calls sudo where needed."
    echo "the autostart file must go into a normal user's home directory."
    exit 1
fi

echo "==> Repository: $REPO"

if [[ $WITH_DEPS -eq 1 ]]; then
    echo "==> Installing dependencies..."
    sudo apt update
    sudo apt install -y python3-evdev iio-sensor-proxy touchegg
fi

echo "==> Scripts into /usr/local/bin ..."
sudo cp "$REPO/scripts/silead-unfold.py"        /usr/local/bin/
sudo cp "$REPO/scripts/silead-longpress.py"     /usr/local/bin/
sudo cp "$REPO/scripts/linux-tablet-autorotate" /usr/local/bin/
sudo chmod +x /usr/local/bin/silead-unfold.py \
              /usr/local/bin/silead-longpress.py \
              /usr/local/bin/linux-tablet-autorotate

echo "==> udev rule (hides the raw sensor from libinput) ..."
sudo cp "$REPO/udev/99-silead-ignore.rules" /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger

echo "==> System services (unfold + longpress) ..."
sudo cp "$REPO/systemd/silead-unfold.service"    /etc/systemd/system/
sudo cp "$REPO/systemd/silead-longpress.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now silead-unfold.service silead-longpress.service

echo "==> Auto-rotation (autostart in the user session) ..."
mkdir -p "$HOME/.config/autostart"
cp "$REPO/autostart/linux-tablet-autorotate.desktop" "$HOME/.config/autostart/"

if [[ $WITH_FIRMWARE -eq 1 ]]; then
    echo "==> Controller firmware ..."
    echo "    (compatible hardware only - see docs/diagnostics.md)"
    sudo mkdir -p /lib/firmware/silead
    sudo cp "$REPO/firmware/mssl1680.fw" /lib/firmware/silead/mssl1680.fw
    echo "    Reload the module or reboot for the firmware to take effect."
else
    echo "==> Firmware NOT touched (run with --firmware if hardware is compatible)."
fi

echo
echo "==> Done. Check the status:"
echo "    systemctl status silead-unfold silead-longpress --no-pager"
echo "==> A reboot is recommended:  sudo reboot"
echo
echo "After reboot: finger/stylus taps should land precisely,"
echo "rotating the tablet turns screen and touch together, a long press"
echo "produces a right-click."
