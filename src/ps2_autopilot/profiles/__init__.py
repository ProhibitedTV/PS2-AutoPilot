from .generic_chaos import GenericChaosProfile
from .madden2005_v4 import Madden2005V4Profile
from .madden2005_v5 import Madden2005V5Profile
from .madden2005_v6 import Madden2005V6Profile
from .madden2005_v6_runtime import Madden2005V6RuntimeProfile
from .madden2005_v7 import Madden2005V7Profile
from .madden2005_v8 import Madden2005V8Profile

Madden2005Profile = Madden2005V8Profile

__all__ = [
    "GenericChaosProfile",
    "Madden2005Profile",
    "Madden2005V4Profile",
    "Madden2005V5Profile",
    "Madden2005V6Profile",
    "Madden2005V6RuntimeProfile",
    "Madden2005V7Profile",
    "Madden2005V8Profile",
]
