#!/usr/bin/env bash
# Linux Tablet (CachyOS) configuration-only archive. Does not include personal/Android data.
set -euo pipefail
if (( EUID == 0 )); then echo 'Запускай без sudo от пользователя Plasma.' >&2; exit 1; fi
umask 077
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$HOME/Документы/Linux-Tablet-Archives"
mkdir -p -- "$DEST"
TEMP="$(mktemp -d)"
trap 'rm -rf -- "$TEMP"' EXIT
ROOT="$TEMP/LinuxTablet-CachyOS-$STAMP"
mkdir -p "$ROOT/system" "$ROOT/user" "$ROOT/diagnostics"

save_system_file() {
  local src="$1"
  local target="$ROOT/system$src"
  [[ -f "$src" && -r "$src" ]] || return 0
  mkdir -p "$(dirname "$target")"
  cp -- "$src" "$target"
}
save_user_file() {
  local src="$1" rel
  [[ -f "$src" && -r "$src" ]] || return 0
  rel="${src#$HOME/}"
  mkdir -p "$ROOT/user/$(dirname "$rel")"
  cp -- "$src" "$ROOT/user/$rel"
}

for f in \
  /usr/local/bin/silead-unfold.py \
  /usr/local/bin/silead-longpress.py \
  /usr/local/sbin/waydroid-folder-watch \
  /etc/systemd/system/silead-unfold.service \
  /etc/systemd/system/silead-longpress.service \
  /etc/systemd/system/waydroid-bind-folders.service \
  /etc/udev/rules.d/99-silead-ignore.rules \
  /etc/modules-load.d/uinput.conf \
  /usr/lib/firmware/silead/mssl1680.fw; do
  save_system_file "$f"
done
shopt -s nullglob
for f in /usr/local/bin/linux-tablet-* /etc/systemd/system/linux-tablet*.service; do
  save_system_file "$f"
done
for f in "$HOME"/.local/bin/linux-tablet-* \
         "$HOME"/.config/autostart/linux-tablet*.desktop \
         "$HOME"/Загрузки/install-waydroid-folders-cachyos*.sh; do
  save_user_file "$f"
done

# KDE Plasma layout, tablet input settings and output configuration.
# Keep these in a private local backup; do not upload them to public GitHub.
for f in \
  "$HOME/.config/kwinrc" \
  "$HOME/.config/kcminputrc" \
  "$HOME/.config/kxkbrc" \
  "$HOME/.config/plasmarc" \
  "$HOME/.config/plasmashellrc" \
  "$HOME/.config/plasma-org.kde.plasma.desktop-appletsrc" \
  "$HOME/.config/kwinoutputconfig.json" \
  "$HOME/.config/kglobalshortcutsrc" \
  "$HOME/.config/plasma-keyboardrc" \
  "$HOME/.config/plasmakeyboardrc"; do
  save_user_file "$f"
done
for f in "$HOME"/.local/share/kscreen/*; do
  save_user_file "$f"
done
# Add current Waydroid configuration (but not Android image or userdata).
save_system_file /var/lib/waydroid/waydroid.cfg

{
  echo "Linux Tablet configuration snapshot: $STAMP"
  echo "This archive contains scripts/configuration/firmware only."
  echo 'It DOES NOT contain documents, Android app data, or a bootable disk image.'
  echo 'It MAY contain personal KDE preferences, screen layouts and local paths; keep private.'
  echo 'A Snapper root snapshot may not include the Btrfs @home subvolume.'
  echo
  echo "Kernel: $(uname -r)"
  echo "Session: ${XDG_SESSION_TYPE:-unknown}"
  echo "User: $(id -un)"
  echo
  echo 'Saved configuration tree:'
  find "$ROOT/system" "$ROOT/user" -type f -printf '%P\n' | LC_ALL=C sort
} > "$ROOT/README.txt"

{
  echo '=== UNIT STATE ==='
  systemctl is-enabled silead-unfold silead-longpress waydroid-bind-folders waydroid-container || true
  systemctl is-active silead-unfold silead-longpress waydroid-bind-folders waydroid-container || true
  echo '=== AUTOSTART FILES ==='
  ls -la "$HOME/.config/autostart"/linux-tablet*.desktop 2>/dev/null || true
  echo '=== DISPLAY ==='
  command -v kscreen-doctor >/dev/null && kscreen-doctor -o || true
  echo '=== WAYDROID ==='
  command -v waydroid >/dev/null && waydroid status || true
  echo '=== MOUNTS ==='
  if command -v findmnt >/dev/null; then
    for d in Download Documents Pictures; do
      findmnt --mountpoint "$HOME/.local/share/waydroid/data/media/0/$d/LinuxShared" || true
    done
  fi
  echo '=== PACKAGE VERSIONS ==='
  pacman -Q python-evdev python-pyqt6 plasma-keyboard waydroid iio-sensor-proxy 2>&1 || true
} > "$ROOT/diagnostics/state.txt"

{
  echo '=== CURRENT ACL OF LINUX SHARE ROOTS ==='
  getfacl -p "$HOME/Загрузки" "$HOME/Документы" "$HOME/Изображения" 2>&1 || true
} > "$ROOT/diagnostics/shared-folders-acl.txt"

if [[ -r /usr/lib/firmware/silead/mssl1680.fw ]]; then
  md5sum /usr/lib/firmware/silead/mssl1680.fw > "$ROOT/diagnostics/firmware-md5.txt"
fi

ARCHIVE="$DEST/LinuxTablet-CachyOS-$STAMP.tar.gz"
tar -czf "$ARCHIVE" -C "$TEMP" "LinuxTablet-CachyOS-$STAMP"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
echo "Резервная копия создана: $ARCHIVE"
echo "Контрольная сумма: $ARCHIVE.sha256"
echo 'Внимание: это копия конфигурации, не полный образ системы и не резервная копия Android-приложений.'