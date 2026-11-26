"""VIN validation and offline decoding (ISO 3779 / 49 CFR 565).

Decodes what is standardised: maker (WMI), model year, plant, serial and the
North-American check digit. Model/trim/engine are manufacturer-specific and come
from the user (or a VIN database) - we never guess them.
"""
from __future__ import annotations

_TRANSLIT = {**{str(d): d for d in range(10)},
             **dict(zip("ABCDEFGH", range(1, 9))), **dict(zip("JKLMN", range(1, 6))), "P": 7, "R": 9,
             **dict(zip("STUVWXYZ", range(2, 10)))}
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"   # 30-year cycle

WMI = {
    "1FT": "Ford (USA, truck)", "1FA": "Ford (USA)", "1FM": "Ford (USA, MPV)", "1FD": "Ford (USA, incomplete)",
    "2FM": "Ford (Canada, MPV)", "3FA": "Ford (Mexico)", "WF0": "Ford (Germany)",
    "1G1": "Chevrolet (USA)", "1GC": "Chevrolet (USA, truck)", "1GT": "GMC (USA, truck)", "2G1": "Chevrolet (Canada)",
    "1HG": "Honda (USA)", "JHM": "Honda (Japan)", "2HG": "Honda (Canada)",
    "JT2": "Toyota (Japan)", "JTD": "Toyota (Japan)", "4T1": "Toyota (USA)", "5TD": "Toyota (USA, MPV)",
    "5YJ": "Tesla (USA)", "1N4": "Nissan (USA)", "JN1": "Nissan (Japan)",
    "WBA": "BMW (Germany)", "WBS": "BMW M (Germany)", "WDB": "Mercedes-Benz (Germany)", "WDD": "Mercedes-Benz (Germany)",
    "WVW": "Volkswagen (Germany)", "WVG": "Volkswagen (Germany, SUV)", "3VW": "Volkswagen (Mexico)",
    "WAU": "Audi (Germany)", "WP0": "Porsche (Germany)", "KMH": "Hyundai (Korea)", "KNA": "Kia (Korea)",
    "SAL": "Land Rover (UK)", "SAJ": "Jaguar (UK)", "YV1": "Volvo (Sweden)", "ZFA": "Fiat (Italy)",
    "VF1": "Renault (France)", "VF3": "Peugeot (France)", "MA1": "Mahindra (India)", "MAT": "Tata (India)",
    "MA3": "Maruti Suzuki (India)", "MAL": "Hyundai (India)", "1C4": "Chrysler/Jeep (USA)", "1J4": "Jeep (USA)",
    "2T1": "Toyota (Canada)", "JM1": "Mazda (Japan)", "JF1": "Subaru (Japan)",
}

FORD_PLANTS = {"F": "Dearborn, Michigan", "K": "Kansas City, Missouri", "E": "Kentucky Truck", "L": "Michigan Assembly"}


def normalise(vin: str) -> str:
    return "".join(ch for ch in (vin or "").upper() if ch.isalnum())


def is_valid_format(vin: str) -> bool:
    v = normalise(vin)
    return len(v) == 17 and not any(c in v for c in "IOQ") and all(c in _TRANSLIT for c in v)


def check_digit(vin: str) -> str:
    total = sum(_TRANSLIT[c] * w for c, w in zip(normalise(vin), _WEIGHTS))
    r = total % 11
    return "X" if r == 10 else str(r)


def model_year(vin: str) -> int | None:
    v = normalise(vin)
    if len(v) != 17 or v[9] not in _YEAR_CODES:
        return None
    base = _YEAR_CODES.index(v[9])
    # North-American rule: a letter in position 7 means the 2010-2039 cycle.
    return (2010 if v[6].isalpha() else 1980) + base


def decode(vin: str) -> dict:
    v = normalise(vin)
    if not is_valid_format(v):
        return {"vin": v, "valid": False, "error": "A VIN has 17 letters and digits, without I, O or Q."}
    wmi = v[:3]
    maker = WMI.get(wmi) or WMI.get(wmi[:2] + "*") or "Unknown maker"
    north_american = v[0] in "12345"
    cd_ok = check_digit(v) == v[8]
    plant = FORD_PLANTS.get(v[10]) if maker.startswith("Ford") else None
    return {
        "vin": v,
        "valid": True,
        "maker": maker,
        "make": maker.split(" (")[0],
        "year": model_year(v),
        "check_digit_ok": cd_ok if north_american else None,
        "parts": [
            {"text": v[:3], "label": "Maker", "meaning": maker},
            {"text": v[3:8], "label": "Model", "meaning": "model, body and engine (maker-specific)"},
            {"text": v[8], "label": "Check digit",
             "meaning": ("valid" if cd_ok else "does not match") if north_american else "not used in this region"},
            {"text": v[9], "label": "Year", "meaning": str(model_year(v) or "unknown")},
            {"text": v[10], "label": "Plant", "meaning": plant or "maker-specific code"},
            {"text": v[11:], "label": "Serial", "meaning": "production number"},
        ],
    }
