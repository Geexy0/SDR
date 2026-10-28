"""Public rtl_433 TPMS model → OEM / make hints (not VIN / plate lookup)."""

from __future__ import annotations

from typing import Any

# (needle in model/protocol blob, oem_family, suggested_make, suggested_model_hint, note)
_OEM_RULES: tuple[tuple[str, str, str, str | None, str], ...] = (
    ("steelmate", "Steelmate", "Steelmate", "aftermarket TPMS", "Aftermarket"),
    ("schrader", "Schrader", "Schrader/Sensata", None, "Common OEM / aftermarket family"),
    ("eg53ma4", "Schrader EG53MA4", "Schrader", "EG53MA4", "Schrader EG53MA4"),
    ("smd3ma4", "Schrader SMD3MA4", "Nissan/Infiniti/Renault/Subaru", "SMD3MA4", "Multi-brand Schrader"),
    ("toyota", "Toyota/Lexus", "Toyota", "Pacific-style TPMS", "Toyota/Lexus family"),
    ("pmv-107j", "Toyota PMV-107J", "Toyota", "PMV-107J", "Toyota PMV-107J"),
    ("pmv107j", "Toyota PMV-107J", "Toyota", "PMV-107J", "Toyota PMV-107J"),
    ("ford", "Ford", "Ford", None, "Ford TPMS family"),
    ("citroen", "Citroën/Peugeot PSA", "Citroën/Peugeot", None, "PSA TPMS"),
    ("renault", "Renault", "Renault", None, "Renault TPMS"),
    ("0435r", "Renault 0435R", "Renault", "0435R", "Renault 0435R"),
    ("hyundai", "Hyundai/Kia", "Hyundai", None, "Hyundai/Kia (VDO)"),
    ("elantra", "Hyundai Elantra", "Hyundai", "Elantra-class", "Elantra2012-style"),
    ("kia", "Kia", "Kia", None, "Kia TPMS"),
    ("nissan", "Nissan", "Nissan", None, "Nissan TPMS"),
    ("abarth", "Abarth/Fiat", "Abarth/Fiat", "124 Spider-class", "Abarth 124"),
    ("porsche", "Porsche", "Porsche", "Boxster/Cayman-class", "Porsche Boxster/Cayman"),
    ("bmw", "BMW", "BMW", None, "BMW Gen3–Gen5"),
    ("huf", "HUF/Beru/Continental", "VW/Audi/BMW/multi", None, "HUF/Beru multi-brand"),
    ("continental", "Continental", "Continental OEM", None, "Continental"),
    ("audi", "VW/Audi/Skoda", "Audi", None, "VAG / Audi"),
    ("skoda", "VW/Audi/Skoda", "Skoda", None, "VAG"),
    ("volkswagen", "VW/Audi/Skoda", "Volkswagen", None, "VAG"),
    ("gm-aftermarket", "GM aftermarket", "GM", "aftermarket", "GM aftermarket"),
    ("chevrolet", "GM/Chevrolet", "Chevrolet", None, "GM"),
    ("jansite", "Jansite", "Jansite", "aftermarket", "Aftermarket"),
    ("eeztire", "EezTire/Carchet", "EezTire", "E618/Carchet", "Aftermarket truck/car"),
    ("carchet", "EezTire/Carchet", "Carchet", None, "Aftermarket"),
    ("tyreguard", "TyreGuard", "TyreGuard", "400", "Aftermarket"),
    ("ave tpms", "AVE", "AVE", None, "AVE TPMS"),
    ("solartpms", "Solar TPMS", "unbranded", "solar truck", "Truck solar TPMS"),
)


def oem_hint_from_payload(payload: dict[str, Any]) -> dict[str, str | None]:
    blob = " ".join(
        str(payload.get(k) or "")
        for k in ("model", "protocol", "type", "brand", "make")
    ).lower()
    for needle, oem, make, model_hint, note in _OEM_RULES:
        if needle in blob:
            return {
                "oem_family": oem,
                "suggested_make": make,
                "suggested_model": model_hint,
                "oem_note": note,
            }
    return {
        "oem_family": None,
        "suggested_make": None,
        "suggested_model": None,
        "oem_note": None,
    }


def extract_sensor_id(payload: dict[str, Any]) -> str | None:
    for key in ("id", "sensor_id", "sid", "ID"):
        if key in payload and payload[key] not in (None, ""):
            raw = str(payload[key]).strip()
            if raw:
                if raw.lower().startswith("0x"):
                    raw = raw[2:]
                return raw.upper()
    return None


def is_tpms_payload(payload: dict[str, Any]) -> bool:
    typ = str(payload.get("type") or "").lower()
    if typ == "tpms":
        return True
    model = str(payload.get("model") or "").lower()
    if "tpms" in model:
        return True
    if any(k in payload for k in ("pressure_kPa", "pressure_PSI", "pressure_bar")):
        return extract_sensor_id(payload) is not None
    return False


# rtl_433 protocol indices dedicated to TPMS (from `rtl_433 -R help`)
TPMS_PROTOCOL_IDS: tuple[int, ...] = (
    59,
    60,
    82,
    88,
    89,
    90,
    95,
    110,
    123,
    140,
    156,
    168,
    180,
    186,
    201,
    203,
    208,
    212,
    225,
    226,
    241,
    248,
    252,
    257,
    275,
)
