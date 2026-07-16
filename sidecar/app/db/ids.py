"""UUIDv7 generation — time-sortable primary keys (database-design §2/§3).

Python 3.13 has no `uuid.uuid7`; this is a minimal RFC 9562 implementation:
48-bit Unix-ms timestamp in the high bits (so PKs sort by creation time), the
version/variant nibbles, and random fill. Good enough for a single-user local
DB — collision risk is negligible and the sortability is what the boot
re-enqueue sweep and the cost dashboard's `(kind, created_at)` reads want.
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> str:
    """A UUIDv7 as a canonical string. Monotonic-ish, sorts by creation time."""
    unix_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")  # 80 random bits
    rand_a = (rand >> 68) & 0xFFF  # 12 bits
    rand_b = rand & ((1 << 62) - 1)  # 62 bits
    value = (
        (unix_ms << 80)
        | (0x7 << 76)  # version
        | (rand_a << 64)
        | (0b10 << 62)  # variant
        | rand_b
    )
    return str(uuid.UUID(int=value))
