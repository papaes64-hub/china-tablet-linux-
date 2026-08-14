# Diagnostics: figuring out what's actually broken

This is the key document of the repository. The ready-made scripts fit **our**
hardware; yours is different, so the diagnosis comes first, and the fix follows
from the diagnosis.

The whole method rests on one idea: **the sensor reports "raw" coordinates, and
you need to see with your own eyes what it reports** when you touch known points
on the screen. From the shape of the distortion it becomes clear what to fix and
how.

---

## Step 1. Identify the sensor

```bash
dmesg | grep -i -E "silead|gsl|mssl|touch"
```

Look for lines like `silead_ts ... Silead chip ID: 0x...` and the ACPI name
(`MSSL1680` or similar). If you have a Silead controller — this repo is relevant
to you. Also check which firmware the driver loads and whether it fails:

```bash
dmesg | grep -i firmware
ls -la /lib/firmware/silead/
```

Find the device in the input system:

```bash
xinput list                      # look for a name, e.g. silead_ts
cat /proc/bus/input/devices | grep -A5 -i silead   # look for eventN
```

Remember the `/dev/input/eventN` number — you'll need it for evtest (it can
change after reboots or reloading the module).

---

## Step 2. Capture the "raw" coordinates

```bash
sudo apt install evtest
sudo evtest /dev/input/eventN
```

In the evtest header, note `ABS_X` / `ABS_Y` — their `Min` and `Max` (the
declared range). Then **tap each of the four corners once**: top-left,
top-right, bottom-left, bottom-right. Write down the `ABS_X` / `ABS_Y` pairs
(the first values after `BTN_TOUCH value 1`).

The key test — **drag your finger slowly, without lifting, across the whole
screen**: first left to right, then top to bottom. Watch how the coordinates
change.

---

## Step 3. Make the diagnosis

Compare what you saw with the table. Diagnoses go from simple to complex.

### A. Mirror or rotation (the easiest)

**Symptom:** coordinates change smoothly and monotonically from edge to edge,
full coverage, but the direction is wrong — finger goes right, cursor goes left
(or axes are swapped: finger right, cursor down).

**Cause:** the sensor is physically rotated/mirrored relative to the screen.

**Fix:** linear, no scripts needed — a transformation matrix:

```bash
xinput set-prop "SENSOR_NAME" "Coordinate Transformation Matrix" <9 numbers>
```

Cheat sheet:
- mirror X: `-1 0 1 0 1 0 0 0 1`
- mirror Y: `1 0 0 0 -1 1 0 0 1`
- rotate 180°: `-1 0 1 0 -1 1 0 0 1`
- swap axes: `0 1 0 1 0 0 0 0 1`

Make it permanent via `/etc/X11/xorg.conf.d/` or (kernel 6.10+) the
`i2c_touchscreen_props` GRUB parameter. The kernel `touchscreen-*` properties do
the same thing.

### B. "Collapsed" range

**Symptom:** touches live in a small patch in a corner/center, never reaching
the screen edges; or there is coverage but it's narrow (finger across the whole
screen — cursor across a third).

**Cause:** the firmware expects a matrix of a different size/resolution.

**Fix:** find the right firmware (Step 4). A scaling matrix can partly help if
the distortion is strictly linear (clean corners, predictable center), but
usually firmware is needed.

### C. "Folded" axis (our case)

**Symptom:** when dragging across the whole screen, the coordinate grows up to
the middle and then **reverses and shrinks** — as if half the screen is
mirrored. Corners may look fine while the middle "falls out". Often the left
half of the screen gives one range and the right half a mirrored one.

**Cause:** the firmware's channel count for that axis doesn't match the sensor
matrix — the controller lays out touches "in halves".

**Fix:** first try firmware (Step 4). If **none** gives a whole axis, but one
gives a **clean** fold (no doubling, each screen point maps to a single value),
the axis can be unfolded in software with a coordinate handler (our
`silead-unfold.py`, see Step 5).

### D. Phantom touches (a disqualifier)

**Symptom:** a single physical touch produces **two points** in different places
(in evtest a single tap shows a second `ABS_MT_SLOT` with its own
`TRACKING_ID`). Especially visible when holding a finger still.

**Cause:** same — a foreign channel layout, but with overlapping ranges, so a
touch "bleeds" into a neighboring channel.

**Fix:** reject firmware with phantoms **immediately**. Unlike a clean fold (C),
phantoms cannot be removed reliably in software: it's ambiguous which point is
real. Look for other firmware.

---

## Step 4. Picking firmware

The main source is the `onitake/gsl-firmware` repository:

```bash
git clone --depth 1 https://github.com/onitake/gsl-firmware
```

Ready-to-use files are in `firmware/linux/silead/`. Choose by screen diagonal
and resolution (`xrandr | grep '\*'`), not by brand name.

A proven quick test loop (one firmware — one minute):

```bash
sudo cp gsl-firmware/firmware/linux/silead/CANDIDATE.fw /lib/firmware/silead/mssl1680.fw
sudo modprobe -r silead ; sudo modprobe silead
# then evtest: drag left-to-right + hold in the center
```

Criteria for good firmware: **drag with no reversal in the middle** AND **a
single TRACKING_ID per touch** (no phantoms) AND coverage to the edges.

### If your model isn't in the repository

The firmware is often embedded inside the stock Windows driver
`SileadTouch.sys` (there may be no separate .fw file). It can be extracted:

- with the `tools/` from `gsl-firmware`, or
- if you have the source config `.h` (`TS_CFG_DATA GSL_TS_CFG[]`) — a binary is
  assembled from it: each `{0xREGISTER, 0xVALUE}` pair is written as two
  little-endian uint32 values.

> In our case that's exactly what happened: cycling through ready-made firmware
> only produced folds and phantoms, but the working configuration was extracted
> from the `SileadTouch.sys` of the working Windows driver. It gave a **clean
> fold with no phantoms** — which is what allowed unfolding the axis with a
> script.

---

## Step 5. When you need a software handler

If the best available firmware gives a **clean, unambiguous fold** (diagnosis C
without phantoms), the axis can be unfolded in user space: a script grabs the
raw sensor's events (`evdev` + `grab`), recomputes coordinates using calibration
curves you measured, and feeds the system a new virtual touchscreen (`uinput`).
The raw sensor is hidden from libinput by a udev rule.

How to measure calibration curves for your own matrix:

1. In evtest, touch known points across the full width (corners, quarters, the
   middle of each half) and record pairs of "raw X → where on screen".
2. Build two tables: for the left half (raw X grows) and the right half (raw X
   after the fold). Put them into `silead-unfold.py` (`LEFT_RAW_TO_OUT`,
   `RIGHT_RAW_TO_OUT`) and set `X_SPLIT` — the raw value at the fold.
3. Likewise `Y_MIN`/`Y_MAX` and the `INVERT_X`/`INVERT_Y` flags — from the
   corners.
4. Run `sudo python3 silead-unfold.py`, tap, adjust the numbers until the finger
   lands under the cursor at every point.

A sign you're on the right track: **corners land but the middle is slightly
off** — almost always a linear calibration error, fixable with numbers. If the
error is different and unpredictable in different places, that's
nonlinearity/phantoms — go back to Step 4 for other firmware.

---

## Quick reference

| What you see in evtest | Diagnosis | Fix |
|---|---|---|
| Smooth but wrong way | Mirror/rotation (A) | xinput matrix / kernel props |
| Small patch, not to edges | Collapsed range (B) | Different firmware |
| Reversal in the middle | Folded axis (C) | Firmware; if fold is clean — script |
| Two points per touch | Phantoms (D) | Different firmware only |

The order of action is always the same: **firmware first, script as a last
resort.** The right firmware settles everything at once; the script is only
needed when no ideal firmware exists for your matrix and the best one gives a
clean fold.
