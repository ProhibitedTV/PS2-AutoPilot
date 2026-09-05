from .generic_chaos import GenericChaosProfile
from .guitar_hero_v2 import GuitarHeroPhase, GuitarHeroScreen, GuitarHeroV2Profile
from .guitar_hero_v3 import GuitarHeroV3Profile
from .jak_and_daxter import JakAndDaxterProfile as JakAndDaxterV1Profile, JakPhase
from .jak_and_daxter_v2 import JakAndDaxterV2Profile
from .jak_and_daxter_v3 import JakAndDaxterV3Profile
from .jak_and_daxter_v4 import JakAndDaxterV4Profile
from .jak_and_daxter_v5 import JakAndDaxterV5Profile
from .jak_and_daxter_v6 import JakAndDaxterV6Profile
from .jak_and_daxter_v7 import JakAndDaxterV7Profile
from .jak_and_daxter_v8 import JakAndDaxterV8Profile
from .jak_and_daxter_v9 import JakAndDaxterV9Profile
from .jak_and_daxter_v10 import JakAndDaxterV10Profile
from .jak_and_daxter_v11 import JakAndDaxterV11Profile
from .jak_and_daxter_v12 import JakAndDaxterV12Profile
from .jak_and_daxter_v13 import JakAndDaxterV13Profile
from .jak_and_daxter_v14 import JakAndDaxterV14Profile
from .jak_and_daxter_v15 import JakAndDaxterV15Profile
from .jak_and_daxter_v16 import JakAndDaxterV16Profile
from .jak_and_daxter_v17 import JakAndDaxterV17Profile
from .jak_and_daxter_v18 import JakAndDaxterV18Profile
from .jak_and_daxter_v19 import JakAndDaxterV19Profile
from .jak_and_daxter_v20 import JakAndDaxterV20Profile
from .jak_and_daxter_v21 import JakAndDaxterV21Profile
from .jak_and_daxter_v22_hardened import JakAndDaxterV22Profile
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
from .madden2005_v23 import Madden2005V23Profile
from .madden2005_v24 import Madden2005V24Profile
from .madden2005_v25 import Madden2005V25Profile
from .madden2005_v26 import Madden2005V26Profile
from .madden2005_v27 import Madden2005V27Profile
from .madden2005_v28 import Madden2005V28Profile
from .madden2005_v29 import Madden2005V29Profile
from .madden2005_v30 import Madden2005V30Profile
from .madden2005_v31 import Madden2005V31Profile
from .madden2005_v32 import Madden2005V32Profile
from .nfs_hot_pursuit_2 import NfsHotPursuit2V1Profile, NfsPhase
from .nfs_hot_pursuit_2_v2 import NfsHotPursuit2V2Profile, NfsRoute, NfsScreen
from .nfs_hot_pursuit_2_v3 import NfsHotPursuit2V3Profile
from .nfs_hot_pursuit_2_v4 import NfsHotPursuit2V4Profile
from .nfs_hot_pursuit_2_v5 import NfsHotPursuit2V5Profile
from .nfs_hot_pursuit_2_v6 import NfsHotPursuit2V6Profile
from .nfs_hot_pursuit_2_v7 import NfsHotPursuit2V7Profile
from .nfs_hot_pursuit_2_v8 import NfsHotPursuit2V8Profile
from .nfs_hot_pursuit_2_v9 import NfsHotPursuit2V9Profile
from .nfs_hot_pursuit_2_v10 import NfsHotPursuit2V10Profile
from .nfs_hot_pursuit_2_v11 import NfsHotPursuit2V11Profile
from .nfs_hot_pursuit_2_v12 import NfsHotPursuit2V12Profile
from .nfs_hot_pursuit_2_v13 import NfsHotPursuit2V13Profile
from .nfs_hot_pursuit_2_v14 import NfsHotPursuit2V14Profile

GuitarHeroProfile = GuitarHeroV3Profile
JakAndDaxterProfile = JakAndDaxterV22Profile
Madden2005Profile = Madden2005V32Profile
NfsHotPursuit2Profile = NfsHotPursuit2V14Profile

from .registry import (  # noqa: E402
    ProfileSpec,
    build_profile,
    canonical_profile_name,
    get_profile_spec,
    list_profile_specs,
)

__all__ = [
    "GenericChaosProfile",
    "GuitarHeroProfile",
    "GuitarHeroV2Profile",
    "GuitarHeroV3Profile",
    "GuitarHeroPhase",
    "GuitarHeroScreen",
    "JakAndDaxterProfile",
    "JakAndDaxterV1Profile",
    "JakAndDaxterV2Profile",
    "JakAndDaxterV3Profile",
    "JakAndDaxterV4Profile",
    "JakAndDaxterV5Profile",
    "JakAndDaxterV6Profile",
    "JakAndDaxterV7Profile",
    "JakAndDaxterV8Profile",
    "JakAndDaxterV9Profile",
    "JakAndDaxterV10Profile",
    "JakAndDaxterV11Profile",
    "JakAndDaxterV12Profile",
    "JakAndDaxterV13Profile",
    "JakAndDaxterV14Profile",
    "JakAndDaxterV15Profile",
    "JakAndDaxterV16Profile",
    "JakAndDaxterV17Profile",
    "JakAndDaxterV18Profile",
    "JakAndDaxterV19Profile",
    "JakAndDaxterV20Profile",
    "JakAndDaxterV21Profile",
    "JakAndDaxterV22Profile",
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
    "Madden2005V23Profile",
    "Madden2005V24Profile",
    "Madden2005V25Profile",
    "Madden2005V26Profile",
    "Madden2005V27Profile",
    "Madden2005V28Profile",
    "Madden2005V29Profile",
    "Madden2005V30Profile",
    "Madden2005V31Profile",
    "Madden2005V32Profile",
    "NfsHotPursuit2Profile",
    "NfsHotPursuit2V1Profile",
    "NfsHotPursuit2V2Profile",
    "NfsHotPursuit2V3Profile",
    "NfsHotPursuit2V4Profile",
    "NfsHotPursuit2V5Profile",
    "NfsHotPursuit2V6Profile",
    "NfsHotPursuit2V7Profile",
    "NfsHotPursuit2V8Profile",
    "NfsHotPursuit2V9Profile",
    "NfsHotPursuit2V10Profile",
    "NfsHotPursuit2V11Profile",
    "NfsHotPursuit2V12Profile",
    "NfsHotPursuit2V13Profile",
    "NfsHotPursuit2V14Profile",
    "NfsPhase",
    "NfsRoute",
    "NfsScreen",
    "ProfileSpec",
    "build_profile",
    "canonical_profile_name",
    "get_profile_spec",
    "list_profile_specs",
]
