#!/usr/bin/env python3
"""Linux Tablet: CachyOS/Plasma 6 Wayland display auto-rotation.

Keeps the already installed hinge controller responsible for physical input.
Reuses its exact 190-degree hinge math without modifying it.
Read-only by default; use --apply to change screen rotation via KScreen.

Sensor orientation -> kscreen-doctor mapping confirmed on earlier CachyOS:
    bottom-up -> normal; normal -> inverted;
    left-up -> left; right-up -> right.
In laptop mode always use normal orientation. In tablet mode follow sensor.
"""

import argparse
import fcntl
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import time

SCREEN = 'eDP-1'
HINGE_PATH = Path.home() / '.local/bin/linux-tablet-hinge-kde'
MODE_LIMIT = 190.0
DEBOUNCE_SECONDS = 0.70
ORIENTATIONS = {
    'bottom-up': 'normal',
    'normal': 'inverted',
    'left-up': 'left',
    'right-up': 'right',
}


def cmd(*args):
    p = subprocess.run(args, text=True, capture_output=True, timeout=8)
    if p.returncode:
        raise RuntimeError(f'{" ".join(args)}: {p.stderr.strip() or p.stdout.strip()}')
    return p.stdout


def load_hinge():
    if not HINGE_PATH.is_file():
        raise RuntimeError(f'Нет работающего скрипта шарнира: {HINGE_PATH}')
    # Existing .local/bin entry has no .py extension, so use SourceFileLoader.
    loader = importlib.machinery.SourceFileLoader('linux_tablet_hinge_shared', str(HINGE_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def screen_info():
    output = cmd('kscreen-doctor', '-o')
    output = re.sub(r'\x1b\[[0-9;]*m', '', output)  # strip ANSI colours
    sections = re.split(r'(?=^\s*Output:)', output, flags=re.MULTILINE)
    sec = next((s for s in sections if re.search(r'^Output:\s+\d+\s+' +
                  re.escape(SCREEN) + r'\b', s, flags=re.MULTILINE)), None)
    if sec is None:
        raise RuntimeError(f'Нет выхода {SCREEN} в kscreen-doctor -o')
    match = re.search(r'Auto Rotate Policy:\s*(never|inTabletMode|always)', sec)
    if not match:
        raise RuntimeError('Не найдена политика Auto Rotate Policy')
    return match.group(1)


def policy(value):
    cmd('kscreen-doctor', f'output.{SCREEN}.autoRotatePolicy.{value}')


def rotate(value):
    cmd('kscreen-doctor', f'output.{SCREEN}.rotation.{value}')


def monitor_sensor():
    # stdbuf makes the CLI's text records available immediately.
    return subprocess.Popen(['stdbuf', '-oL', '-eL', 'monitor-sensor'],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='реально поворачивать экран')
    args = parser.parse_args()

    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/tmp/linux-tablet-{os.getuid()}'))
    runtime.mkdir(parents=True, exist_ok=True)
    lock_path = runtime / 'linux-tablet-autorotate-kde.lock'
    with open(lock_path, 'w', encoding='utf-8') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Другой экземпляр автоповорота уже запущен')

        hinge = load_hinge()
        display = hinge.sensor('i2c-KIOX020A:00')
        base = hinge.sensor('i2c-KIOX010A:00')
        prev_policy = screen_info()
        proc = None
        stable_mode = None
        mode_pending = None
        mode_pending_since = 0.0
        desired_pending = None
        desired_pending_since = 0.0
        applied = None
        orientation = None
        report_at = 0.0
        buffer = b''
        stop = False

        def shutdown(*_):
            nonlocal stop
            stop = True

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGHUP, shutdown)

        try:
            if args.apply:
                # Only one agent should rotate KScreen; restore on exit.
                policy('never')
            proc = monitor_sensor()
            print('Linux Tablet auto-rotate:',
                  'ПРИМЕНЕНИЕ' if args.apply else 'НАБЛЮДЕНИЕ', flush=True)
            print('190° — граница шарнира, 0.70 с — стабилизация, экран:', SCREEN,
                  flush=True)
            print('Карта:', ORIENTATIONS, flush=True)

            while not stop:
                if proc.poll() is not None:
                    raise RuntimeError('monitor-sensor завершился; проверь iio-sensor-proxy')
                ready, _, _ = select.select([proc.stdout], [], [], .2)
                if ready:
                    data = os.read(proc.stdout.fileno(), 8192)
                    if data:
                        buffer += data
                        while b'\n' in buffer:
                            raw, buffer = buffer.split(b'\n', 1)
                            line = raw.decode(errors='replace').strip()
                            m = re.search(r'Accelerometer orientation changed:\s*([a-z-]+)', line)
                            if m:
                                orientation = m.group(1)
                                print('IIO:', orientation, flush=True)

                now = time.monotonic()
                angle = hinge.hinge_angle(display, base)
                observed_mode = 'TABLET' if angle >= MODE_LIMIT else 'LAPTOP'

                # Hinge debounce: do not switch around 190 degrees.
                if observed_mode == stable_mode:
                    mode_pending = None
                elif observed_mode != mode_pending:
                    mode_pending = observed_mode
                    mode_pending_since = now
                elif now - mode_pending_since >= DEBOUNCE_SECONDS:
                    stable_mode = observed_mode
                    mode_pending = None
                    print(f'>>> {stable_mode}, угол={angle:.1f}°', flush=True)

                wanted = None
                if stable_mode == 'LAPTOP':
                    wanted = 'normal'
                elif stable_mode == 'TABLET':
                    wanted = ORIENTATIONS.get(orientation)

                # Orientation debounce: ignore transient intermediate events.
                if wanted is None or wanted == applied:
                    desired_pending = None
                elif wanted != desired_pending:
                    desired_pending = wanted
                    desired_pending_since = now
                elif now - desired_pending_since >= DEBOUNCE_SECONDS:
                    if args.apply:
                        rotate(wanted)
                    applied = wanted
                    desired_pending = None
                    print('>>> SCREEN:', wanted,
                          '(выполнено)' if args.apply else '(только расчёт)', flush=True)

                if now >= report_at:
                    print(f'угол={angle:6.1f}° | шарнир={stable_mode or "ожидание"}'
                          f' | IIO={orientation or "ожидание"}'
                          f' | экран={applied or "ожидание"}', flush=True)
                    report_at = now + 3
        finally:
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            if args.apply:
                try:
                    policy(prev_policy)
                    print('Восстановлена политика KDE:', prev_policy, flush=True)
                except Exception as e:
                    print('Не удалось восстановить политику KDE:', e,
                          file=sys.stderr, flush=True)
            print('Автоповорот завершён. Контроллер шарнира не затронут.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        print('Ошибка:', e, file=sys.stderr)
        sys.exit(1)