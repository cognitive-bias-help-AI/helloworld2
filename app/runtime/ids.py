"""Canonical runtime identifier generation."""

from __future__ import annotations

import secrets
import time

CROCKFORD_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_ulid() -> str:
    """Generate a 48-bit millisecond timestamp + 80-bit random ULID."""
    timestamp = int(time.time() * 1000) & ((1 << 48) - 1)
    randomness = int.from_bytes(secrets.token_bytes(10), "big")
    value = (timestamp << 80) | randomness
    encoded = ["0"] * 26
    for index in range(25, -1, -1):
        encoded[index] = CROCKFORD_ULID_ALPHABET[value & 31]
        value >>= 5
    return "".join(encoded)


__all__ = ["CROCKFORD_ULID_ALPHABET", "generate_ulid"]
