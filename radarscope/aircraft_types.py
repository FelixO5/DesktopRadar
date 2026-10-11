"""Classify aircraft into silhouette categories (airliner, bizjet, fighter, ...).

Uses the ICAO type designator (``t``, e.g. ``A320``) first, then falls back to
the ADS-B emitter category (``category``, e.g. ``A3``) the transponder sends.
No Qt, no network.
"""

from __future__ import annotations

from enum import Enum


class Category(str, Enum):
    LIGHT = "light"                   # single-engine piston / light aircraft
    TURBOPROP = "turboprop"           # twin turboprops, regional props
    BIZJET = "bizjet"                 # small / business jets
    NARROWBODY = "narrowbody"         # single-aisle airliners
    WIDEBODY = "widebody"             # twin-aisle twins
    HEAVY = "heavy"                   # four-engine heavies
    MIL_TRANSPORT = "mil_transport"   # high-wing military transports
    FIGHTER = "fighter"               # fast jets / jet trainers
    HELICOPTER = "helicopter"
    GLIDER = "glider"
    BALLOON = "balloon"
    DRONE = "drone"
    UNKNOWN = "unknown"


LABELS = {
    Category.LIGHT: "Light aircraft", Category.TURBOPROP: "Turboprop",
    Category.BIZJET: "Small jet", Category.NARROWBODY: "Airliner",
    Category.WIDEBODY: "Wide-body airliner", Category.HEAVY: "Four-engine heavy",
    Category.MIL_TRANSPORT: "Military transport", Category.FIGHTER: "Fighter / fast jet",
    Category.HELICOPTER: "Helicopter", Category.GLIDER: "Glider", Category.BALLOON: "Balloon",
    Category.DRONE: "Drone", Category.UNKNOWN: "Unknown type",
}

_TYPES: dict[Category, str] = {
    Category.NARROWBODY: """
        A318 A319 A320 A321 A19N A20N A21N B712 B731 B732 B733 B734 B735 B736 B737 B738 B739
        B37M B38M B39M B3XM B752 B753 BCS1 BCS3 E170 E175 E190 E195 E290 E295 E75L E75S
        CRJ1 CRJ2 CRJ7 CRJ9 CRJX F70 F100 RJ70 RJ85 RJ1H B461 B462 B463 MD81 MD82 MD83 MD87
        MD88 MD90 C919 SU95 P8 E6
    """,
    Category.WIDEBODY: """
        A306 A30B A310 A332 A333 A338 A339 A359 A35K B762 B763 B764 B772 B773 B77L B77W
        B778 B779 B788 B789 B78X MD11 DC10 L101 IL96 K46 KC46 MRTT
    """,
    Category.HEAVY: """
        A342 A343 A345 A346 A388 B741 B742 B743 B744 B748 B74S B74R A124 A225 IL62 E3TF E3CF
        K35R K35E C135 R135 E767 VC10
    """,
    Category.MIL_TRANSPORT: "C17 C5 C5M A400 C130 C30J IL76 C160 AN12 AN22 Y20 KC39 C2",
    Category.TURBOPROP: """
        AT43 AT44 AT45 AT46 AT72 AT73 AT75 AT76 DH8A DH8B DH8C DH8D SF34 JS31 JS32 JS41 B190
        BE20 BE30 BE9L BE9T B350 D228 DHC6 SW4 P180 L410 AN24 AN26 AN32 C295 CN35 SH36 F50 F27
        D328 E120 PA31 C441 C425 BE99 MA60 Q400
    """,
    Category.BIZJET: """
        C25A C25B C25C C25M C501 C510 C525 C526 C550 C551 C55B C560 C56X C650 C680 C68A C700
        C750 CL30 CL35 CL60 GLEX GL5T GL6T GL7T GL8T GLF2 GLF3 GLF4 GLF5 GLF6 G150 G200 G280
        LJ31 LJ35 LJ40 LJ45 LJ55 LJ60 LJ70 LJ75 E50P E55P E545 E550 E135 E35L PC24 FA10 FA20
        FA50 FA7X FA8X F2TH F900 H25A H25B H25C HDJT PRM1 BE40 SF50 EA50 ASTR GALX WW24 J328
    """,
    Category.FIGHTER: """
        EUFI F35 F16 F15 F18 F18S F22 F14 F4 F5 TOR HAWK T45 RFAL M2K M2000 MIR2 JAS39 GRIF
        A10 T38 T50 L39 L159 M346 AJET MB39 SU27 SU30 SU35 MG29 HAR AV8 K8 T7 TUCA
    """,
    Category.HELICOPTER: """
        EC20 EC25 EC30 EC35 EC45 EC55 EC75 AS32 AS50 AS55 AS65 AS3B A109 A119 A139 A149 A169
        A189 B06 B06T B407 B412 B427 B429 B505 B212 R22 R44 R66 S61 S76 S92 H60 H47 H64 UH1
        EH10 LYNX WILD NH90 PUMA MI8 MI17 MI24 G2CA H269 H500 MD52 MD60 EN28 EN48 B47G CH47
        AS35 H125 H130 H135 H145 H160 H175 TIGR
    """,
    Category.GLIDER: "GLID ASK21 ASK13 DG1T DG80 DUOD ARCP",
    Category.BALLOON: "BALL SHIP",
    Category.LIGHT: """
        C150 C152 C162 C170 C172 C175 C177 C180 C182 C185 C195 C205 C206 C207 C208 C210 P210
        P28A P28B P28R P28T P28U PA18 PA20 PA22 PA24 PA28 PA30 PA32 PA34 PA44 PA46 P46T
        SR20 SR22 S22T DA20 DA40 DA42 DA62 BE23 BE33 BE35 BE36 BE55 BE58 BE76 M20P M20T
        DR40 DR10 AA5 TB10 TB20 TB21 TOBA RV4 RV6 RV7 RV8 RV9 RV10 RV12 RV14 G115 G120 TEX2
        PC6 PC7 PC9 PC12 TBM7 TBM8 TBM9 EV97 C42 CTSW PIVI SUBA EXTR CAP2 CAP1 SPIT HURI
        DHC1 DH82 J3 PA38 AT3 BDOG TWEN COUR MOR2 GA8 A210 PUP SAVA
    """,
}

TYPE_CATEGORY: dict[str, Category] = {
    code: cat for cat, codes in _TYPES.items() for code in codes.split()
}

#: ADS-B emitter category -> silhouette when the type code is unknown.
EMITTER_CATEGORY: dict[str, Category] = {
    "A1": Category.LIGHT,        # light (< 15,500 lb)
    "A2": Category.BIZJET,       # small (15,500-75,000 lb)
    "A3": Category.NARROWBODY,   # large (75,000-300,000 lb)
    "A4": Category.NARROWBODY,   # high-vortex large (B757)
    "A5": Category.WIDEBODY,     # heavy (> 300,000 lb)
    "A6": Category.FIGHTER,      # high performance (> 5 g, > 400 kt)
    "A7": Category.HELICOPTER,   # rotorcraft
    "B1": Category.GLIDER,       # glider / sailplane
    "B2": Category.BALLOON,      # lighter than air
    "B4": Category.LIGHT,        # ultralight / hang-glider / paraglider
    "B6": Category.DRONE,        # UAV
}


def classify(type_code: str, emitter_category: str = "") -> Category:
    """Silhouette category from ICAO type designator, then ADS-B emitter category."""
    cat = TYPE_CATEGORY.get(type_code.strip().upper())
    if cat is not None:
        return cat
    return EMITTER_CATEGORY.get(emitter_category.strip().upper(), Category.UNKNOWN)
