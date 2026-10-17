"""Header gate — a Python port of blampc_lobby_map_header_ok() from h1pb2
(src/host/blampc_lobby_maps.c). A map that passes here is listed and loaded
by the game; anything else is refused by the engine.

Engine-side original: cache_file_header_verify() reads 'head' at 0x00,
'foot' at 0x7FC, version == 7, size <= 0x18000000, name 1..31 chars.
The menu adds a denylist of resource-pack names.
"""
import os
import struct

DENY = {"bitmaps", "sounds", "loc", "ui"}


def read_header(path):
    """Return dict with magic/version/size/name/build or None if too small."""
    if not os.path.isfile(path) or os.path.getsize(path) < 0x800:
        return None
    with open(path, "rb") as fh:
        head = fh.read(0x800)
    name = head[0x20:0x40].split(b"\0")[0]
    build = head[0x40:0x60].split(b"\0")[0]
    try:
        name = name.decode("ascii", "replace")
        build = build.decode("ascii", "replace")
    except Exception:
        pass
    return {
        "magic": head[:4],
        "version": struct.unpack("<I", head[4:8])[0],
        "raw_ver": head[4:8],
        "size": struct.unpack("<I", head[8:12])[0],
        "foot": head[0x7FC:0x800],
        "name": name,
        "build": build,
    }


def gate(path):
    """Check the file against the game's rules.
    Returns (ok: bool, why: str). why=="" when ok."""
    h = read_header(path)
    if h is None:
        return False, "too small to be a cache"
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem.lower() in DENY:
        return False, "that name is a resource pack, not a playable map"
    if h["magic"] != b"daeh":
        return False, "not a little-endian cache (no 'head' signature)"
    if h.get("raw_ver") == b"\x05\x00\x00\x00":
        return False, ("this is an original Xbox map (big files from a disc dump) — "
                       "this game can only use PC-format maps")
    if h["foot"] != b"toof\0\0\0\0"[:4]:
        return False, "no 'foot' signature — truncated or still copying"
    if h["version"] != 7:
        extra = " (that is Halo Custom Edition; this port reads retail)" if h["version"] == 609 else \
                " (that is MCC/Anniversary format)" if h["version"] == 13 else ""
        return False, f"cache version {h['version']}, not 7{extra}"
    if h["size"] > 0x18000000:
        return False, f"header says {h['size']} bytes, past the engine's limit"
    n = len(h["name"])
    if n == 0 or n > 31:
        return False, "scenario name is empty or longer than 31 characters"
    return True, ""


def classify(path):
    """Short label for the UI list."""
    h = read_header(path)
    if h is None:
        return "not a map"
    ok, why = gate(path)
    if ok:
        if not h["build"].lower().startswith("invader"):
            return "OK (art may be missing)"
        return "OK"
    return f"v{h['version']} — won't load" if h["magic"] == b"daeh" else "not a map"
