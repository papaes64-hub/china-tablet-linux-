"""Regression checks without a running Cinnamon session or hardware changes."""
import copy
import fcntl
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'keyboard_fix', Path(__file__).parents[1] / 'scripts/install-cinnamon-keyboard.py')
FIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIX)

APPLETS = ['panel1:left:0:menu@cinnamon.org:0',
           'panel1:left:1:separator@cinnamon.org:1',
           'panel1:left:2:grouped-window-list@cinnamon.org:2',
           'panel1:right:5:keyboard@cinnamon.org:8',
           'panel1:right:11:cornerbar@cinnamon.org:14']


class KeyboardFixTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='tablet-keyboard-test-')
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)

    def test_panel_preserves_existing_and_reuses_id(self):
        tablet, next_id, instance_id = FIX.panel_entries(APPLETS, True, 15)
        self.assertEqual([entry for entry in tablet if not FIX.is_keyboard_entry(entry)], APPLETS)
        self.assertEqual((next_id, instance_id), (16, 15))
        self.assertEqual(FIX.panel_entries(tablet, True, next_id, instance_id)[0], tablet)
        laptop, next_id, instance_id = FIX.panel_entries(tablet, False, next_id, instance_id)
        self.assertEqual(laptop, APPLETS)
        again, next_id, same_id = FIX.panel_entries(laptop, True, next_id, instance_id)
        self.assertEqual(again, tablet)
        self.assertEqual(same_id, 15)
        with_collision = laptop + ['panel1:right:12:new-user-applet:15']
        changed, _, allocated = FIX.panel_entries(with_collision, True, next_id, instance_id)
        self.assertEqual(allocated, 16)
        self.assertIn('panel1:right:12:new-user-applet:15', changed)

    def test_real_shell_mode_transitions_do_not_reopen_on_rotation(self):
        original = Path(__file__).with_name('autorotate-before.sh').read_text()
        converted = FIX.patch_autorotate(original)
        self.assertEqual(converted, FIX.patch_autorotate(converted))
        old_noop = FIX.replace_function(original, 'onboard_start', 'onboard_start() { :; }')
        self.assertEqual(converted, FIX.patch_autorotate(old_noop))
        self.assertEqual(original[original.index('    case "$orientation" in'):],
                         converted[converted.index('    case "$orientation" in'):])
        self.assertNotIn('org.onboard', converted)
        helper = self.root / '.local/bin/linux-tablet-keyboard'
        helper.parent.mkdir(parents=True)
        (self.root / '.cache').mkdir()
        events = self.root / 'keyboard-events'
        helper.write_text('#!/bin/bash\nprintf "%s\\n" "$1" >> "' + str(events) + '"\n')
        helper.chmod(0o755)
        converted = converted.replace('$HOME', str(self.root))
        converted = converted.replace('${XDG_RUNTIME_DIR:-/tmp}', str(self.root))
        prefix = '''
gsettings() { :; }
xrandr() { :; }
xinput() {
    if [ "$1" = list ]; then
        printf '%s\\n' 'silead_ts_unfolded touchscreen' 'HTIX Touchpad'
    fi
}
setxkbmap() { :; }
stdbuf() { shift 2; "$@"; }
monitor-sensor() {
    printf '%s\\n' '  orientation: bottom-up' \
        '  Accelerometer orientation changed: normal' \
        '  Accelerometer orientation changed: left-up' \
        '  Accelerometer orientation changed: right-up' \
        '  Accelerometer orientation changed: right-up' \
        '  Accelerometer orientation changed: bottom-up'
}
'''
        subprocess.run(['bash'], input=prefix + converted, text=True, check=True,
                       capture_output=True, timeout=10)
        self.assertEqual(events.read_text().splitlines(), ['laptop', 'tablet', 'laptop'])

    def test_mode_and_restore_preserve_later_unrelated_applet(self):
        state_path = self.root / 'state.json'
        helper_path = self.root / 'helper'
        autorotate_path = self.root / 'autorotate'
        model = {(FIX.DESKTOP, 'enabled-applets'): copy.deepcopy(APPLETS),
                 (FIX.DESKTOP, 'next-applet-id'): 15,
                 (FIX.KEYBOARD, 'activation-mode'): 'accessible',
                 (FIX.KEYBOARD, 'keyboard-position'): 'top',
                 (FIX.A11Y, 'screen-keyboard-enabled'): False}
        saved = [{'schema': schema, 'key': key, 'value': copy.deepcopy(model[schema, key])}
                 for schema, key in FIX.BACKUP_KEYS]
        backup = self.root / 'backup'
        backup.mkdir()
        (backup / 'settings.json').write_text(json.dumps(saved))
        (backup / 'autorotate.before').write_text('original script\n')
        (backup / 'helper.before').write_text('previous helper\n')
        def get(schema, key):
            return copy.deepcopy(model[schema, key])
        def set_value(schema, key, value):
            model[schema, key] = copy.deepcopy(value)
        with patch.object(Path, 'home', return_value=self.root), \
                patch.object(FIX, 'STATE', state_path), patch.object(FIX, 'HELPER', helper_path), \
                patch.object(FIX, 'AUTOROTATE', autorotate_path), \
                patch.object(FIX, 'get_setting', side_effect=get), \
                patch.object(FIX, 'set_setting', side_effect=set_value), \
                patch.object(FIX, 'refresh_keyboard') as refresh, \
                patch.object(FIX.subprocess, 'run'), patch.object(FIX, 'stop_autorotate'), \
                patch.object(FIX, 'start_autorotate') as start, \
                patch.object(FIX, 'root_install', side_effect=lambda a, b: shutil.copy2(a, b)):
            FIX.set_mode('tablet')
            self.assertTrue(model[FIX.A11Y, 'screen-keyboard-enabled'])
            self.assertEqual(model[FIX.KEYBOARD, 'activation-mode'], 'on-demand')
            self.assertEqual(model[FIX.KEYBOARD, 'keyboard-position'], 'bottom')
            self.assertTrue(any(FIX.is_keyboard_entry(e) for e in model[FIX.DESKTOP, 'enabled-applets']))
            FIX.set_mode('laptop')
            self.assertFalse(model[FIX.A11Y, 'screen-keyboard-enabled'])
            self.assertEqual(model[FIX.DESKTOP, 'enabled-applets'], APPLETS)
            self.assertEqual(model[FIX.KEYBOARD, 'activation-mode'], 'on-demand')
            FIX.set_mode('tablet')
            new_entry = 'panel1:right:12:later-user-applet:20'
            model[FIX.DESKTOP, 'enabled-applets'].append(new_entry)
            FIX.restore(backup)
            self.assertEqual(model[FIX.DESKTOP, 'enabled-applets'], APPLETS + [new_entry])
            self.assertEqual(model[FIX.KEYBOARD, 'activation-mode'], 'accessible')
            self.assertEqual(model[FIX.KEYBOARD, 'keyboard-position'], 'top')
            self.assertEqual(autorotate_path.read_text(), 'original script\n')
            self.assertEqual(helper_path.read_text(), 'previous helper\n')
            self.assertFalse(state_path.exists())
            self.assertEqual(refresh.call_count, 4)
            start.assert_called_once()

    def test_rejects_unrecognized_source_without_partial_patch(self):
        with self.assertRaises(RuntimeError):
            FIX.patch_autorotate('#!/bin/bash\necho unknown\n')

    def test_stop_targets_only_autorotate_and_its_children(self):
        table = {101: (1, 'start-a', ['bash', str(FIX.AUTOROTATE)]),
                 102: (101, 'start-b', ['monitor-sensor']),
                 103: (102, 'start-c', ['child-monitor']),
                 104: (1, 'start-d', ['bash', '/different/script']),
                 105: (1, 'start-e', ['libreoffice', 'sheet.ods'])}
        # Only the first inventory contains live tasks; later snapshots simulate
        # their exit, while ensuring unrelated applications are never signalled.
        with patch.object(FIX, 'proc_table', side_effect=[table, table, {}, {}, {}]), \
                patch.object(FIX.os, 'kill') as kill, patch.object(FIX.subprocess, 'run'):
            FIX.stop_autorotate()
        self.assertEqual({call.args[0] for call in kill.call_args_list}, {101, 102, 103})

    def test_restart_cleanup_releases_inherited_flock(self):
        script = self.root / 'autorotate-lock-test.sh'
        lockfile = self.root / 'autorotate.lock'
        script.write_text('#!/bin/bash\nexec 9>"' + str(lockfile) + '"\n'
                          'flock -n 9 || exit 1\n'
                          'while read -r line; do :; done < <(sleep 30)\n')
        process = subprocess.Popen(['bash', str(script)])
        try:
            deadline = time.monotonic() + 3
            acquired = False
            while time.monotonic() < deadline:
                if lockfile.exists():
                    with lockfile.open('a') as lock:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            fcntl.flock(lock, fcntl.LOCK_UN)
                        except BlockingIOError:
                            acquired = True
                            break
                time.sleep(0.02)
            self.assertTrue(acquired)
            with patch.object(FIX, 'AUTOROTATE', script), patch.object(FIX.subprocess, 'run'):
                FIX.stop_autorotate()
            process.wait(timeout=3)
            with lockfile.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


if __name__ == '__main__':
    unittest.main()
