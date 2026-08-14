# Bonus: rescuing Windows after an "optimizer" — without booting into Windows

A separate story, not directly about the touchscreen, but it happened during the
same project and is useful to many. Handy if Windows, after a "registry cleanup",
an update "repository" reset, or a feature update, boots into a **gray/black
screen with a live mouse cursor but no desktop**.

> Disclaimer: editing someone's registry is at your own risk. Back up anything
> valuable first, then fix. We don't delete anything — only look, and adjust
> values if needed.

---

## What "gray screen with a cursor" means

The system booted, the session logged in (the cursor moves, Ctrl+Alt+Del may
open Task Manager), but the **`explorer.exe` shell didn't start**. So Windows is
mostly intact — something on the path to launching the shell is broken. Almost
always this is the result of "optimizers" that edit the registry and system
services.

The good news: the Windows partition is readable from Linux, and the registry
can be checked and fixed **from the outside**, without booting into Windows.

---

## The tool

From any Linux (e.g. a second system in a dual-boot):

```bash
sudo apt install chntpw ntfs-3g
```

The Windows partition must be **unmounted**, otherwise edits won't be saved:

```bash
lsblk -f                                   # find the Windows NTFS partition (e.g. /dev/sda3)
sudo umount /dev/sda3                       # if mounted
sudo mkdir -p /mnt/win
sudo ntfs-3g -o remove_hiberfile /dev/sda3 /mnt/win
```

(`remove_hiberfile` is needed if Windows shut down "dirty".)

---

## Check in order (from common to rare)

The registry hives are in `/mnt/win/Windows/System32/config/`.
Open the `SOFTWARE` hive:

```bash
sudo chntpw -e /mnt/win/Windows/System32/config/SOFTWARE
```

A `>` prompt appears. **Enter one command at a time** (pasting several lines at
once makes them merge and fail).

### 1. Shell and logon (the most common)

```
cd Microsoft\Windows NT\CurrentVersion\Winlogon
cat Shell
cat Userinit
```

Correct values:
- `Shell` = `explorer.exe`
- `Userinit` = `C:\Windows\system32\userinit.exe,` (trailing comma is normal)

If there's garbage, emptiness, or a foreign path — fix it with `ed Shell` (or
`ed Userinit`) and type the correct value.

### 2. Hijack via IFEO (a common trick of "cleaners" and malware)

```
cd \Microsoft\Windows NT\CurrentVersion\Image File Execution Options
ls
```

The list should **not** contain a subkey `explorer.exe` (or `userinit.exe`) with
a `Debugger` value inside. If it does — that's a hijack, and the shell silently
won't start. Stock entries (iexplore.exe, office, etc.) are normal.

### 3. Policies that block the shell

```
cd \Microsoft\Windows\CurrentVersion\Policies\System
ls
```

Healthy content is UAC parameters (`EnableLUA`, `ConsentPrompt...`). Nothing
extra like desktop restrictions should be there.

Exit: `q` (answer `y` to save if you edited; `n` if you only looked).

---

## If the registry is clean but the screen is gray

Then check the files themselves (with regular Linux commands):

```bash
# is the shell intact
ls -la /mnt/win/Windows/explorer.exe        # should be ~4-5 MB, not 0

# a stuck update?
ls -la /mnt/win/Windows/WinSxS/pending.xml  # if present — the transaction didn't finish

# tail of the component servicing log — where it stumbled
tail -40 /mnt/win/Windows/Logs/CBS/CBS.log
```

- **explorer.exe is zero/missing** — replace it with a copy from
  `/mnt/win/Windows/WinSxS/` (backup copies of components live there).
- **pending.xml exists** — a stuck update; fix it from Windows' own recovery
  environment: `DISM /Image:C:\ /Cleanup-Image /RevertPendingActions`.
- **WMI repository was reset** (a common side effect of Update "fixers") — files
  in `Windows/System32/wbem/Repository/` are recreated; some services may fail to
  start until WMI restores its registrations.

---

## The simplest path if you can launch the shell manually

If Ctrl+Alt+Del opens Task Manager, you may not need the registry at all:

1. Task Manager → **File → Run new task** → **Browse…** button (with the mouse,
   no keyboard — handy if the logon keyboard layout is stuck on a non-Latin one).
2. Via Browse, reach `C:\Windows\explorer.exe` → Open → the desktop comes up.
3. From there launch **System Restore** (`C:\Windows\System32\rstrui.exe`) and
   pick a restore point **before** the "optimizer" ran — it rolls back the
   registry and files as a whole. Don't forget the "Show more restore points"
   checkbox.
4. Or in `cmd.exe` (run as administrator) run: `sfc /scannow`, then
   `DISM /Online /Cleanup-Image /RestoreHealth`, and with a reset WMI —
   `winmgmt /salvagerepository`.

---

## Moral

"Optimizers" and aggressive "registry cleaning" on a live system are a common
cause of exactly this kind of breakage. On weak hardware Windows isn't fast even
without "accelerators" — but breaking it with them is easy. If the tablet is
moving to Linux anyway — maybe that was the sign.
