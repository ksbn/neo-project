"""Distance units, following NASA NeoWs conventions.

NeoWs reports ``miss_distance.lunar`` as ``astronomical * 389`` (checked against
live responses), so one lunar distance (LD) is 1/389 AU = 384,570 km here, not
the 384,400 km mean Earth-Moon distance. Using NASA's convention keeps every LD
figure in the app consistent with what NASA publishes.
"""

from __future__ import annotations

from typing import Final

AU_KM: Final = 149_597_870.7  # IAU 2012 astronomical unit
LD_PER_AU: Final = 389.0  # NeoWs convention
LUNAR_DISTANCE_KM: Final = AU_KM / LD_PER_AU


def km_to_ld(km: float) -> float:
    return km / LUNAR_DISTANCE_KM


def au_to_ld(au: float) -> float:
    return au * LD_PER_AU
