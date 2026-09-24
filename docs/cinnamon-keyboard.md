# Manual Cinnamon keyboard in tablet mode

This optional migration targets the N4020 project's existing Linux Mint 22.3,
Cinnamon 6.6.9, X11 installation and its `linux-tablet-autorotate` script.
The owner has tested the mode switching and manual activation on the device
and confirmed that the logic works. The remaining reported issue is excess
space reserved around the keyboard in portrait orientation.

Before the migration, Onboard still launched in tablet mode, Cinnamon's
keyboard briefly appeared on input focus, and no keyboard button was present.
The migration addressed that behavior; its confirmed logic is kept intact by
the separate height adjustment below.

## Confirmed mode behavior

- Laptop mode: the keyboard is closed and the keyboard applet is absent.
- Tablet mode: Cinnamon's built-in keyboard applet appears on the left side
  of panel 1. A tap toggles the keyboard; focusing a text field or spreadsheet
  cell does not open it.
- Rotation between tablet orientations does not rerun keyboard setup.
- Returning to laptop mode closes the keyboard and removes the applet.

The migration removes Onboard launch and geometry commands from the installed
script. It keeps the existing screen rotation, touch matrices, physical-input
switching, sensor-output parsing and unrelated UI settings. The existing
orientation-to-mode mapping is retained; this does not add hinge-angle
detection.

## Install

Run from a terminal in the user's Cinnamon X11 session:

```bash
python3 scripts/install-cinnamon-keyboard.py install
```

Run the installer as the desktop user. It asks for `sudo` authentication when
replacing the system autorotate script. The native Cinnamon keyboard applet
and expected keyboard API must already be available; preflight checks reject
an incompatible installation before making changes.

The installer saves the original script, affected settings and any previous
helper under `~/.local/share/linux-tablet/backups/keyboard-*`. It installs a
per-user helper at `~/.local/bin/linux-tablet-keyboard`, patches the installed
script, terminates the old autorotate process and its child processes, and
starts the updated script in the same desktop session. Onboard is stopped.
No logout is requested by the installer.

The applet is added to the existing panel list. Other applets keep their
entries and IDs; the keyboard applet reuses its own instance ID across folds.

## Portrait keyboard height

The owner supplied these live measurements with the keyboard open:

| Element | Width | Height |
| --- | ---: | ---: |
| Portrait screen | 768 | 1366 |
| Reserved keyboard area | 768 | 413 |
| Keyboard actor | 768 | 176 |
| Current key grid | 594 | 128 |

The empty outer area is 237 pixels taller than the keyboard. An optional
per-user Cinnamon extension fits this outer area to the keyboard actor's
actual height, preserving the bottom edge above the panel. It follows opening,
rotation and keyboard-actor replacement. It only adjusts bottom-positioned
keyboards on portrait screens; key dimensions and focus behavior are not
changed.

```bash
python3 scripts/install-keyboard-fit.py install
```

This installer needs no `sudo` and does not patch the working mode helper or
autorotate script. It installs the extension
`linux-tablet-keyboard-fit@linux-tablet` under the user's Cinnamon extensions
directory and enables it through Cinnamon settings. It saves a backup and
prints a restore command. Disabling the extension restores Cinnamon's native
area calculation, while keeping the manual keyboard controls.

The geometry adjustment passes automated checks using the supplied dimensions;
visual confirmation on the tablet is still pending. After installing, close
and reopen the keyboard in portrait mode and check the gap, typing, rotation
and return to laptop mode. The original size diagnostic should then show the
outer area close to the keyboard actor's height.

## Check on the device

1. Enter tablet mode: one keyboard icon should appear; Onboard should stay
   closed. Touch a LibreOffice cell: no keyboard should appear by itself.
2. Tap the icon, type, then hide the keyboard. Check whether the panel icon
   remains reachable while the keyboard is open; the native keyboard also
   has its own hide key.
3. Rotate through the tablet orientations. Check the keyboard position and
   touch alignment, then return to laptop mode: keyboard and icon should hide,
   and the physical keyboard and touchpad should work.

```bash
~/.local/bin/linux-tablet-keyboard status
tail -n 25 ~/.cache/linux-tablet-autorotate-session.log
```

The installer prints an exact restore command using the backup directory it
created. Restore recovers the old script and keyboard settings while retaining
unrelated applets added after installation. It also restores the earlier
Onboard behavior if that was present in the backup.

## Validation and implementation references

The original six automated checks cover real Bash mode transitions with simulated sensor
output, repeated rotation, the earlier no-op Onboard patch, preservation and
reuse of applet IDs, recovery from backup, rejection of unknown scripts, and
cleanup of a real child process holding the autorotate lock. They do not test
the physical display, touch focus or a running Cinnamon session. Three further
tests cover the geometry extension, Cinnamon Eval result parsing and preservation
of unrelated enabled extensions. The geometry scenarios include the measured
413-to-176 pixel reduction, panel clearance, reopening, both rotation directions,
new keyboard actors, layout growth, safe exclusions and disabling the extension.

```bash
python3 -m unittest discover -s tests -v
```

Upstream implementation references:

- [Cinnamon's native keyboard applet](https://github.com/linuxmint/cinnamon/blob/master/files/usr/share/cinnamon/applets/on-screen-keyboard@cinnamon.org/applet.js)
- [Virtual keyboard manager](https://github.com/linuxmint/cinnamon/blob/master/js/ui/virtualKeyboard.js)
- [Keyboard accessibility settings](https://github.com/linuxmint/cinnamon/blob/master/files/usr/share/cinnamon/cinnamon-settings/modules/cs_accessibility.py)

The helper sets `activation-mode` to `on-demand`. At a mode transition it calls
the keyboard manager's existing settings-change handler once, then closes
the keyboard. This rebuilds stale focus listeners without replacing Cinnamon
methods or editing Cinnamon's installed source. The installer checks that the
required methods exist before patching the autorotate script.
