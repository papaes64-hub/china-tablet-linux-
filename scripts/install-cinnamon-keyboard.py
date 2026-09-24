#!/usr/bin/env python3
"""Manual Cinnamon keyboard for the project's Mint/X11 autorotate script.

Run as the desktop user: python3 install-cinnamon-keyboard.py install
The installed copy accepts: laptop, tablet, status, restore BACKUP_DIR.
"""

import argparse
import ast
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

AUTOROTATE = Path('/usr/local/bin/linux-tablet-autorotate')
HELPER = Path.home() / '.local/bin/linux-tablet-keyboard'
STATE = Path.home() / '.config/linux-tablet/keyboard-applet.json'
UUID = 'on-screen-keyboard@cinnamon.org'
PANEL = 'panel1'
KEYBOARD = 'org.cinnamon.keyboard'
A11Y = 'org.cinnamon.desktop.a11y.applications'
DESKTOP = 'org.cinnamon'
BACKUP_KEYS = [(KEYBOARD, 'activation-mode'), (KEYBOARD, 'keyboard-position'),
               (A11Y, 'screen-keyboard-enabled'), (DESKTOP, 'enabled-applets')]
MARKER = '# LinuxTablet manual keyboard integration v1'


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True,
                          **kwargs).stdout.strip()


def get_setting(schema, key):
    raw = run(['gsettings', 'get', schema, key])
    if raw in ('true', 'false'):
        return raw == 'true'
    if raw.startswith('@as '):
        raw = raw[4:]
    return ast.literal_eval(raw)


def set_setting(schema, key, value):
    if get_setting(schema, key) != value:
        run(['gsettings', 'set', schema, key, json.dumps(value, ensure_ascii=False)])


def is_keyboard_entry(entry):
    fields = entry.split(':')
    return len(fields) >= 5 and fields[3] == UUID


def panel_entries(entries, tablet, next_id, preferred_id=None):
    """Preserve unrelated applets and reuse our instance ID across folds."""
    own = [entry for entry in entries if is_keyboard_entry(entry)]
    other = [entry for entry in entries if not is_keyboard_entry(entry)]
    if not tablet:
        return other, next_id, preferred_id
    used = {int(entry.split(':')[4]) for entry in other
            if len(entry.split(':')) >= 5 and entry.split(':')[4].isdigit()}
    existing_id = int(own[0].split(':')[4]) if own else None
    instance_id = existing_id if existing_id is not None else preferred_id
    if not isinstance(instance_id, int) or instance_id < 0 or instance_id in used:
        instance_id = max([next_id, *[value + 1 for value in used]])
    if own and len(own) == 1 and own[0].startswith(PANEL + ':') and existing_id == instance_id:
        return entries, max(next_id, instance_id + 1), instance_id
    # Add one button on the left without reordering existing applets.
    orders = [int(entry.split(':')[2]) for entry in other
              if entry.startswith(PANEL + ':left:') and entry.split(':')[2].isdigit()]
    entry = f'{PANEL}:left:{max(orders, default=-1) + 1}:{UUID}:{instance_id}'
    return other + [entry], max(next_id, instance_id + 1), instance_id


def write_atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def cinnamon_eval(code):
    output = run(['gdbus', 'call', '--session', '--dest', 'org.Cinnamon',
                  '--object-path', '/org/Cinnamon', '--method', 'org.Cinnamon.Eval', code])
    if not output.startswith('(true,'):
        raise RuntimeError('Cinnamon: ' + output)
    return output


def refresh_keyboard():
    # Rebuild once at a mode transition, dropping any old focus listeners.
    # No method is replaced; this is Cinnamon's own settings-change handler.
    cinnamon_eval('(() => { const k = imports.ui.main.virtualKeyboardManager; '
                  'k._keyboardSettingsChanged(); k.close(); return true; })()')


def set_mode(mode):
    cache = Path.home() / '.cache'
    cache.mkdir(exist_ok=True)
    with (cache / 'linux-tablet-keyboard.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        tablet = mode == 'tablet'
        subprocess.run(['pkill', '-u', str(os.getuid()), '-x', 'onboard'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        set_setting(KEYBOARD, 'activation-mode', 'on-demand')
        set_setting(KEYBOARD, 'keyboard-position', 'bottom')
        set_setting(A11Y, 'screen-keyboard-enabled', tablet)
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        entries, next_id, instance_id = panel_entries(
            get_setting(DESKTOP, 'enabled-applets'), tablet,
            get_setting(DESKTOP, 'next-applet-id'), state.get('instance_id'))
        # Reserve the ID before exposing a new applet to Cinnamon.
        set_setting(DESKTOP, 'next-applet-id', next_id)
        set_setting(DESKTOP, 'enabled-applets', entries)
        refresh_keyboard()
        write_atomic(STATE, (json.dumps({'instance_id': instance_id, 'mode': mode}) + '\n').encode())
        print('LinuxTablet keyboard:', mode, '(ручной вызов)')


def replace_function(text, name, replacement):
    # Accept both the original multiline function and the earlier one-line no-op.
    pattern = re.compile(r'(?m)^' + re.escape(name) + r'\(\)\s*\{[^\n]*\n')
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(f'Ожидалась одна функция {name}; найдено {len(matches)}. Файл не изменён.')
    match = matches[0]
    if match.group(0).rstrip().endswith('}'):
        end = match.end()
    else:
        closing = re.search(r'(?m)^}\s*$', text[match.end():])
        if not closing:
            raise RuntimeError('Не найден конец функции ' + name)
        end = match.end() + closing.end()
        if end < len(text) and text[end] == '\n':
            end += 1
    return text[:match.start()] + replacement.rstrip() + '\n' + text[end:]


def patch_autorotate(text):
    if MARKER in text:
        return text
    for expected in ('set_touchpad', 'keyboard_enable', 'keyboard_disable', 'rotate_tablet'):
        if not re.search(r'(?m)^' + expected + r'\(\)', text):
            raise RuntimeError('Неизвестная версия autorotate: нет ' + expected)
    text = replace_function(text, 'onboard_start', '''screen_keyboard_tablet() {
    "$HOME/.local/bin/linux-tablet-keyboard" tablet
}''')
    text = replace_function(text, 'onboard_stop', '''screen_keyboard_laptop() {
    "$HOME/.local/bin/linux-tablet-keyboard" laptop
}''')
    text = replace_function(text, 'set_laptop_mode', '''set_laptop_mode() {
    set_touchpad on
    keyboard_enable
    if [ "$last_mode" != "laptop" ]; then
        set_laptop_ui
        screen_keyboard_laptop && last_mode="laptop"
    fi
}''')
    text = replace_function(text, 'set_tablet_mode', '''set_tablet_mode() {
    set_touchpad off
    keyboard_disable
    if [ "$last_mode" != "tablet" ]; then
        set_tablet_ui
        screen_keyboard_tablet && last_mode="tablet"
    fi
}''')
    # Retain rotation, matrices, sensor parsing and unrelated UI settings verbatim.
    text = re.sub(r'(?m)^\s*gsettings set org\.onboard[^\n]*\n', '', text)
    text = re.sub(r'(?m)^\s*# (?:Возвращаем исходные размеры Onboard\.|Большая клавиатура, закреплённая снизу во всю ширину\.)\n', '', text)
    text, count = re.subn(r'(?m)^last_orientation=""$',
                         'last_orientation=""\nlast_mode=""\n' + MARKER, text)
    if count != 1 or re.search(r'\bonboard_(start|stop)\b|\bnohup onboard\b', text):
        raise RuntimeError('Не удалось однозначно заменить запуск Onboard.')
    run(['bash', '-n'], input=text)
    return text


def proc_table():
    result = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            if path.stat().st_uid != os.getuid():
                continue
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            args = (path / 'cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
            result[int(path.name)] = (int(fields[1]), fields[19], args)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
    return result


def stop_autorotate():
    table = proc_table()
    targets = {pid for pid, (_, _, args) in table.items()
               if args and Path(args[0]).name in ('bash', 'sh') and str(AUTOROTATE) in args[1:3]}
    while True:
        expanded = targets | {pid for pid, (ppid, _, _) in table.items() if ppid in targets}
        if expanded == targets:
            break
        targets = expanded
    for sig in (signal.SIGTERM, signal.SIGKILL):
        current = proc_table()
        for pid in targets:
            if pid in current and current[pid][1] == table[pid][1]:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            current = proc_table()
            if not any(pid in current and current[pid][1] == table[pid][1]
                       and current[pid][2] != [''] for pid in targets):
                break
            time.sleep(0.05)
    # Onboard may have detached from the original shell and inherited its lock.
    subprocess.run(['pkill', '-u', str(os.getuid()), '-x', 'onboard'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def start_autorotate():
    lock_path = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp')) / 'linux-tablet-autorotate.lock'
    with lock_path.open('a') as lock:
        deadline = time.monotonic() + 3
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Старая блокировка autorotate не освободилась.')
                time.sleep(0.05)
    logfile = Path.home() / '.cache/linux-tablet-autorotate-session.log'
    logfile.parent.mkdir(exist_ok=True)
    with logfile.open('a') as log:
        process = subprocess.Popen(['bash', str(AUTOROTATE)], stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True,
                                   close_fds=True)
    time.sleep(0.4)
    if process.poll() is not None:
        raise RuntimeError('Autorotate завершился сразу после запуска: ' + str(logfile))


def root_install(source, destination):
    staged = destination.with_name('.' + destination.name + f'.{os.getpid()}.new')
    run(['sudo', 'install', '-m', '755', str(source), str(staged)])
    run(['sudo', 'mv', '-f', str(staged), str(destination)])


def status():
    state = json.loads(STATE.read_text()) if STATE.exists() else {'mode': 'ещё не определён'}
    print('Режим:', state['mode'])
    print('Вызов:', get_setting(KEYBOARD, 'activation-mode'))
    print('Значок:', 'есть' if any(is_keyboard_entry(e) for e in get_setting(DESKTOP, 'enabled-applets')) else 'скрыт')
    onboard = subprocess.run(['pgrep', '-u', str(os.getuid()), '-x', 'onboard'], capture_output=True)
    print('Onboard:', 'РАБОТАЕТ' if onboard.returncode == 0 else 'не запущена')


def restore(backup):
    backup = Path(backup).resolve()
    saved = json.loads((backup / 'settings.json').read_text())
    subprocess.run(['sudo', '-v'], check=True)
    stop_autorotate()
    root_install(backup / 'autorotate.before', AUTOROTATE)
    for path, name, mode in ((HELPER, 'helper.before', 0o755), (STATE, 'state.before', 0o600)):
        old = backup / name
        if old.exists():
            write_atomic(path, old.read_bytes(), mode)
        else:
            path.unlink(missing_ok=True)
    current = [e for e in get_setting(DESKTOP, 'enabled-applets') if not is_keyboard_entry(e)]
    before = next(item['value'] for item in saved if item['key'] == 'enabled-applets')
    set_setting(DESKTOP, 'enabled-applets', current + [e for e in before if is_keyboard_entry(e)])
    for item in saved:
        if item['key'] != 'enabled-applets':
            set_setting(item['schema'], item['key'], item['value'])
    refresh_keyboard()
    start_autorotate()
    print('Восстановлено из:', backup)


def install():
    if os.environ.get('XDG_SESSION_TYPE') != 'x11' or not os.environ.get('DISPLAY'):
        raise RuntimeError('Запусти установщик из терминала своей сессии Cinnamon X11.')
    applet = Path('/usr/share/cinnamon/applets') / UUID / 'applet.js'
    if not applet.is_file():
        raise RuntimeError('В этой установке не найден штатный значок клавиатуры: ' + str(applet))
    if not any(item.startswith('1:') for item in get_setting(DESKTOP, 'panels-enabled')):
        raise RuntimeError('Не найдена ожидаемая панель 1. Настройки не изменены.')
    check = cinnamon_eval('(() => { const k = imports.ui.main.virtualKeyboardManager; '
                          'return !!k && typeof k.manualToggle === "function" && '
                          'typeof k._keyboardSettingsChanged === "function" && '
                          'typeof k.close === "function"; })()')
    if not re.fullmatch(r"\(true, ['\"]true['\"]\)", check):
        raise RuntimeError('В этой версии Cinnamon отличается интерфейс клавиатуры: ' + check)
    original = AUTOROTATE.read_text()
    patched = patch_autorotate(original)
    saved = [{'schema': schema, 'key': key, 'value': get_setting(schema, key)}
             for schema, key in BACKUP_KEYS]
    subprocess.run(['sudo', '-v'], check=True)
    base = Path.home() / '.local/share/linux-tablet/backups'
    base.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='keyboard-' + time.strftime('%Y%m%d-%H%M%S') + '-', dir=base))
    shutil.copy2(AUTOROTATE, backup / 'autorotate.before')
    for path, name in ((HELPER, 'helper.before'), (STATE, 'state.before')):
        if path.exists():
            shutil.copy2(path, backup / name)
    (backup / 'settings.json').write_text(json.dumps(saved, ensure_ascii=False, indent=2) + '\n')
    (backup / 'autorotate.after').write_text(patched)
    (backup / 'restore.py').write_bytes(Path(__file__).read_bytes())
    print('Резервная копия:', backup, flush=True)
    try:
        write_atomic(HELPER, Path(__file__).read_bytes(), 0o755)
        root_install(backup / 'autorotate.after', AUTOROTATE)
        stop_autorotate()
        set_mode('laptop')
        start_autorotate()
    except Exception:
        print('Установка не завершилась; восстанавливаю прежние файлы и настройки.', file=sys.stderr)
        restore(backup)
        raise
    print('Установлено. В планшетном режиме значок появится слева на панели.')
    print('Нажатие открывает/скрывает клавиатуру. Фокус ввода не должен её вызывать.')
    print('Проверка: ~/.local/bin/linux-tablet-keyboard status')
    print('Отмена: python3 ' + str(backup / 'restore.py') + ' restore ' + str(backup))
    status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='install',
                        choices=['install', 'laptop', 'tablet', 'status', 'restore'])
    parser.add_argument('backup', nargs='?')
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError('Запусти без sudo: python3 install-cinnamon-keyboard.py. Пароль будет запрошен отдельно.')
    if args.action == 'install':
        install()
    elif args.action in ('laptop', 'tablet'):
        set_mode(args.action)
    elif args.action == 'restore':
        if not args.backup:
            parser.error('restore требует каталог резервной копии')
        restore(args.backup)
    else:
        status()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        print('Ошибка:', exc, file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr, file=sys.stderr)
        sys.exit(1)
