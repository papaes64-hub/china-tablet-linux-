#!/usr/bin/env python3
"""
Separate long-press helper for the calibrated Silead virtual touchscreen.

It does not modify or grab the touchscreen.  It only watches the virtual
touch events and emits one right mouse click after a stationary one-finger
hold.
"""

import math
import select
import time
from dataclasses import dataclass

import evdev
from evdev import UInput, ecodes as e


TOUCH_NAME = "silead_ts_unfolded touchscreen"
MOUSE_NAME = "silead_longpress_mouse"

HOLD_SECONDS = 0.75
MOVE_LIMIT_MM = 2.0

X_RESOLUTION = 16.0
Y_RESOLUTION = 28.0
MAX_SLOTS = 10


@dataclass
class Slot:
    active: bool = False
    x: int | None = None
    y: int | None = None


def find_touchscreen() -> evdev.InputDevice | None:
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if dev.name == TOUCH_NAME:
            return dev
        dev.close()
    return None


def run_once() -> None:
    dev = None
    while dev is None:
        dev = find_touchscreen()
        if dev is None:
            time.sleep(1.0)

    mouse = UInput(
        {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
            e.EV_REL: [e.REL_X, e.REL_Y],
        },
        name=MOUSE_NAME,
    )

    slots = [Slot() for _ in range(MAX_SLOTS)]
    current_slot = 0

    candidate_slot: int | None = None
    origin_x: int | None = None
    origin_y: int | None = None
    deadline: float | None = None
    triggered = False
    cancelled = False

    def active_slots() -> list[int]:
        return [i for i, slot in enumerate(slots) if slot.active]

    def clear_candidate() -> None:
        nonlocal candidate_slot, origin_x, origin_y
        nonlocal deadline, triggered, cancelled
        candidate_slot = None
        origin_x = None
        origin_y = None
        deadline = None
        triggered = False
        cancelled = False

    def maybe_start_candidate(now: float) -> None:
        nonlocal candidate_slot, origin_x, origin_y, deadline
        active = active_slots()
        if len(active) != 1:
            return
        index = active[0]
        slot = slots[index]
        if slot.x is None or slot.y is None:
            return
        candidate_slot = index
        origin_x = slot.x
        origin_y = slot.y
        deadline = now + HOLD_SECONDS

    def movement_mm(slot: Slot) -> float:
        if (
            origin_x is None
            or origin_y is None
            or slot.x is None
            or slot.y is None
        ):
            return 0.0
        dx = (slot.x - origin_x) / X_RESOLUTION
        dy = (slot.y - origin_y) / Y_RESOLUTION
        return math.hypot(dx, dy)

    def emit_right_click() -> None:
        mouse.write(e.EV_KEY, e.BTN_RIGHT, 1)
        mouse.syn()
        time.sleep(0.025)
        mouse.write(e.EV_KEY, e.BTN_RIGHT, 0)
        mouse.syn()

    try:
        while True:
            now = time.monotonic()
            timeout = None
            if deadline is not None and not triggered and not cancelled:
                timeout = max(0.0, deadline - now)

            readable, _, _ = select.select([dev.fd], [], [], timeout)

            if not readable:
                active = active_slots()
                if (
                    deadline is not None
                    and not triggered
                    and not cancelled
                    and candidate_slot is not None
                    and active == [candidate_slot]
                    and time.monotonic() >= deadline
                ):
                    emit_right_click()
                    triggered = True
                continue

            for event in dev.read():
                if event.type == e.EV_ABS:
                    if event.code == e.ABS_MT_SLOT:
                        current_slot = max(
                            0, min(MAX_SLOTS - 1, event.value)
                        )
                        continue

                    slot = slots[current_slot]

                    if event.code == e.ABS_MT_TRACKING_ID:
                        if event.value >= 0:
                            slot.active = True
                            slot.x = None
                            slot.y = None

                            # A second finger cancels long press.
                            if len(active_slots()) > 1:
                                cancelled = True
                                deadline = None
                        else:
                            reopen_menu = (
                                candidate_slot == current_slot
                                and triggered
                            )

                            slot.active = False
                            slot.x = None
                            slot.y = None

                            if candidate_slot == current_slot:
                                if reopen_menu:
                                    # Let the touchscreen release finish,
                                    # then leave the context menu open.
                                    time.sleep(0.12)
                                    emit_right_click()

                                clear_candidate()
                        continue

                    if event.code == e.ABS_MT_POSITION_X:
                        slot.x = event.value
                    elif event.code == e.ABS_MT_POSITION_Y:
                        slot.y = event.value

                    if (
                        candidate_slot == current_slot
                        and not triggered
                        and not cancelled
                        and movement_mm(slot) > MOVE_LIMIT_MM
                    ):
                        cancelled = True
                        deadline = None
                    continue

                if event.type == e.EV_SYN and event.code == e.SYN_REPORT:
                    active = active_slots()

                    if not active:
                        clear_candidate()
                        continue

                    if len(active) > 1:
                        cancelled = True
                        deadline = None
                        continue

                    if candidate_slot is None:
                        maybe_start_candidate(time.monotonic())
                    continue

    finally:
        mouse.close()
        dev.close()


def main() -> None:
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            break
        except (OSError, IOError):
            time.sleep(1.0)


if __name__ == "__main__":
    main()
