from __future__ import annotations

from .madden2005 import Possession
from .madden2005_v6 import Madden2005V6Profile


class Madden2005V6RuntimeProfile(Madden2005V6Profile):
    """Runtime wrapper that lets definitive new evidence replace stale possession.

    The legacy possession filter intentionally resists rapid role changes, but a
    football possession really can flip instantly after an interception or a new
    play-call screen. Evidence at 0.95+ is therefore allowed to replace a stale
    opposite-role belief immediately; weaker evidence still uses the conservative
    base filter.
    """

    name = "madden2005"

    def _set_possession(self, role: Possession, evidence: float) -> None:
        evidence = self._clamp(evidence, 0.0, 1.0)
        if role == Possession.UNKNOWN:
            return
        if self.possession not in {Possession.UNKNOWN, role} and evidence >= 0.95:
            self.possession = role
            self.possession_confidence = evidence
            return
        super()._set_possession(role, evidence)
