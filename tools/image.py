"""Strict, version-bound Cortex-M0 call-site and literal patching."""
from dataclasses import dataclass
import hashlib
import struct


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def thumb_bl(source, target):
    """ARMv6-M BL T1; addresses are instruction addresses, not function pointers."""
    if not 0 <= source <= 0xFFFFFFFB or not 0 <= target <= 0xFFFFFFFF:
        raise ValueError("BL address outside 32-bit address space")
    if source & 1 or target & 1:
        raise ValueError("BL requires aligned instruction addresses")
    delta = target - source - 4
    if not -(1 << 24) <= delta < (1 << 24):
        raise ValueError("BL target out of range")
    bits = delta & 0x1FFFFFF
    sign = (bits >> 24) & 1
    j1 = 1 ^ ((bits >> 23) & 1) ^ sign
    j2 = 1 ^ ((bits >> 22) & 1) ^ sign
    return struct.pack("<HH", 0xF000 | sign << 10 | (bits >> 12) & 0x3FF,
                       0xD000 | j1 << 13 | j2 << 11 | (bits >> 1) & 0x7FF)


def bl_target(source, data):
    if source < 0 or source > 0xFFFFFFFB or source & 1 or len(data) != 4:
        raise ValueError("BL requires an aligned address and four instruction bytes")
    a, b = struct.unpack("<HH", data)
    if a & 0xF800 != 0xF000 or b & 0xD000 != 0xD000:
        raise ValueError("Not a BL T1 instruction")
    sign = (a >> 10) & 1
    i1 = 1 ^ ((b >> 13) & 1) ^ sign
    i2 = 1 ^ ((b >> 11) & 1) ^ sign
    delta = sign << 24 | i1 << 23 | i2 << 22 | (a & 0x3FF) << 12 | (b & 0x7FF) << 1
    if sign:
        delta -= 1 << 25
    return source + 4 + delta


@dataclass(frozen=True)
class Edit:
    offset: int
    before: bytes
    after: bytes
    purpose: str


def patch_image(original, expected_hash, edits, payload, payload_address, limit,
                protected_ranges=()):
    protected_ranges = tuple(protected_ranges)
    if sha256(original) != expected_hash:
        raise ValueError("Base firmware SHA-256 mismatch")
    if (not payload or payload_address < len(original) or limit > 0x100000000
            or payload_address + len(payload) > limit):
        raise ValueError("Payload overlaps original image or reserved storage")
    for start, end in protected_ranges:
        if not 0 <= start < end <= 0x100000000:
            raise ValueError("Invalid protected range")
        if len(original) < end and start < payload_address + len(payload):
            raise ValueError("Image extension intersects a protected range")
    occupied = set()
    result = bytearray(original)
    for edit in edits:
        if len(edit.before) != len(edit.after) or not edit.before:
            raise ValueError("An edit must have equal, nonzero old/new lengths")
        if edit.offset < 0 or edit.offset + len(edit.before) > len(original):
            raise ValueError(f"Edit outside original image: {edit.offset:#x}")
        if any(edit.offset < end and start < edit.offset + len(edit.before)
               for start, end in protected_ranges):
            raise ValueError(f"Edit intersects a protected range: {edit.offset:#x}")
        span = set(range(edit.offset, edit.offset + len(edit.before)))
        if occupied & span:
            raise ValueError("Overlapping edits")
        occupied |= span
        if original[edit.offset:edit.offset + len(edit.before)] != edit.before:
            raise ValueError(f"Original bytes differ at {edit.offset:#x}: {edit.purpose}")
        result[edit.offset:edit.offset + len(edit.after)] = edit.after
    result.extend(b"\xff" * (payload_address - len(result)))
    result.extend(payload)
    return bytes(result)
