from __future__ import annotations

import time

from .base import Controller


class KeyboardController(Controller):
    def __init__(self, keymap: dict[str, str]) -> None:
        import pydirectinput

        pydirectinput.PAUSE = 0.01
        pydirectinput.FAILSAFE = False
        self.io = pydirectinput
        self.keymap = keymap
        self.held: set[str] = set()
        self.stick_actions: set[str] = set()

    def _key(self, action: str) -> str:
        if action not in self.keymap:
            raise KeyError(f"No key mapped for action '{action}'.")
        return self.keymap[action]

    def tap(self, action: str, duration: float = 0.08) -> None:
        key = self._key(action)
        self.io.keyDown(key)
        time.sleep(duration)
        self.io.keyUp(key)

    def hold(self, action: str) -> None:
        key = self._key(action)
        if action not in self.held:
            self.io.keyDown(key)
            self.held.add(action)

    def release(self, action: str) -> None:
        key = self._key(action)
        self.io.keyUp(key)
        self.held.discard(action)

    def set_left_stick(self, x: float, y: float) -> None:
        desired: set[str] = set()
        if x < -0.35:
            desired.add("left")
        elif x > 0.35:
            desired.add("right")
        if y > 0.35:
            desired.add("up")
        elif y < -0.35:
            desired.add("down")
        for action in self.stick_actions - desired:
            self.release(action)
        for action in desired - self.stick_actions:
            self.hold(action)
        self.stick_actions = desired

    def release_all(self) -> None:
        for action in list(self.held):
            self.release(action)
        self.stick_actions.clear()
