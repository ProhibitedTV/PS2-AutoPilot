from .generic_chaos import GenericChaosProfile
from .jak_and_daxter import JakAndDaxterProfile as JakAndDaxterV1Profile, JakPhase
from .jak_and_daxter_v2 import JakAndDaxterV2Profile
from .jak_and_daxter_v3 import JakAndDaxterV3Profile
from .jak_and_daxter_v4 import JakAndDaxterV4Profile
from .madden2005_v4 import Madden2005V4Profile
from .madden2005_v5 import Madden2005V5Profile
from .madden2005_v6 import Madden2005V6Profile
from .madden2005_v6_runtime import Madden2005V6RuntimeProfile
from .madden2005_v7 import Madden2005V7Profile
from .madden2005_v8 import Madden2005V8Profile
from .madden2005_v9 import Madden2005V9Profile
from .madden2005_v10 import Madden2005V10Profile
from .madden2005_v11 import Madden2005V11Profile
from .madden2005_v12 import Madden2005V12Profile
from .madden2005_v13 import Madden2005V13Profile
from .madden2005_v14 import Madden2005V14Profile
from .madden2005_v15 import Madden2005V15Profile
from .madden2005_v16 import Madden2005V16Profile
from .madden2005_v17 import Madden2005V17Profile
from .madden2005_v18 import Madden2005V18Profile
from .madden2005_v19 import Madden2005V19Profile
from .madden2005_v20 import Madden2005V20Profile
from .madden2005_v21 import Madden2005V21Profile
from .madden2005_v22 import Madden2005V22Profile

JakAndDaxterProfile = JakAndDaxterV4Profile
Madden2005Profile = Madden2005V22Profile

from .registry import (  # noqa: E402
    ProfileSpec,
    build_profile,
    canonical_profile_name,
    get_profile_spec,
    list_profile_specs,
)

__all__ = [
    "GenericChaosProfile",
    "JakAndDaxterProfile",
    "JakAndDaxterV1Profile",
    "JakAndDaxterV2Profile",
    "JakAndDaxterV3Profile",
    "JakAndDaxterV4Profile",
    "JakPhase",
    "Madden2005Profile",
    "Madden2005V4Profile",
    "Madden2005V5Profile",
    "Madden2005V6Profile",
    "Madden2005V6RuntimeProfile",
    "Madden2005V7Profile",
    "Madden2005V8Profile",
    "Madden2005V9Profile",
    "Madden2005V10Profile",
    "Madden2005V11Profile",
    "Madden2005V12Profile",
    "Madden2005V13Profile",
    "Madden2005V14Profile",
    "Madden2005V15Profile",
    "Madden2005V16Profile",
    "Madden2005V17Profile",
    "Madden2005V18Profile",
    "Madden2005V19Profile",
    "Madden2005V20Profile",
    "Madden2005V21Profile",
    "Madden2005V22Profile",
    "ProfileSpec",
    "build_profile",
    "canonical_profile_name",
    "get_profile_spec",
    "list_profile_specs",
]
