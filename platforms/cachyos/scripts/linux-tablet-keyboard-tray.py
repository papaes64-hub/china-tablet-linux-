#!/usr/bin/env python3
"""Linux Tablet | CachyOS Plasma 6.8 | keyboard only while folded.

Independent tray UI: reads the verified hinge sensor math but never changes
physical keyboard, touchpad, rotation, Silead filtering, or Waydroid.

Plasma 6.8 virtual keyboard uses DBus 'mode' (int):
  0 = Never; 1 = Touch and Tablet; 2 = Touch, Tablet, Mouse.
In laptop mode set 0, in tablet mode set 1 on transition.
While in tablet mode, one click on the tray icon toggles 0 / 1.
"""

import fcntl
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import time

HINGE = Path.home() / '.local/bin/linux-tablet-hinge-kde'
KWIN = 'org.kde.KWin'
PATH = '/VirtualKeyboard'
IFACE = 'org.kde.kwin.VirtualKeyboard'
DEBOUNCE = 0.70
ANGLE_LIMIT = 190.0


def bus(*args):
    r = subprocess.run(['busctl', '--user', *args], capture_output=True,
                       text=True, timeout=3)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout.strip()


def get_mode():
    data = bus('get-property', KWIN, PATH, IFACE, 'mode')
    match = re.fullmatch(r'i\s+(-?\d+)', data)
    if not match:
        raise RuntimeError(f'Неожиданное свойство mode: {data!r}')
    return int(match.group(1))


def set_mode(value):
    if value == 0:
        # Retract an already displayed keyboard before disabling auto-show.
        bus('set-property', KWIN, PATH, IFACE, 'active', 'b', 'false')
    bus('set-property', KWIN, PATH, IFACE, 'mode', 'i', str(value))
    if get_mode() != value:
        raise RuntimeError('KWin не сохранил переключение mode')


def load_hinge():
    if not HINGE.is_file():
        raise RuntimeError(f'Не найден проверенный контроллер: {HINGE}')
    loader = importlib.machinery.SourceFileLoader('linux_tablet_hinge_used', str(HINGE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def main():
    hinge = load_hinge()
    display = hinge.sensor('i2c-KIOX020A:00')
    base = hinge.sensor('i2c-KIOX010A:00')

    if '--check' in sys.argv:
        angle = hinge.hinge_angle(display, base)
        print(f'Угол: {angle:.1f}°; ожидаемый режим:',
              'TABLET' if angle >= ANGLE_LIMIT else 'LAPTOP')
        print('KWin VirtualKeyboard.mode:', get_mode(), '(0=выкл, 1=сенсор)')
        print('Проверка выполнена без изменений.')
        return 0

    # Only one instance should manage keyboard policies.
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp'))
    lock = open(runtime / f'linux-tablet-keyboard-tray-{os.getuid()}.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Переключатель виртуальной клавиатуры уже запущен')

    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

    app = QApplication(['Linux Tablet Keyboard'])
    app.setQuitOnLastWindowClosed(False)

    icon_fallback = app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
    icon_on = QIcon.fromTheme('input-keyboard-virtual-show-symbolic', icon_fallback)
    icon_off = QIcon.fromTheme('input-keyboard-virtual-off-symbolic', icon_fallback)

    tray = QSystemTrayIcon(icon_on, app)
    tray.setToolTip('Linux Tablet: клавиатура планшета')
    tray.setVisible(False)

    menu = QMenu()
    toggle_action = menu.addAction('Включить/выключить экранную клавиатуру')
    menu.addSeparator()
    quit_action = menu.addAction('Закрыть значок')
    tray.setContextMenu(menu)

    stable = None
    pending = None
    since = 0.0
    last_status = 0.0
    last_error = ''

    def report_error(exc):
        nonlocal last_error
        message = str(exc)
        if message != last_error:
            print('Ошибка:', message, file=sys.stderr, flush=True)
            last_error = message

    def update_icon():
        try:
            mode = get_mode()
            on = mode != 0
            tray.setIcon(icon_on if on else icon_off)
            tray.setToolTip('Linux Tablet — клавиатура ' +
                            ('ВКЛ (нажмите для отключения)' if on else 'ВЫКЛ (нажмите для включения)'))
            toggle_action.setText('Отключить клавиатуру' if on else 'Включить клавиатуру')
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            report_error(exc)

    def toggle():
        if stable != 'TABLET':
            return
        try:
            mode = get_mode()
            set_mode(0 if mode else 1)
            update_icon()
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            report_error(exc)

    def on_activated(reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            toggle()

    def tick():
        nonlocal stable, pending, since, last_status, last_error
        try:
            angle = hinge.hinge_angle(display, base)
            current = 'TABLET' if angle >= ANGLE_LIMIT else 'LAPTOP'
            now = time.monotonic()
            if current == stable:
                pending = None
            elif current != pending:
                pending, since = current, now
            elif now - since >= DEBOUNCE:
                # Change keyboard configuration only at the stable hinge transition.
                set_mode(1 if current == 'TABLET' else 0)
                stable, pending = current, None
                tray.setVisible(stable == 'TABLET')
                print(f'>>> {stable}  {angle:.1f}°  virtual keyboard mode={get_mode()}', flush=True)
                update_icon()

            if stable == 'TABLET' and now - last_status > 2:
                update_icon()   # reflects changes in Plasma's own widget
                last_status = now
            elif stable == 'LAPTOP' and now - last_status > 3:
                # Enforce laptop mode even if a user changed Plasma's setting.
                if get_mode() != 0:
                    set_mode(0)
                last_status = now
            last_error = ''
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            report_error(exc)

    tray.activated.connect(on_activated)
    toggle_action.triggered.connect(toggle)
    quit_action.triggered.connect(app.quit)
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(300)
    tick()
    print('Linux Tablet Keyboard: запущено; значок только при угле ≥ 190°', flush=True)
    return app.exec()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'Ошибка: {exc}', file=sys.stderr)
        sys.exit(1)