from __future__ import annotations

import time

from .base import Controller


class VirtualGamepadController(Controller):
    """Xbox 360 virtual controller for PCSX2 via ViGEm/vgamepad."""

    def __init__(self) -> None:
        try:
            import vgamepad as vg
        except ImportError as exc:
            raise RuntimeError(
                "virtual_gamepad backend requires: pip install -e .[virtual-gamepad]"
            ) from exc
        self.vg = vg
        self.pad = vg.VX360Gamepad()
        self.held: set[str] = set()
        self.buttons = {
            "confirm": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
            "cross": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
            "cancel": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
            "triangle": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
            "circle": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
            "square": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
            "start": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
            "select": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
            "up": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
            "down": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
            "left": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
            "right": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
            "l1": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
            "r1": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
        }

    @staticmethod
    def _clamp(value: float) -> float:
        return max(-1.0, min(1.0, float(value)))

    def _button(self, action: str):
        if action not in self.buttons:
            raise KeyError(f"No virtual-gamepad mapping for '{action}'.")
        return self.buttons[action]

    def tap(self, action: str, duration: float = 0.08) -> None:
        button = self._button(action)
        self.pad.press_button(button=button)
        self.pad.update()
        time.sleep(duration)
        self.pad.release_button(button=button)
        self.pad.update()

    def hold(self, action: str) -> None:
        if action not in self.held:
            self.pad.press_button(button=self._button(action))
            self.held.add(action)
            self.pad.update()

    def release(self, action: str) -> None:
        self.pad.release_button(button=self._button(action))
        self.held.discard(action)
        self.pad.update()

    def set_left_stick(self, x: float, y: float) -> None:
        self.pad.left_joystick_float(self._clamp(x), self._clamp(y))
        self.pad.update()

    def set_right_stick(self, x: float, y: float) -> None:
        self.pad.right_joystick_float(self._clamp(x), self._clamp(y))
        self.pad.update()

    def release_all(self) -> None:
        self.pad.reset()
        self.held.clear()
        self.pad.update()
