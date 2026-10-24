#!/usr/bin/env python3
"""Linux Tablet / CachyOS Plasma 6 Wayland: hinge-controlled physical input.

Angle calibration (September 2026):
  KIOX020A: display, KIOX010A: base
  base transform: (x,y,z) -> (y,x,-z)
  angle = -degrees(atan2(bx*dz - bz*dx, bx*dx + bz*dz)) mod 360
  >=190 deg: tablet, <190 deg: laptop; 0.70 s stable position.

Default is a read-only monitor. --apply controls ONLY the internal keyboard and
HTIX5288 touchpad through KWin's per-device D-Bus enabled property. On exit,
physical input is restored. No root, touchscreen, Waydroid or rotation edits.
"""
import argparse
import math
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

SYSFS = Path('/sys/bus/iio/devices')
SERVICE = 'org.kde.KWin'
INTERFACE = 'org.kde.KWin.InputDevice'
ANGLE_LIMIT = 190.0
DEBOUNCE_SECONDS = 0.70


def command(*args):
    return subprocess.run(args, text=True, capture_output=True,
                          check=True, timeout=4).stdout.strip()


def sensor(name):
    for path in SYSFS.glob('iio:device*'):
        f = path / 'name'
        if f.is_file() and f.read_text().strip() == name:
            return path
    raise RuntimeError(f'Не обнаружен акселерометр {name}')


def vector(path):
    return tuple(int((path / f'in_accel_{axis}_raw').read_text())
                 for axis in 'xyz')


def hinge_angle(display, base):
    display3 = vector(display)
    dx, _, dz = display3
    b0x, b0y, b0z = vector(base)
    bx, bz = b0y, -b0z
    cross = bx * dz - bz * dx
    dot = bx * dx + bz * dz
    if not (cross or dot):
        raise RuntimeError('Недопустимые показания акселерометров')
    angle = (-math.degrees(math.atan2(cross, dot))) % 360.0

    # Folded is mechanically near 360 degrees. In that position noise may
    # produce a small positive angle (e.g., 3 degrees instead of 357).
    # Map this narrow wrap region to 360+ only when the full 3D axes confirm
    # folded posture; don't mistake a truly closed lid for a folded screen.
    base3 = (b0y, b0x, -b0z)
    # The full-vector cosine is used only to resolve the 360° wrap.
    # It is not a tablet-mode switching threshold.
    norm = math.sqrt(sum(x * x for x in display3) *
                     sum(x * x for x in base3))
    c = sum(x * y for x, y in zip(display3, base3)) / norm if norm else -1
    if angle < 45 and c >= 0.80:
        angle += 360
    return angle


def physical_devices():
    tree = command('busctl', '--user', 'tree', SERVICE, '--list')
    paths = sorted(set(re.findall(r'/org/kde/KWin/InputDevice/event\d+', tree)))
    found = {}
    for path in paths:
        raw = command('busctl', '--user', 'get-property', SERVICE,
                      path, INTERFACE, 'name')
        tokens = shlex.split(raw)
        if len(tokens) < 2:
            continue
        name = tokens[1]
        if name == 'AT Translated Set 2 keyboard':
            found['keyboard'] = path
        elif 'HTIX5288' in name and 'Touchpad' in name:
            found['touchpad'] = path
    if set(found) != {'keyboard', 'touchpad'}:
        raise RuntimeError(f'Не найдены клавиатура/тачпад через KWin: {found}')
    return found


def set_physical(enabled):
    # Discover first, then change; don't depend on event numbers remaining
    # the same after suspend or hotplug.
    found = physical_devices()
    for label in ('keyboard', 'touchpad'):
        path = found[label]
        command('busctl', '--user', 'set-property', SERVICE,
                path, INTERFACE, 'enabled', 'b', str(enabled).lower())
        result = command('busctl', '--user', 'get-property', SERVICE,
                         path, INTERFACE, 'enabled')
        if result != f'b {str(enabled).lower()}':
            raise RuntimeError(f'Не удалось переключить {label}: {result}')
        print(f'  {label}: {"ON" if enabled else "OFF"} ({path})', flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true',
                   help='переключать клавиатуру и тачпад; без флага — только наблюдение')
    args = p.parse_args()

    def terminate(_sig, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGHUP, terminate)

    display = sensor('i2c-KIOX020A:00')
    base = sensor('i2c-KIOX010A:00')
    if args.apply:
        physical_devices()  # fail early before making any changes
    print('Linux Tablet: угол шарнира, граница 190°, выдержка 0.70 с', flush=True)
    print('Режим:', 'ПЕРЕКЛЮЧЕНИЕ' if args.apply else 'ТОЛЬКО НАБЛЮДЕНИЕ', flush=True)
    print('Ctrl+C — завершить (физический ввод будет включён).', flush=True)
    stable = pending = None
    pending_since = last_report = 0.0
    try:
        while True:
            angle = hinge_angle(display, base)
            observed = 'TABLET' if angle >= ANGLE_LIMIT else 'LAPTOP'
            now = time.monotonic()
            if observed == stable:
                pending = None
            elif observed != pending:
                pending, pending_since = observed, now
            elif now - pending_since >= DEBOUNCE_SECONDS:
                if args.apply:
                    set_physical(pending == 'LAPTOP')
                stable, pending = pending, None
                print(f'>>> {stable} angle={angle:.1f}°', flush=True)
            if now - last_report >= 2:
                print(f'angle={angle:.1f}°  наблюдение={observed}  '
                      f'состояние={stable or "ожидание"}', flush=True)
                last_report = now
            time.sleep(0.1)
    except KeyboardInterrupt:
        print('\nОстановка', flush=True)
    finally:
        if args.apply:
            try:
                print('Восстанавливаю физический ввод...', flush=True)
                set_physical(True)
            except Exception as exc:
                print(f'ВНИМАНИЕ: не удалось восстановить ввод: {exc}', file=sys.stderr)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as exc:
        print(f'Ошибка: {exc}', file=sys.stderr)
        sys.exit(1)