from .generic_chaos import GenericChaosProfile
from .madden2005_v4 import Madden2005V4Profile
from .madden2005_v5 import Madden2005V5Profile

Madden2005Profile = Madden2005V5Profile

__all__ = [
    "GenericChaosProfile",
    "Madden2005Profile",
    "Madden2005V4Profile",
    "Madden2005V5Profile",
]
