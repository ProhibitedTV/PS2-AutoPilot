from __future__ import annotations

from abc import ABC, abstractmethod


class Controller(ABC):
    @abstractmethod
    def tap(self, action: str, duration: float = 0.08) -> None: ...

    @abstractmethod
    def hold(self, action: str) -> None: ...

    @abstractmethod
    def release(self, action: str) -> None: ...

    @abstractmethod
    def release_all(self) -> None: ...

    def set_left_stick(self, x: float, y: float) -> None:
        del x, y

    def set_right_stick(self, x: float, y: float) -> None:
        del x, y

    def neutral_sticks(self) -> None:
        self.set_left_stick(0.0, 0.0)
        self.set_right_stick(0.0, 0.0)
