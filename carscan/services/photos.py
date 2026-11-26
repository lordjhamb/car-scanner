"""Photo inspection: the shot list, safe image handling, and coverage checks.

Uploaded images are decoded with Pillow, re-encoded as JPEG (which strips EXIF
metadata such as GPS location), and capped in size before they are stored or
sent to an AI provider.
"""
from __future__ import annotations

import base64
import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
STORE_MAX_PX = 2048
AI_MAX_PX = 1568
Image.MAX_IMAGE_PIXELS = 60_000_000   # refuse decompression bombs

# (slot id, group, name, how to take it, why it matters)
SHOT_LIST = [
    ("ext_front", "Outside, all around", "Front", "Straight on, whole car in frame", "Bumper, lights, grille damage"),
    ("ext_front_left", "Outside, all around", "Front-left corner", "45°, kneel slightly", "Panel gaps and paint match"),
    ("ext_left", "Outside, all around", "Left side", "Whole length", "Dents and paint along the side"),
    ("ext_rear_left", "Outside, all around", "Rear-left corner", "45°, whole car in frame", "Rear quarter damage"),
    ("ext_rear", "Outside, all around", "Rear", "Straight on", "Bumper, lights, tow damage"),
    ("ext_rear_right", "Outside, all around", "Rear-right corner", "45°", "Rear quarter damage"),
    ("ext_right", "Outside, all around", "Right side", "Whole length", "Dents and paint along the side"),
    ("ext_front_right", "Outside, all around", "Front-right corner", "45°", "Panel gaps and paint match"),
    ("tyre_fl", "Close-ups", "Front-left tyre tread", "Coin or gauge in the groove", "Tread depth and wear pattern"),
    ("tyre_fr", "Close-ups", "Front-right tyre tread", "Coin or gauge in the groove", "Tread depth and wear pattern"),
    ("tyre_rl", "Close-ups", "Rear-left tyre tread", "Coin or gauge in the groove", "Tread depth and wear pattern"),
    ("tyre_rr", "Close-ups", "Rear-right tyre tread", "Coin or gauge in the groove", "Tread depth and wear pattern"),
    ("tyre_dot", "Close-ups", "Tyre DOT codes", "The 4 digits after “DOT” on the sidewall", "Tyre age, for the mileage check"),
    ("panel_gaps", "Close-ups", "Hood and fender gaps", "Along the panel edges", "Signs of accident repair"),
    ("int_dash", "Inside", "Dash, ignition on", "All warning lights visible", "Warning lights that stay on"),
    ("int_odo", "Inside", "Odometer close-up", "Readable digits", "Confirms the reading you typed"),
    ("int_driver_seat", "Inside", "Driver seat", "Door open for light", "Wear that indicates mileage"),
    ("int_wheel_pedals", "Inside", "Steering wheel and pedals", "", "Wear that indicates mileage"),
    ("int_rear", "Inside", "Rear seats", "", "Damage and stains"),
    ("int_headliner", "Inside", "Headliner", "Look for water stains", "Leaks, flood damage"),
    ("int_cargo", "Inside", "Boot or truck bed", "", "Damage, rust, water"),
    ("eng_bay", "Under the hood", "Engine bay", "Whole bay", "Leaks, repairs, missing parts"),
    ("eng_fluids", "Under the hood", "Fluid caps and reservoirs", "", "Oil and coolant condition"),
    ("eng_vin", "Under the hood", "VIN plate", "", "Matches the VIN read from the car"),
    ("under_front", "Underneath", "Front, behind the bumper", "Phone flat on the ground, flash on", "Rust, leaks, damage"),
    ("under_middle", "Underneath", "Middle, under the cabin", "Phone flat on the ground, flash on",
     "Frame rust, fuel and brake lines"),
    ("under_rear", "Underneath", "Rear axle and exhaust", "Hold still for a second", "Leaks at the axle, exhaust rust"),
    ("doc_service", "Paperwork", "Service book or stickers", "", "Mileage history"),
    ("doc_door_label", "Paperwork", "Door-jamb label", "Driver's door frame sticker", "Build date, original tyre size"),
]
SLOTS = {s[0]: {"slot": s[0], "group": s[1], "name": s[2], "how": s[3], "why": s[4]} for s in SHOT_LIST}
UNSORTED = "unsorted"


class PhotoError(ValueError):
    pass


def process_upload(raw: bytes, max_bytes: int) -> tuple[bytes, int, int]:
    """Validate and normalise an uploaded image. Returns (jpeg_bytes, width, height)."""
    if not raw:
        raise PhotoError("The file is empty.")
    if len(raw) > max_bytes:
        raise PhotoError(f"Photos must be under {max_bytes // (1024 * 1024)} MB.")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            probe.verify()
        if fmt not in ALLOWED_FORMATS:
            raise PhotoError("Use JPEG, PNG or WebP photos.")
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((STORE_MAX_PX, STORE_MAX_PX))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=88, optimize=True)   # no exif= -> metadata stripped
            return out.getvalue(), img.width, img.height
    except PhotoError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError) as e:
        raise PhotoError("That file isn't a readable image.") from e


def save(upload_root: Path, user_id: int, car_id: int, data: bytes) -> str:
    folder = upload_root / str(user_id) / str(car_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    (folder / name).write_bytes(data)
    return name


def path_for(upload_root: Path, user_id: int, car_id: int, filename: str) -> Path:
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise PhotoError("Bad file name.")
    return upload_root / str(user_id) / str(car_id) / filename


def for_ai(path: Path) -> str:
    """Base64 JPEG scaled for vision models."""
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((AI_MAX_PX, AI_MAX_PX))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85)
    return base64.standard_b64encode(out.getvalue()).decode()


def coverage(photos: list[dict]) -> dict:
    have = {p["slot"] for p in photos}
    missing = [SLOTS[s] for s in SLOTS if s not in have]
    return {"total": len(SLOTS), "done": len(SLOTS) - len(missing), "missing": missing,
            "unsorted": sum(1 for p in photos if p["slot"] == UNSORTED)}
