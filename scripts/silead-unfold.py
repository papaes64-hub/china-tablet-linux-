#!/usr/bin/env python3
"""
Silead MSSL1680 coordinate unfolder.

v10 changes:
- replaces the single X correction curve with separate measured curves for
  the left and right physical sensor halves;
- resolves the ambiguous centre values where both halves reported nearly the
  same virtual X for different physical positions;
- uses the 11-point global test plus the 20-point seam test;
- keeps the v9 filtering, round curves and dropout handling unchanged.
"""

import math
import select
import time
from dataclasses import dataclass, field

import evdev
from evdev import AbsInfo, UInput, ecodes as e


# The raw controller folds two physical X halves into different raw ranges.
X_SPLIT = 1025
Y_MIN, Y_MAX = 30, 1260

INVERT_Y = True
INVERT_X = False
OUT_MAX = 4095
DEV_NAME = "silead_ts"
MAX_SLOTS = 10

# Direct raw-to-screen calibration.  The left table is increasing in raw X;
# the right table is also ordered by increasing raw X, but its screen X runs
# from right to centre.  The near-centre right-side point comes from the
# 20-point seam calibration and is what removes the approximately 59 px error.
LEFT_RAW_TO_OUT = (
    (110.0, 0),
    (164.15, 204),
    (343.38, 573),
    (515.72, 941),
    (683.34, 1310),
    (841.36, 1637),
    (853.54, 1679),
    (948.20, 1883),
    (989.82, 2048),
)

RIGHT_RAW_TO_OUT = (
    (1060.00, 4095),
    (1125.85, 3891),
    (1294.83, 3522),
    (1468.37, 3154),
    (1636.10, 2785),
    (1810.05, 2416),
    (1913.49, 2212),
    (1997.00, 2048),
)

# Shape-preserving adaptive smoothing. No per-axis median/deadband is used.
ALPHA_STILL = 0.12
ALPHA_SLOW = 0.55
ALPHA_MEDIUM = 0.85
ALPHA_FAST = 1.00
STILL_MM = 0.45
SLOW_MM = 1.50
MEDIUM_MM = 4.00

# The captured diagonal contained false release gaps up to 124 ms.
RELEASE_DEBOUNCE = 0.165  # seconds

# The largest measured continuous-motion jump across a false dropout was
# about 27.5 mm. Leave margin, while still rejecting a far-away new tap.
REJOIN_DISTANCE_MM = 35.0

# No synthetic point queue is used. Keeping the virtual contact active is
# sufficient for drawing applications to connect the last and current point.
X_RESOLUTION = 16  # output units/mm
Y_RESOLUTION = 28  # output units/mm

# At the fold seam the controller may hold X near the centre while Y keeps
# moving. Reconstruct only substantial jumps; slow movement stays mostly raw.
SEAM_CENTER = OUT_MAX / 2.0
SEAM_LOCK_WIDTH = 35        # about 2.2 mm
SEAM_BLEND_WIDTH = 260      # about 16.3 mm
SEAM_TRIGGER_WIDTH = 130
SEAM_MIN_RECONSTRUCT_MM = 5.0
SEAM_MAX_OFFSET_MM = 18.0
SEAM_HISTORY_POINTS = 10
SEAM_MAX_SLOPE = 4.0


def interpolate_table(raw: float, table: tuple[tuple[float, int], ...]) -> int:
    """Piecewise-linear interpolation with endpoint clamping."""
    if raw <= table[0][0]:
        return int(table[0][1])
    if raw >= table[-1][0]:
        return int(table[-1][1])

    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if raw <= x1:
            t = (raw - x0) / float(x1 - x0)
            value = y0 + t * (y1 - y0)
            return max(0, min(OUT_MAX, int(round(value))))

    return int(table[-1][1])


def unfold_x(raw: int) -> int:
    table = LEFT_RAW_TO_OUT if raw <= X_SPLIT else RIGHT_RAW_TO_OUT
    out = interpolate_table(float(raw), table)
    if INVERT_X:
        out = OUT_MAX - out
    return out


def raw_side(raw_x: int) -> int:
    return 0 if raw_x <= X_SPLIT else 1


def fix_y(raw: int) -> int:
    t = (raw - Y_MIN) / float(Y_MAX - Y_MIN)
    out = max(0, min(OUT_MAX, int(t * OUT_MAX)))
    if INVERT_Y:
        out = OUT_MAX - out
    return out


def adaptive_filter_point(
    previous_x: int | None,
    previous_y: int | None,
    current_x: int,
    current_y: int,
) -> tuple[int, int]:
    """Vector-distance adaptive low-pass filter.

    It suppresses stationary jitter but preserves diagonal and circular
    trajectories because it does not take separate X/Y medians.
    """
    if previous_x is None or previous_y is None:
        return current_x, current_y

    dx_mm = (current_x - previous_x) / float(X_RESOLUTION)
    dy_mm = (current_y - previous_y) / float(Y_RESOLUTION)
    distance_mm = math.hypot(dx_mm, dy_mm)

    if distance_mm <= STILL_MM:
        alpha = ALPHA_STILL
    elif distance_mm <= SLOW_MM:
        alpha = ALPHA_SLOW
    elif distance_mm <= MEDIUM_MM:
        alpha = ALPHA_MEDIUM
    else:
        alpha = ALPHA_FAST

    new_x = int(round(previous_x + alpha * (current_x - previous_x)))
    new_y = int(round(previous_y + alpha * (current_y - previous_y)))

    # Preserve extremely slow deliberate motion despite integer rounding.
    if distance_mm > STILL_MM:
        if new_x == previous_x and current_x != previous_x:
            new_x += 1 if current_x > previous_x else -1
        if new_y == previous_y and current_y != previous_y:
            new_y += 1 if current_y > previous_y else -1

    return new_x, new_y


def find_device() -> evdev.InputDevice | None:
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if dev.name == DEV_NAME:
            return dev
        dev.close()
    return None


@dataclass
class SlotState:
    raw_active: bool = False
    virtual_active: bool = False
    pending_release: float | None = None
    pending_down: bool = False
    possible_rejoin: bool = False
    candidate_x: int | None = None
    candidate_y: int | None = None
    last_raw_x: int | None = None
    last_raw_y: int | None = None
    virtual_tracking_id: int = -1
    x: int | None = None
    y: int | None = None
    coord_dirty: bool = False
    candidate_side: int | None = None
    frame_side: int | None = None
    contact_side: int | None = None
    history: list[tuple[int, int]] = field(default_factory=list)
    seam_active: bool = False
    seam_anchor_x: int = 0
    seam_anchor_y: int = 0
    seam_slope: float = 0.0
    seam_y_offset: float = 0.0
    seam_offset_ready: bool = False
    seam_target_side: int | None = None


def main() -> None:
    dev = None
    while dev is None:
        dev = find_device()
        if dev is None:
            time.sleep(2)

    x_abs = AbsInfo(value=0, min=0, max=OUT_MAX,
                    fuzz=0, flat=0, resolution=X_RESOLUTION)
    y_abs = AbsInfo(value=0, min=0, max=OUT_MAX,
                    fuzz=0, flat=0, resolution=Y_RESOLUTION)
    slot_abs = AbsInfo(value=0, min=0, max=MAX_SLOTS - 1,
                       fuzz=0, flat=0, resolution=0)
    track_abs = AbsInfo(value=0, min=0, max=65535,
                        fuzz=0, flat=0, resolution=0)

    caps = {
        e.EV_KEY: [e.BTN_TOUCH],
        e.EV_ABS: [
            (e.ABS_X, x_abs),
            (e.ABS_Y, y_abs),
            (e.ABS_MT_POSITION_X, x_abs),
            (e.ABS_MT_POSITION_Y, y_abs),
            (e.ABS_MT_SLOT, slot_abs),
            (e.ABS_MT_TRACKING_ID, track_abs),
        ],
    }

    ui = UInput(
        caps,
        name="silead_ts_unfolded touchscreen",
        input_props=[e.INPUT_PROP_DIRECT],
    )

    slots = [SlotState() for _ in range(MAX_SLOTS)]
    current_slot = 0
    next_tracking_id = 1
    single_x: int | None = None
    single_y: int | None = None
    frame_dirty = False

    def active_count() -> int:
        return sum(1 for slot in slots if slot.virtual_active)

    def sync_now() -> None:
        nonlocal frame_dirty
        if frame_dirty:
            ui.syn()
            frame_dirty = False

    def emit_release(slot_index: int) -> None:
        nonlocal frame_dirty
        slot = slots[slot_index]
        if not slot.virtual_active:
            slot.pending_release = None
            return

        ui.write(e.EV_ABS, e.ABS_MT_SLOT, slot_index)
        ui.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1)
        slot.virtual_active = False
        slot.pending_release = None
        slot.virtual_tracking_id = -1

        if active_count() == 0:
            ui.write(e.EV_KEY, e.BTN_TOUCH, 0)

        frame_dirty = True

    def point_distance_mm(slot: SlotState, x: int, y: int) -> float:
        if slot.x is None or slot.y is None:
            return math.inf
        dx = (x - slot.x) / X_RESOLUTION
        dy = (y - slot.y) / Y_RESOLUTION
        return math.hypot(dx, dy)

    def reset_seam(slot: SlotState) -> None:
        slot.seam_active = False
        slot.seam_slope = 0.0
        slot.seam_y_offset = 0.0
        slot.seam_offset_ready = False
        slot.seam_target_side = None

    def estimate_recent_slope(slot: SlotState) -> float:
        points = slot.history[-SEAM_HISTORY_POINTS:]
        if len(points) < 2:
            return 0.0

        mean_x = sum(point[0] for point in points) / len(points)
        mean_y = sum(point[1] for point in points) / len(points)
        denominator = sum((point[0] - mean_x) ** 2 for point in points)
        if denominator < 25.0:
            return 0.0

        numerator = sum(
            (point[0] - mean_x) * (point[1] - mean_y)
            for point in points
        )
        slope = numerator / denominator
        return max(-SEAM_MAX_SLOPE, min(SEAM_MAX_SLOPE, slope))

    def start_seam_reconstruction(slot: SlotState, target_side: int) -> None:
        if slot.x is None or slot.y is None:
            return
        slot.seam_active = True
        slot.seam_anchor_x = slot.x
        slot.seam_anchor_y = slot.y
        slot.seam_slope = estimate_recent_slope(slot)
        slot.seam_y_offset = 0.0
        slot.seam_offset_ready = False
        slot.seam_target_side = target_side

    def correct_seam_y(
        slot: SlotState,
        x: int,
        y: int,
        side: int | None,
    ) -> int:
        if not slot.seam_active:
            return y

        if side is not None and slot.seam_target_side is not None:
            if side != slot.seam_target_side:
                reset_seam(slot)
                return y

        distance = abs(x - SEAM_CENTER)

        if not slot.seam_offset_ready:
            predicted = (
                slot.seam_anchor_y
                + slot.seam_slope * (x - slot.seam_anchor_x)
            )
            max_offset = SEAM_MAX_OFFSET_MM * Y_RESOLUTION
            slot.seam_y_offset = max(
                -max_offset,
                min(max_offset, predicted - y),
            )
            slot.seam_offset_ready = True

        if distance <= SEAM_LOCK_WIDTH:
            weight = 1.0
        elif distance < SEAM_BLEND_WIDTH:
            u = (
                (distance - SEAM_LOCK_WIDTH)
                / float(SEAM_BLEND_WIDTH - SEAM_LOCK_WIDTH)
            )
            # Fade the initial correction smoothly and quickly.
            smooth = u * u * (3.0 - 2.0 * u)
            weight = 1.0 - smooth
        else:
            reset_seam(slot)
            return y

        corrected = y + slot.seam_y_offset * weight
        return max(0, min(OUT_MAX, int(round(corrected))))

    def emit_position(slot_index: int, x: int, y: int, *, force: bool) -> None:
        nonlocal frame_dirty, single_x, single_y
        slot = slots[slot_index]

        if force:
            new_x, new_y = x, y
        else:
            new_x, new_y = adaptive_filter_point(
                slot.x, slot.y, x, y
            )

        change_x = force or new_x != slot.x
        change_y = force or new_y != slot.y
        if not change_x and not change_y:
            return

        ui.write(e.EV_ABS, e.ABS_MT_SLOT, slot_index)
        if change_x:
            ui.write(e.EV_ABS, e.ABS_MT_POSITION_X, new_x)
            slot.x = new_x
        if change_y:
            ui.write(e.EV_ABS, e.ABS_MT_POSITION_Y, new_y)
            slot.y = new_y

        # Keep the legacy single-touch axes synchronized with slot 0.
        if slot_index == 0:
            if change_x or force or new_x != single_x:
                ui.write(e.EV_ABS, e.ABS_X, new_x)
                single_x = new_x
            if change_y or force or new_y != single_y:
                ui.write(e.EV_ABS, e.ABS_Y, new_y)
                single_y = new_y

        slot.history.append((new_x, new_y))
        if len(slot.history) > SEAM_HISTORY_POINTS:
            del slot.history[:-SEAM_HISTORY_POINTS]

        frame_dirty = True

    def bridge_position(slot_index: int, x: int, y: int) -> None:
        """Resume at the fresh point without queuing synthetic catch-up events."""
        emit_position(slot_index, x, y, force=True)


    def begin_virtual_contact(slot_index: int, x: int, y: int) -> None:
        nonlocal next_tracking_id, frame_dirty
        slot = slots[slot_index]
        was_empty = active_count() == 0

        slot.virtual_active = True
        slot.pending_release = None
        slot.history.clear()
        reset_seam(slot)
        slot.contact_side = slot.candidate_side
        slot.virtual_tracking_id = next_tracking_id
        next_tracking_id = (next_tracking_id + 1) & 0xFFFF
        if next_tracking_id == 0:
            next_tracking_id = 1

        # Down and fresh position are emitted in one frame. This prevents a
        # very short tap from using stale coordinates from the previous touch.
        ui.write(e.EV_ABS, e.ABS_MT_SLOT, slot_index)
        ui.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, slot.virtual_tracking_id)
        emit_position(slot_index, x, y, force=True)
        if was_empty:
            ui.write(e.EV_KEY, e.BTN_TOUCH, 1)
        frame_dirty = True

    def finish_pending_down(slot_index: int, *, allow_cached_axis: bool = False) -> None:
        slot = slots[slot_index]
        if not slot.pending_down:
            return

        # Type-B touch devices may omit an unchanged axis in the first frame
        # of a new tracking ID. Use the last raw value only for that missing
        # axis, and only when at least one fresh coordinate was received.
        if allow_cached_axis and (slot.candidate_x is not None or slot.candidate_y is not None):
            if slot.candidate_x is None:
                slot.candidate_x = slot.last_raw_x
            if slot.candidate_y is None:
                slot.candidate_y = slot.last_raw_y

        if slot.candidate_x is None or slot.candidate_y is None:
            return

        x, y = slot.candidate_x, slot.candidate_y

        if slot.possible_rejoin and slot.virtual_active:
            distance = point_distance_mm(slot, x, y)
            if distance <= REJOIN_DISTANCE_MM:
                # False controller dropout: retain the same tracking ID.
                # When the raw contact has crossed to the other sensor half,
                # reconstruct the trajectory near the centre seam.
                slot.pending_release = None
                crossed_seam = (
                    slot.contact_side is not None
                    and slot.candidate_side is not None
                    and slot.contact_side != slot.candidate_side
                    and abs(x - SEAM_CENTER) <= SEAM_TRIGGER_WIDTH
                )
                if crossed_seam and distance >= SEAM_MIN_RECONSTRUCT_MM:
                    start_seam_reconstruction(slot, slot.candidate_side)
                else:
                    reset_seam(slot)

                corrected_y = correct_seam_y(
                    slot, x, y, slot.candidate_side
                )
                bridge_position(slot_index, x, corrected_y)
                if slot.candidate_side is not None:
                    slot.contact_side = slot.candidate_side
            else:
                # A real, fast new tap elsewhere: end the old contact first.
                emit_release(slot_index)
                sync_now()
                begin_virtual_contact(slot_index, x, y)
        else:
            if slot.virtual_active:
                emit_release(slot_index)
                sync_now()
            begin_virtual_contact(slot_index, x, y)

        slot.pending_down = False
        slot.possible_rejoin = False
        slot.candidate_x = None
        slot.candidate_y = None
        slot.candidate_side = None

    def flush_expired(now: float) -> None:
        for index, slot in enumerate(slots):
            if (
                slot.virtual_active
                and slot.pending_release is not None
                and now >= slot.pending_release
                and not slot.pending_down
            ):
                emit_release(index)
        sync_now()

    dev.grab()
    try:
        while True:
            now = time.monotonic()
            deadlines = [
                slot.pending_release
                for slot in slots
                if slot.virtual_active
                and slot.pending_release is not None
                and not slot.pending_down
            ]
            timeout = None if not deadlines else max(0.0, min(deadlines) - now)

            readable, _, _ = select.select([dev.fd], [], [], timeout)
            if not readable:
                flush_expired(time.monotonic())
                continue

            for event in dev.read():
                if event.type == e.EV_ABS:
                    if event.code == e.ABS_MT_SLOT:
                        current_slot = max(0, min(MAX_SLOTS - 1, event.value))
                        continue

                    slot = slots[current_slot]

                    if event.code == e.ABS_MT_TRACKING_ID:
                        now = time.monotonic()
                        if event.value >= 0:
                            slot.raw_active = True
                            slot.pending_down = True
                            slot.possible_rejoin = (
                                slot.virtual_active
                                and slot.pending_release is not None
                                and now < slot.pending_release
                            )
                            slot.candidate_x = None
                            slot.candidate_y = None
                        else:
                            slot.raw_active = False
                            if slot.pending_down:
                                # No complete X/Y pair arrived: ignore this
                                # malformed micro-touch instead of drawing at
                                # a stale coordinate.
                                slot.pending_down = False
                                slot.possible_rejoin = False
                                slot.candidate_x = None
                                slot.candidate_y = None
                                slot.candidate_side = None
                            if slot.virtual_active:
                                slot.pending_release = now + RELEASE_DEBOUNCE
                        continue

                    if event.code == e.ABS_MT_POSITION_X:
                        side = raw_side(event.value)
                        mapped = unfold_x(event.value)
                        slot.last_raw_x = mapped
                        slot.frame_side = side
                        if slot.pending_down:
                            slot.candidate_x = mapped
                            slot.candidate_side = side
                            finish_pending_down(current_slot)
                        elif slot.virtual_active:
                            slot.coord_dirty = True
                        continue

                    if event.code == e.ABS_MT_POSITION_Y:
                        mapped = fix_y(event.value)
                        slot.last_raw_y = mapped
                        if slot.pending_down:
                            slot.candidate_y = mapped
                            finish_pending_down(current_slot)
                        elif slot.virtual_active:
                            slot.coord_dirty = True
                        continue

                    # Physical ABS_X/ABS_Y duplicate slot-0 coordinates. They
                    # are regenerated from slot 0 above, so do not forward them.
                    if event.code in (e.ABS_X, e.ABS_Y):
                        continue

                    ui.write(event.type, event.code, event.value)
                    frame_dirty = True
                    continue

                # BTN_TOUCH is regenerated from virtual slot state.
                if event.type == e.EV_KEY and event.code == e.BTN_TOUCH:
                    continue

                if event.type == e.EV_SYN and event.code == e.SYN_REPORT:
                    # Complete new contacts whose first frame omitted one
                    # unchanged coordinate axis.
                    for index, pending_slot in enumerate(slots):
                        if pending_slot.pending_down:
                            finish_pending_down(index, allow_cached_axis=True)

                    # Emit one complete X/Y point per hardware frame. This is
                    # the key fix for diagonal and circular trajectories.
                    for index, active_slot in enumerate(slots):
                        if (
                            active_slot.coord_dirty
                            and active_slot.virtual_active
                            and not active_slot.pending_down
                            and active_slot.last_raw_x is not None
                            and active_slot.last_raw_y is not None
                        ):
                            corrected_y = correct_seam_y(
                                active_slot,
                                active_slot.last_raw_x,
                                active_slot.last_raw_y,
                                active_slot.frame_side,
                            )
                            emit_position(
                                index,
                                active_slot.last_raw_x,
                                corrected_y,
                                force=False,
                            )
                            if active_slot.frame_side is not None:
                                active_slot.contact_side = active_slot.frame_side
                        active_slot.coord_dirty = False
                        active_slot.frame_side = None

                    sync_now()
                    flush_expired(time.monotonic())
                    continue

                ui.write(event.type, event.code, event.value)
                frame_dirty = True

            flush_expired(time.monotonic())

    finally:
        try:
            dev.ungrab()
        except Exception:
            pass
        ui.close()


if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            break
        except (OSError, IOError):
            time.sleep(2)
