#!/usr/bin/env python3
"""Install the portrait keyboard-height fix for LinuxTablet / Cinnamon 6.6."""
import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

UUID = 'linux-tablet-keyboard-fit@linux-tablet'
EXTENSION_DIR = Path.home() / '.local/share/cinnamon/extensions' / UUID
METADATA = {
    'uuid': UUID,
    'name': 'LinuxTablet: высота клавиатуры',
    'description': 'Убирает лишнее место над экранной клавиатурой в вертикальном положении.',
    'cinnamon-version': ['6.6'],
    'version': 1,
    'url': 'https://github.com/papaes64-hub/china-tablet-linux-'
}

EXTENSION_JS = r'''const Main = imports.ui.main;
const GLib = imports.gi.GLib;

let controller = null;

class KeyboardFit {
    constructor() {
        this.layout = Main.layoutManager;
        this.box = this.layout.keyboardBox;
        this.actor = null;
        this.actorSignals = [];
        this.signals = [];
        this.pending = 0;
        this.destroyed = false;
        this.fitting = false;
        this.lastResult = null;

        this.signals.push([this.box, this.box.connect('notify::visible', () => this.queue())]);
        this.signals.push([this.layout, this.layout.connect('monitors-changed', () => this.queue())]);
        this.queue();
    }

    queue() {
        if (this.destroyed || this.pending || this.fitting)
            return;
        this.pending = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this.pending = 0;
            try {
                this.fit();
            } catch (error) {
                global.logError(error);
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    disconnectActor() {
        for (const [object, id] of this.actorSignals) {
            try { object.disconnect(id); } catch (error) { /* Actor may be destroyed. */ }
        }
        this.actorSignals = [];
        this.actor = null;
    }

    watchActor(actor) {
        if (this.actor === actor)
            return;
        this.disconnectActor();
        this.actor = actor;
        if (!actor)
            return;
        this.actorSignals.push([actor, actor.connect('notify::height', () => this.queue())]);
        this.actorSignals.push([actor, actor.connect('destroy', () => {
            if (this.actor === actor)
                this.disconnectActor();
        })]);
    }

    fit() {
        const manager = Main.virtualKeyboardManager;
        const actor = manager ? manager.keyboardActor : null;
        this.watchActor(actor);
        const monitor = this.layout.keyboardMonitor;
        if (!this.box.visible || !actor || !monitor || manager._screensaverMode || actor._screensaverMode)
            return;
        // The measured defect is a tall, mostly empty box in portrait mode.
        // Keep Cinnamon's normal layout for landscape and top-positioned OSKs.
        if (monitor.height <= monitor.width || manager.getKeyboardPosition() !== 'bottom')
            return;
        const height = Math.ceil(actor.height);
        if (!Number.isFinite(height) || height <= 0 || height > monitor.height / 2)
            return;
        const bottom = this.box.y + this.box.height;
        const y = bottom - height;
        if (!Number.isFinite(y) || y < monitor.y || bottom > monitor.y + monitor.height + 1)
            return;
        if (Math.abs(this.box.height - height) < 1)
            return;

        this.fitting = true;
        try {
            const previousHeight = this.box.height;
            // Preserve the box's existing bottom edge, including panel clearance.
            // The key grid, keyboard actor, focus handlers and visibility stay intact.
            this.box.set_position(this.box.x, y);
            this.box.set_size(this.box.width, height);
            this.layout.updateChrome();
            this.lastResult = {before: previousHeight, after: height, bottom};
        } finally {
            this.fitting = false;
        }
    }

    destroy() {
        this.destroyed = true;
        if (this.pending) {
            GLib.source_remove(this.pending);
            this.pending = 0;
        }
        this.disconnectActor();
        for (const [object, id] of this.signals)
            object.disconnect(id);
        this.signals = [];
        this.layout._updateKeyboardBox();
        this.layout.updateChrome();
    }
}

function init() {}

function enable() {
    if (controller)
        controller.destroy();
    controller = new KeyboardFit();
    return controller;
}

function disable() {
    if (controller)
        controller.destroy();
    controller = null;
}
'''


def run(args):
    return subprocess.run(args, text=True, capture_output=True, check=True).stdout.strip()


def enabled_extensions():
    raw = run(['gsettings', 'get', 'org.cinnamon', 'enabled-extensions'])
    return ast.literal_eval(raw[4:] if raw.startswith('@as ') else raw)


def is_ours(entry):
    return entry.lstrip('!') == UUID


def set_enabled(enabled):
    current = enabled_extensions()
    updated = [entry for entry in current if not is_ours(entry)]
    if enabled:
        updated.append(UUID)
    if updated != current:
        run(['gsettings', 'set', 'org.cinnamon', 'enabled-extensions', json.dumps(updated)])


def eval_cinnamon(code):
    result = run(['gdbus', 'call', '--session', '--dest', 'org.Cinnamon',
                  '--object-path', '/org/Cinnamon', '--method', 'org.Cinnamon.Eval', code])
    if not result.startswith('(true,'):
        raise RuntimeError(result)
    # The second member is JSON text, as returned by Cinnamon's Eval interface.
    quoted = result[len('(true,'):].strip()
    if quoted.endswith(')'):
        quoted = quoted[:-1].strip()
    return json.loads(ast.literal_eval(quoted))


def running():
    return eval_cinnamon('imports.ui.extensionSystem.runningExtensions.indexOf(' + json.dumps(UUID) + ') >= 0')


def wait_for_state(expected):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if running() == expected:
            return
        time.sleep(0.1)
    raise RuntimeError('Cinnamon не подтвердил ' + ('загрузку' if expected else 'отключение') + ' расширения.')


def restore(backup):
    backup = Path(backup).resolve()
    state = json.loads((backup / 'state.json').read_text())
    set_enabled(False)
    wait_for_state(False)
    if EXTENSION_DIR.exists():
        shutil.rmtree(EXTENSION_DIR)
    if state['had_files']:
        shutil.copytree(backup / 'extension.before', EXTENSION_DIR)
    set_enabled(state['was_enabled'])
    if state['was_enabled']:
        wait_for_state(True)
    print('Восстановлена прежняя настройка высоты клавиатуры.')


def install():
    if os.environ.get('XDG_SESSION_TYPE') != 'x11' or not os.environ.get('DISPLAY'):
        raise RuntimeError('Запусти из терминала своей сессии Cinnamon X11.')
    version = run(['cinnamon', '--version'])
    if 'Cinnamon 6.6.' not in version and version != 'Cinnamon 6.6':
        raise RuntimeError('Эта версия расширения рассчитана на Cinnamon 6.6: ' + version)
    compatible = eval_cinnamon('(() => { const m = imports.ui.main; '
                               'return !!m.virtualKeyboardManager && !!m.layoutManager.keyboardBox && '
                               'typeof m.layoutManager.updateChrome === "function" && '
                               'typeof m.layoutManager._updateKeyboardBox === "function"; })()')
    if not compatible:
        raise RuntimeError('Не найден ожидаемый интерфейс клавиатуры Cinnamon.')
    if EXTENSION_DIR.exists():
        old_meta = json.loads((EXTENSION_DIR / 'metadata.json').read_text())
        if old_meta.get('uuid') != UUID:
            raise RuntimeError('Каталог расширения занят другим содержимым.')
        if (EXTENSION_DIR / 'extension.js').read_text() == EXTENSION_JS:
            set_enabled(True)
            wait_for_state(True)
            print('Подгонка высоты уже установлена и включена.')
            return
    state = {'had_files': EXTENSION_DIR.exists(),
             'was_enabled': any(is_ours(entry) for entry in enabled_extensions())}
    base = Path.home() / '.local/share/linux-tablet/backups'
    base.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='keyboard-height-' + time.strftime('%Y%m%d-%H%M%S') + '-', dir=base))
    if state['had_files']:
        shutil.copytree(EXTENSION_DIR, backup / 'extension.before')
    (backup / 'state.json').write_text(json.dumps(state) + '\n')
    (backup / 'restore.py').write_bytes(Path(__file__).read_bytes())
    try:
        set_enabled(False)
        wait_for_state(False)
        EXTENSION_DIR.mkdir(parents=True, exist_ok=True)
        (EXTENSION_DIR / 'metadata.json').write_text(json.dumps(METADATA, ensure_ascii=False, indent=2) + '\n')
        (EXTENSION_DIR / 'extension.js').write_text(EXTENSION_JS)
        set_enabled(True)
        wait_for_state(True)
    except Exception:
        restore(backup)
        raise
    print('Подгонка высоты включена. Закрой и снова открой клавиатуру в боковом положении.')
    print('Логика режимов и ручного вызова сохранена.')
    print('Откат: python3 ' + str(backup / 'restore.py') + ' restore ' + str(backup))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='install', choices=['install', 'restore', 'disable'])
    parser.add_argument('backup', nargs='?')
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError('Запусти без sudo, от своего пользователя.')
    if args.action == 'restore':
        if not args.backup:
            parser.error('Нужен каталог резервной копии')
        restore(args.backup)
    elif args.action == 'disable':
        set_enabled(False)
        wait_for_state(False)
        print('Подгонка высоты отключена.')
    else:
        install()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print('Ошибка:', error, file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr)
        sys.exit(1)
