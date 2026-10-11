"""Operator names from callsigns: ICAO airline designators plus military fallbacks.

An airline flight's callsign is the operator's 3-letter ICAO designator followed
by a flight number (``BAW123`` -> British Airways). Military aircraft often fly
tactical callsigns instead, so for those we fall back to the ICAO 24-bit
address block the airframe is registered in. No Qt, no network.
"""

from __future__ import annotations

import re

#: ICAO airline designator -> short display name.
AIRLINES: dict[str, str] = {
    # --- UK & Ireland -------------------------------------------------------
    "BAW": "British Airways", "SHT": "British Airways", "CFE": "BA CityFlyer",
    "EZY": "easyJet", "EJU": "easyJet Europe", "EZS": "easyJet Switzerland",
    "RYR": "Ryanair", "RUK": "Ryanair UK", "MAY": "Malta Air", "LDM": "Lauda Europe",
    "VIR": "Virgin Atlantic", "TOM": "TUI Airways", "EXS": "Jet2", "LOG": "Loganair",
    "EIN": "Aer Lingus", "EUK": "Aer Lingus UK", "EAI": "Emerald Airlines", "AUR": "Aurigny",
    "BCY": "CityJet", "BEE": "Flybe", "WUK": "Wizz Air UK", "AWC": "Titan Airways",
    "NPT": "West Atlantic", "DHK": "DHL Air UK", "VJT": "VistaJet", "NJE": "NetJets Europe",
    "EFW": "BA Euroflyer",
    # --- Europe ---------------------------------------------------------------
    "AFR": "Air France", "HOP": "Air France Hop", "KLM": "KLM", "KLC": "KLM Cityhopper",
    "TRA": "Transavia", "TVF": "Transavia France", "DLH": "Lufthansa", "CLH": "Lufthansa CityLine",
    "EWG": "Eurowings", "CFG": "Condor", "DLA": "Air Dolomiti", "SWR": "Swiss", "AUA": "Austrian",
    "BEL": "Brussels Airlines", "SAS": "SAS", "NOZ": "Norwegian", "NAX": "Norwegian",
    "NSZ": "Norwegian", "FIN": "Finnair", "IBE": "Iberia", "IBS": "Iberia Express",
    "VLG": "Vueling", "AEA": "Air Europa", "TAP": "TAP Air Portugal", "ITY": "ITA Airways",
    "ICE": "Icelandair", "LOT": "LOT Polish", "CSA": "Czech Airlines", "AEE": "Aegean",
    "OAL": "Olympic Air", "ROT": "TAROM", "LGL": "Luxair", "CTN": "Croatia Airlines",
    "ASL": "Air Serbia", "BTI": "airBaltic", "VOE": "Volotea", "WZZ": "Wizz Air",
    "WMT": "Wizz Air Malta", "SXS": "SunExpress", "ENT": "Enter Air", "TVS": "Smartwings",
    "THY": "Turkish Airlines", "PGT": "Pegasus", "AFL": "Aeroflot", "MPH": "Martinair",
    "TUI": "TUI fly", "TFL": "TUI fly", "JAF": "TUI fly Belgium", "EDW": "Edelweiss",
    "GWI": "Germanwings", "BCS": "DHL", "BOX": "AeroLogic", "CLX": "Cargolux",
    # --- Middle East, Africa, Asia, Oceania -------------------------------------
    "UAE": "Emirates", "QTR": "Qatar Airways", "ETD": "Etihad", "SVA": "Saudia",
    "GFA": "Gulf Air", "OMA": "Oman Air", "KAC": "Kuwait Airways", "RJA": "Royal Jordanian",
    "MEA": "Middle East Airlines", "FDB": "flydubai", "ABY": "Air Arabia", "ELY": "El Al",
    "MSR": "EgyptAir", "RAM": "Royal Air Maroc", "ETH": "Ethiopian", "KQA": "Kenya Airways",
    "SAA": "South African", "AIC": "Air India", "AXB": "Air India Express", "IGO": "IndiGo",
    "PIA": "PIA", "UZB": "Uzbekistan Airways", "SIA": "Singapore Airlines",
    "CPA": "Cathay Pacific", "JAL": "Japan Airlines", "ANA": "ANA", "KAL": "Korean Air",
    "AAR": "Asiana", "CCA": "Air China", "CES": "China Eastern", "CSN": "China Southern",
    "CAL": "China Airlines", "EVA": "EVA Air", "HVN": "Vietnam Airlines", "PAL": "Philippine Airlines",
    "MAS": "Malaysia Airlines", "THA": "Thai Airways", "GIA": "Garuda Indonesia",
    "QFA": "Qantas", "ANZ": "Air New Zealand", "ALK": "SriLankan",
    # --- Americas -------------------------------------------------------------
    "AAL": "American Airlines", "UAL": "United", "DAL": "Delta", "JBU": "JetBlue",
    "SWA": "Southwest", "ASA": "Alaska Airlines", "NKS": "Spirit", "FFT": "Frontier",
    "ACA": "Air Canada", "WJA": "WestJet", "TSC": "Air Transat", "AMX": "Aeromexico",
    "AVA": "Avianca", "LAN": "LATAM", "TAM": "LATAM Brasil", "GLO": "Gol", "AZU": "Azul",
    "CMP": "Copa", "FDX": "FedEx", "UPS": "UPS", "GTI": "Atlas Air", "CKS": "Kalitta Air",
    "EJA": "NetJets",
    # --- Military / government ----------------------------------------------------
    "RRR": "Royal Air Force", "RCH": "US Air Force", "CNV": "US Navy", "PAT": "US Army",
    "CFC": "Canadian Forces", "GAF": "German Air Force", "CTM": "French Air Force",
    "FAF": "French Air Force", "IAM": "Italian Air Force", "BAF": "Belgian Air Force",
    "NAF": "Netherlands Air Force", "ASY": "Royal Australian AF", "PLF": "Polish Air Force",
}

#: ICAO 24-bit address blocks of military airframes: (first, last, name).
MILITARY_HEX_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x43C000, 0x43CFFF, "UK Military"),
    (0xADF7C8, 0xAFFFFF, "US Military"),
)

_CALLSIGN = re.compile(r"^([A-Z]{3})\d")


def airline_code(callsign: str) -> str | None:
    """The 3-letter designator if ``callsign`` looks like an airline flight (``BAW12K``)."""
    m = _CALLSIGN.match(callsign.strip().upper())
    return m.group(1) if m else None


def operator_name(callsign: str, hex_id: str = "", military: bool = False) -> str | None:
    """Best display name for who operates the aircraft, or None if unknown."""
    code = airline_code(callsign)
    if code and code in AIRLINES:
        return AIRLINES[code]
    if military:
        try:
            addr = int(hex_id, 16)
        except ValueError:
            addr = -1
        for lo, hi, name in MILITARY_HEX_RANGES:
            if lo <= addr <= hi:
                return name
        return "Military"
    return None
