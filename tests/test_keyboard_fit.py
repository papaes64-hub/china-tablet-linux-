"""Keyboard-area sizing against the user's measured Cinnamon allocation."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'keyboard_fit', Path(__file__).parents[1] / 'scripts/install-keyboard-fit.py')
FIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIT)


class KeyboardFitTests(unittest.TestCase):
    def test_reported_geometry_rotation_reopen_and_disable(self):
        harness = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
class Signals {
    constructor(props = {}) { Object.assign(this, props); this.handlers = new Map(); this.next = 1; }
    connect(name, fn) { const id = this.next++; this.handlers.set(id, {name, fn}); return id; }
    disconnect(id) { this.handlers.delete(id); }
    emit(name) { for (const {name: n, fn} of [...this.handlers.values()]) if (n === name) fn(); }
    set_position(x, y) { this.x = x; this.y = y; }
    set_size(w, h) { this.width = w; this.height = h; }
}
let tasks = new Map(), source = 0, chromeUpdates = 0;
const GLib = {PRIORITY_DEFAULT_IDLE: 200, SOURCE_REMOVE: false,
    idle_add: (_, fn) => { tasks.set(++source, fn); return source; },
    source_remove: id => tasks.delete(id)};
function flush() {
    let passes = 0;
    while (tasks.size) {
        assert.ok(++passes < 12, 'geometry must settle without an idle loop');
        const batch = [...tasks]; tasks.clear();
        for (const [, fn] of batch) fn();
    }
}
const box = new Signals({x: 0, y: 911, width: 768, height: 413, visible: true});
let actor = new Signals({width: 768, height: 176, _currentPage: {width: 594, height: 128}});
const manager = {keyboardActor: actor, getKeyboardPosition: () => 'bottom'};
const layout = new Signals({keyboardBox: box, keyboardMonitor: {x: 0, y: 0, width: 768, height: 1366},
    updateChrome: () => chromeUpdates++,
    _updateKeyboardBox: () => { box.set_position(0, 911); box.set_size(768, 413); }});
const context = vm.createContext({imports: {ui: {main: {layoutManager: layout, virtualKeyboardManager: manager}},
    gi: {GLib}}, global: {logError: error => { throw error; }}, Math, Number});
vm.runInContext(SOURCE, context);
vm.runInContext('enable()', context); flush();
assert.deepEqual([box.width, box.height, box.y], [768, 176, 1148]);
assert.equal(box.y + box.height, 1324, 'bottom edge must stay above the 42px panel');
assert.deepEqual([actor.width, actor.height, actor._currentPage.width, actor._currentPage.height], [768, 176, 594, 128]);

// Native show resets the outer rectangle. A fresh open fits it again.
box.visible = false; box.emit('notify::visible'); flush();
layout._updateKeyboardBox(); box.visible = true; box.emit('notify::visible'); flush();
assert.equal(box.height, 176);

// Landscape retains Cinnamon's original rectangle, even if the actor is taller.
layout.keyboardMonitor = {x: 0, y: 0, width: 1366, height: 768};
box.set_position(0, 512); box.set_size(1366, 214); actor.height = 256;
layout.emit('monitors-changed'); actor.emit('notify::height'); flush();
assert.deepEqual([box.width, box.height, box.y], [1366, 214, 512]);

// Portrait follows a different natural height, including later layout growth.
layout.keyboardMonitor = {x: 0, y: 0, width: 768, height: 1366};
layout._updateKeyboardBox(); actor.height = 200;
layout.emit('monitors-changed'); actor.emit('notify::height'); flush();
assert.deepEqual([box.height, box.y], [200, 1124]);
actor.height = 220; actor.emit('notify::height'); flush();
assert.deepEqual([box.height, box.y], [220, 1104]);

// The mode controller destroys/recreates the keyboard; follow the new actor.
const previousActor = actor;
actor.emit('destroy');
actor = new Signals({width: 768, height: 176}); manager.keyboardActor = actor;
layout._updateKeyboardBox(); box.emit('notify::visible'); flush();
assert.equal(previousActor.handlers.size, 0);
assert.deepEqual([box.height, box.y], [176, 1148]);

// Do not resize screensaver, top-positioned or unallocated keyboards.
layout._updateKeyboardBox(); manager._screensaverMode = true;
box.emit('notify::visible'); flush(); assert.equal(box.height, 413);
manager._screensaverMode = false; manager.getKeyboardPosition = () => 'top';
box.emit('notify::visible'); flush(); assert.equal(box.height, 413);
manager.getKeyboardPosition = () => 'bottom'; actor.height = 0;
actor.emit('notify::height'); flush(); assert.equal(box.height, 413);
actor.height = 176; actor.emit('notify::height'); flush(); assert.equal(box.height, 176);

// Disable cancels pending work, disconnects signals and restores native sizing.
actor.emit('notify::height');
vm.runInContext('disable()', context); flush();
assert.deepEqual([box.height, box.y], [413, 911]);
assert.equal(box.handlers.size + layout.handlers.size + actor.handlers.size, 0);
assert.ok(chromeUpdates >= 6);
console.log('Portrait sizing, panel clearance, reopen, rotation, actor replacement and disable: OK');
'''
        script = 'const SOURCE = ' + json.dumps(FIT.EXTENSION_JS) + ';\n' + harness
        result = subprocess.run(['node'], input=script, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reads_cinnamon_eval_boolean_and_object(self):
        with patch.object(FIT, 'run', return_value="(true, 'true')"):
            self.assertIs(FIT.eval_cinnamon('test'), True)
        with patch.object(FIT, 'run', return_value="(true, '{\"height\":176}')"):
            self.assertEqual(FIT.eval_cinnamon('test'), {'height': 176})
        with patch.object(FIT, 'run', return_value="(false, 'failure')"):
            with self.assertRaises(RuntimeError):
                FIT.eval_cinnamon('test')

    def test_only_changes_our_extension_entry(self):
        entries = ['other-extension', '!' + FIT.UUID, 'another-extension']
        with patch.object(FIT, 'enabled_extensions', return_value=entries), patch.object(FIT, 'run') as run:
            FIT.set_enabled(True)
            self.assertEqual(json.loads(run.call_args.args[0][-1]), ['other-extension', 'another-extension', FIT.UUID])
            FIT.set_enabled(False)
            self.assertEqual(json.loads(run.call_args.args[0][-1]), ['other-extension', 'another-extension'])


if __name__ == '__main__':
    unittest.main()
