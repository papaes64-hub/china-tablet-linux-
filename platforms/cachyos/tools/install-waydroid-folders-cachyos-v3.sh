#!/usr/bin/env bash
# Linux Tablet / CachyOS: Waydroid shared folders, reboot-safe watcher (v3).
# Run as the desktop user, NOT as root.
set -euo pipefail

if (( EUID == 0 )); then
  echo 'Запускай без sudo: bash ~/Загрузки/install-waydroid-folders-cachyos-v3.sh' >&2
  exit 1
fi
LOGIN="$(id -un)"
USER_HOME="$(getent passwd "$LOGIN" | cut -d: -f6)"
BASE="$USER_HOME/.local/share/waydroid/data/media/0"
SERVICE='waydroid-bind-folders.service'
UNIT="/etc/systemd/system/$SERVICE"
WATCHER='/usr/local/sbin/waydroid-folder-watch'

for dir in 'Загрузки' 'Документы' 'Изображения'; do
  [[ -d "$USER_HOME/$dir" ]] || { echo "Нет папки: $USER_HOME/$dir" >&2; exit 1; }
done
for cmd in waydroid mountpoint findmnt getfacl systemctl; do
  command -v "$cmd" >/dev/null || { echo "Не найдена команда: $cmd" >&2; exit 1; }
done
systemctl cat waydroid-container.service >/dev/null 2>&1 || {
  echo 'Не найдена служба waydroid-container.service' >&2; exit 1;
}

TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT
cat > "$TMP/waydroid-folder-watch" <<'WATCHER_CODE'
#!/usr/bin/env bash
# Independent systemd watcher: starts on Linux boot, attaches only after Android FUSE is ready.
set -u
LOGIN="${1:?desktop username required}"
USER_HOME="${2:?desktop home required}"
BASE="$USER_HOME/.local/share/waydroid/data/media/0"
LINUX_DIRS=('Загрузки' 'Документы' 'Изображения')
ANDROID_DIRS=('Download' 'Documents' 'Pictures')
MARKER='.linux-tablet-waydroid-v3.check'
LAST='starting'
LAST_WARNING=0

log() { echo "$(date '+%F %T') waydroid-folder-watch: $*"; }
warn_throttled() {
  local now
  now=$(date +%s)
  if (( now - LAST_WARNING >= 90 )); then log "$*"; LAST_WARNING=$now; fi
}

# Markers distinguish actual Android FUSE visibility from merely mounted host paths.
# Do not alter or overwrite a marker with unexpected contents.
ensure_marker() {
  local src="$1" tag="$2" path content
  path="$src/$MARKER"
  content="LinuxTablet-v3:$LOGIN:$tag"
  if [[ -e "$path" ]]; then
    [[ -f "$path" && "$(cat -- "$path" 2>/dev/null)" == "$content" ]] || {
      log "ВНИМАНИЕ: файл проверки уже существует с другим содержимым: $path"
      return 1
    }
  else
    printf '%s\n' "$content" > "$path" || return 1
  fi
}

android_ready() {
  systemctl is-active --quiet waydroid-container.service || return 1
  [[ -d "$BASE" ]] || return 1
  timeout 10s waydroid shell -- sh -c 'test -d /sdcard/Download && test -d /sdcard/Documents && test -d /sdcard/Pictures' >/dev/null 2>&1
}

# Verify via /sdcard, because Android FUSE can hide a host bind mount.
android_sees_marker() {
  local tag="$1" expected actual
  expected="LinuxTablet-v3:$LOGIN:$tag"
  actual=$(timeout 10s waydroid shell -- cat "/sdcard/$tag/LinuxShared/$MARKER" 2>/dev/null) || return 1
  [[ "$actual" == "$expected" ]]
}

same_source() {
  local src="$1" target="$2"
  [[ -d "$src" && -d "$target" ]] || return 1
  [[ "$(stat -Lc '%d:%i' -- "$src" 2>/dev/null)" == "$(stat -Lc '%d:%i' -- "$target" 2>/dev/null)" ]]
}

attach_all() {
  local i src target tag success=0
  timeout 12s waydroid shell -- mkdir -p \
    /sdcard/Download/LinuxShared \
    /sdcard/Documents/LinuxShared \
    /sdcard/Pictures/LinuxShared >/dev/null 2>&1 || return 1

  for i in 0 1 2; do
    src="$USER_HOME/${LINUX_DIRS[$i]}"
    tag="${ANDROID_DIRS[$i]}"
    target="$BASE/$tag/LinuxShared"
    [[ -d "$src" && -d "$target" ]] || { warn_throttled "Ожидание $target"; continue; }
    ensure_marker "$src" "$tag" || continue

    if mountpoint -q -- "$target"; then
      if ! same_source "$src" "$target"; then
        warn_throttled "Чужое монтирование $target — не трогаю"
        continue
      fi
      if android_sees_marker "$tag"; then
        ((success+=1))
        continue
      fi
      log "Монтирование $tag существует, но Android FUSE его не видит. Переподключаю."
      if ! umount -- "$target"; then
        warn_throttled "Не удалось отключить занятое монтирование $target"
        continue
      fi
    fi

    if ! mount --bind -- "$src" "$target"; then
      warn_throttled "Ошибка mount --bind: $src -> $target"
      continue
    fi
    if android_sees_marker "$tag"; then
      log "Подключено и видно через Android: ${LINUX_DIRS[$i]} -> $tag/LinuxShared"
      ((success+=1))
    else
      warn_throttled "$tag/LinuxShared подключено в Linux, но Android FUSE пока не видит файл проверки"
    fi
  done
  (( success == 3 ))
}

# Clean only mounts whose source is one of our three known directories.
cleanup() {
  local i src target
  for i in 0 1 2; do
    src="$USER_HOME/${LINUX_DIRS[$i]}"
    target="$BASE/${ANDROID_DIRS[$i]}/LinuxShared"
    if mountpoint -q -- "$target" && same_source "$src" "$target"; then
      umount -- "$target" || log "Занято: $target; оставлено до следующей остановки"
    fi
  done
}

stop() { trap - TERM INT; log 'Служба остановлена'; cleanup; exit 0; }
trap stop TERM INT

log "v3 запущена независимо от Waydroid; пользователь=$LOGIN; ожидание Android"
while true; do
  if android_ready; then
    if [[ "$LAST" != 'ready' ]]; then log 'Android FUSE готов, проверяю общие папки'; fi
    if attach_all; then
      LAST='ready'
      sleep 45 & wait $!
    else
      LAST='incomplete'
      sleep 15 & wait $!
    fi
  else
    if [[ "$LAST" != 'waiting' ]]; then log 'Android ещё не готов; служба продолжит ожидание'; fi
    LAST='waiting'
    sleep 15 & wait $!
  fi
done
WATCHER_CODE

cat > "$TMP/$SERVICE" <<UNIT_CODE
[Unit]
Description=Linux Tablet: reboot-safe Waydroid shared folders v3
After=local-fs.target
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart=$WATCHER $LOGIN $USER_HOME
Restart=always
RestartSec=5
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
UNIT_CODE

STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$USER_HOME/.local/share/linux-tablet-backups/waydroid-before-v3-$STAMP"
mkdir -p -- "$BACKUP"
[[ ! -f "$WATCHER" ]] || sudo cp -a -- "$WATCHER" "$BACKUP/waydroid-folder-watch.old"
[[ ! -f "$UNIT" ]] || sudo cp -a -- "$UNIT" "$BACKUP/waydroid-bind-folders.service.old"
getfacl -p "$USER_HOME/Загрузки" "$USER_HOME/Документы" "$USER_HOME/Изображения" > "$BACKUP/acl-original.txt"

# This is the ONLY service we replace. Android container and user's other services stay untouched.
sudo systemctl disable --now "$SERVICE" || true
# Never mount over main Android Download/Documents/Pictures directories.
for tag in Download Documents Pictures; do
  if mountpoint -q -- "$BASE/$tag"; then
    echo "ОСТАНОВЛЕНО: старое монтирование основной папки $BASE/$tag всё ещё активно." >&2
    echo "Пришли: findmnt -R '$BASE'" >&2
    exit 2
  fi
done
for tag in Download Documents Pictures; do
  target="$BASE/$tag/LinuxShared"
  src="$USER_HOME/$(case "$tag" in Download) echo Загрузки;; Documents) echo Документы;; Pictures) echo Изображения;; esac)"
  if mountpoint -q -- "$target" && [[ "$(stat -Lc '%d:%i' "$src")" != "$(stat -Lc '%d:%i' "$target")" ]]; then
    echo "ОСТАНОВЛЕНО: чужое монтирование $target. Не изменяю его." >&2
    exit 3
  fi
done

sudo install -Dm755 "$TMP/waydroid-folder-watch" "$WATCHER"
sudo install -Dm644 "$TMP/$SERVICE" "$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE"

printf '\nРезервная копия: %s\n' "$BACKUP"
printf 'Служба запускается при старте Linux и сама ждёт Waydroid.\n'
systemctl is-enabled "$SERVICE"
systemctl is-active "$SERVICE"
printf '\nЧерез 15–45 секунд после запуска Android проверь:\n'
printf '  sudo journalctl -u %s -n 35 --no-pager\n' "$SERVICE"
printf '  for d in Download Documents Pictures; do findmnt --mountpoint "%s/$d/LinuxShared"; done\n' "$BASE"