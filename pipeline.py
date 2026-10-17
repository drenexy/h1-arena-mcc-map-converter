"""The conversion pipeline: MCC (v13) map -> Halo PC retail v7 map -> game maps\\.

Everything verified against the real toolchain:
  invader-extract -m <mcc halo1/maps> -t <tags> -O -s "*" <map>
  invader-build   -g gbx-retail -t <tags> -N <name> -o <out> <scenario>   (cwd = HCEEK dir)
Failures like CEA-schema stock tags are repaired by overlaying the PC-schema
copy of the failing tag from one of the game's own shipped v7 maps.
"""
import glob
import hashlib
import json
import os
import shutil
import subprocess
import time
import zipfile

import gate

APPDIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                      "h1mapcompanion")
INVADER_DIR = os.path.join(APPDIR, "invader")
TAGBASE = os.path.join(APPDIR, "tagbase")
JOBS = os.path.join(APPDIR, "jobs")

FLAG_FILE = "CREATE_NO_WINDOW" if os.name == "nt" else 0
NOCREATE = getattr(subprocess, FLAG_FILE, 0) if os.name == "nt" else 0


def log_line(msg, log):
    if log:
        log(msg)


def run(cmd, cwd=None, timeout=1800, log=None):
    log_line("$ " + " ".join(os.path.basename(str(c)) if i == 0 else str(c)
                             for i, c in enumerate(cmd)), log)
    t0 = time.time()
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, errors="replace",
                         creationflags=NOCREATE)
    out = ""
    try:
        while True:
            try:
                out = p.communicate(timeout=20)[0] or ""
                break
            except subprocess.TimeoutExpired:
                log_line(f"still working… {int(time.time() - t0)} s", log)
                if time.time() - t0 > timeout:
                    p.kill()
                    raise RuntimeError("timed out after " + str(timeout) + " s")
    except KeyboardInterrupt:
        p.kill()
        raise
    return p.returncode, out


# ---------------------------------------------------------------- setup ----

def find_invader_zip(game_dir, config):
    """Where is the Invader zip? Config wins, then the game package's tools\\."""
    cand = []
    if config.get("invader_zip"):
        cand.append(config["invader_zip"])
    if game_dir:
        cand += glob.glob(os.path.join(game_dir, "tools", "invader-*.zip"))
    for c in cand:
        if c and os.path.isfile(c):
            return c
    return None


def ensure_invader(zip_path, log=None):
    if os.path.isfile(os.path.join(INVADER_DIR, "invader-build.exe")):
        return INVADER_DIR
    os.makedirs(INVADER_DIR, exist_ok=True)
    log_line("first run: unpacking Invader (one time)...", log)
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".exe")]
        z.extractall(INVADER_DIR, members=[z.getinfo(n) for n in names])
    return INVADER_DIR


def ensure_tagbase(hceek_dir, log=None):
    """A private, refactored copy of the Mod Tools tags tree (one time)."""
    src = os.path.join(hceek_dir, "HCEEK", "tags")
    if not os.path.isdir(src):
        raise RuntimeError("Mod Tools are not extracted — run 'HCEEK (Extract).bat' "
                           "in the Mod Tools folder first, then retry.")
    marker = os.path.join(TAGBASE, "ready.json")
    if os.path.isfile(marker):
        return TAGBASE
    log_line("first run: preparing tag base from the Mod Tools (~3 min, one time)...", log)
    if os.path.isdir(TAGBASE):
        shutil.rmtree(TAGBASE, ignore_errors=True)
    rc, out = run(["robocopy", src, TAGBASE, "/E", "/NFL", "/NDL", "/NJH", "/NJS",
                   "/NP"], log=log, timeout=3600)
    if rc >= 8:
        raise RuntimeError("copying tags failed (robocopy " + str(rc) + ")")
    inv = INVADER_DIR
    run([os.path.join(inv, "invader-convert.exe"), "-g", "model", "gbxmodel",
         "-t", TAGBASE, "-b", "*"], cwd=hceek_dir, log=log)
    rc, out = run([os.path.join(inv, "invader-refactor.exe"), "-M", "no-move",
                   "-g", "model", "gbxmodel", "-t", TAGBASE],
                  cwd=hceek_dir, log=log)
    with open(marker, "w") as fh:
        json.dump({"source": hceek_dir}, fh)
    return TAGBASE


# --------------------------------------------------------------- convert ----

def _sync_job(job_tags, log):
    """Make job_tags match tagbase exactly (adds/removes), incrementally."""
    os.makedirs(job_tags, exist_ok=True)
    rc, out = run(["robocopy", TAGBASE, job_tags, "/MIR", "/NFL", "/NDL",
                   "/NJH", "/NJS", "/NP"], log=log, timeout=3600)
    if rc >= 8:
        raise RuntimeError("syncing job tags failed (robocopy " + str(rc) + ")")


def _scenario_after_extract(job_tags, before):
    """The map's own .scenario = a new file, else the newest .scenario."""
    now = set(glob.glob(os.path.join(job_tags, "**", "*.scenario"), recursive=True))
    new = [p for p in now - before]
    if len(new) == 1:
        return new[0]
    if new:
        return max(new, key=os.path.getmtime)
    if now:
        return max(now, key=os.path.getmtime)
    raise RuntimeError("no .scenario tag found after extract")


def _rel(path, tags_dir):
    r = os.path.relpath(path, tags_dir)
    return r[: -len(".scenario")] if r.endswith(".scenario") else r


_ERROR_PATTERNS = ("Failed to find ", "Failed to compile tag ")


def _failed_tags(output):
    out = set()
    for line in output.splitlines():
        line = line.strip()
        for pat in _ERROR_PATTERNS:
            if pat in line:
                tail = line.split(pat, 1)[1].strip()
                if tail and " " not in tail[:4]:
                    out.add(tail)
    return out


def _overlay(failed, job_tags, game_maps_dir, inv, hceek_dir, tried, log):
    """Pull PC-schema copies of the failing tags from the game's own maps."""
    fresh = failed - tried
    if not fresh:
        return False
    tried |= fresh
    for tag in sorted(fresh):
        for gm in sorted(glob.glob(os.path.join(game_maps_dir, "*.map"))):
            stem = os.path.splitext(os.path.basename(gm))[0].lower()
            if stem in gate.DENY or os.path.getsize(gm) < 0x800:
                continue
            rc, out = run([os.path.join(inv, "invader-extract.exe"), "-t", job_tags,
                           "-O", "-s", tag, gm],
                          cwd=hceek_dir, log=log, timeout=600)
            if "Extracted 0 out of 0" not in out and "Extracted " in out:
                log_line(f"  overlaid '{tag}' from {os.path.basename(gm)}", log)
                break
    return True


def convert_map(map_path, game_dir, hceek_dir, mcc_maps_dir, inv_zip,
                log=None):
    """Full pipeline. Returns dict(result). Raises RuntimeError on failure."""
    os.makedirs(JOBS, exist_ok=True)
    inv = ensure_invader(inv_zip, log)
    ensure_tagbase(hceek_dir, log)

    h = gate.read_header(map_path)
    if h is None or h["magic"] != b"daeh":
        raise RuntimeError("not a Halo cache file")
    if h["version"] == 7:
        return {"skipped": True, "name": h["name"],
                "note": "already v7 — copy it into the game's maps folder instead"}
    name = "".join(c for c in h["name"].lower() if c.isalnum() or c in " _-")[:31].strip() or \
        os.path.splitext(os.path.basename(map_path))[0][:31]

    job = os.path.join(JOBS, name.replace(" ", "_"))
    job_tags = os.path.join(job, "tags")
    log_line(f"preparing workspace for '{name}'...", log)
    _sync_job(job_tags, log)
    before = set(glob.glob(os.path.join(job_tags, "**", "*.scenario"), recursive=True))

    log_line("extracting tags...", log)
    cmd = [os.path.join(inv, "invader-extract.exe"), "-t", job_tags, "-O", "-s", "*"]
    if mcc_maps_dir:
        cmd += ["-m", mcc_maps_dir]
    cmd.append(map_path)
    rc, out = run(cmd, cwd=hceek_dir, log=log, timeout=1200)
    if "Extracted 0 out of" in out:
        raise RuntimeError("extract found no tags: " + out.strip()[-300:])

    scen = _rel(_scenario_after_extract(job_tags, before), job_tags)
    log_line(f"scenario tag: {scen}", log)

    out_map = os.path.join(job, "out.map")
    if os.path.isfile(out_map):
        os.remove(out_map)
    tried = set()
    for attempt in range(1, 9):
        log_line(f"building v7 map (attempt {attempt})...", log)
        rc, out = run([os.path.join(inv, "invader-build.exe"), "-g", "gbx-retail",
                       "-t", job_tags, "-N", name, "-o", out_map, scen],
                      cwd=hceek_dir, log=log, timeout=1500)
        if rc == 0 and os.path.isfile(out_map):
            ok, why = gate.gate(out_map)
            if not ok:
                raise RuntimeError("built map failed the game's own check: " + why)
            return {"name": name, "map": out_map, "attempts": attempt}
        failed = _failed_tags(out)
        if not failed or not _overlay(failed, job_tags, os.path.join(game_dir, "maps"),
                                      inv, hceek_dir, tried, log):
            detail = "\n".join(l for l in out.splitlines()
                               if "Failed" in l or "FATAL" in l)[:900]
            if "Failed to find" in out and any(x in out for x in (".bitmap", ".sound")):
                detail += ("\n\nThis map keeps some of its art or sound in MCC's "
                           "protected resource packs, which the converter cannot "
                           "read yet. Only maps that carry their own files (or use "
                           "standard game files) can convert today.")
            raise RuntimeError("build failed:\n" + detail)
    raise RuntimeError("build failed after 8 attempts")


# --------------------------------------------------------------- install ----

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_path(game_dir):
    return os.path.join(game_dir, "mapcompanion.json")


def load_manifest(game_dir):
    try:
        return json.load(open(manifest_path(game_dir)))
    except (OSError, ValueError):
        return {"maps": {}}


def save_manifest(game_dir, man):
    with open(manifest_path(game_dir), "w") as fh:
        json.dump(man, fh, indent=2)


def install_map(game_dir, src_map, name):
    """Copy into maps\\ with the game gate; collisions get _2, _3 ..."""
    maps_dir = os.path.join(game_dir, "maps")
    os.makedirs(maps_dir, exist_ok=True)
    stem = name[:31]
    dest = os.path.join(maps_dir, stem + ".map")
    n = 1
    while os.path.isfile(dest) and sha256(dest) != sha256(src_map):
        n += 1
        dest = os.path.join(maps_dir, f"{stem}_{n}.map")
    if not os.path.isfile(dest):
        shutil.copy2(src_map, dest)
    man = load_manifest(game_dir)
    man["maps"][os.path.basename(dest)] = {"name": name, "sha256": sha256(dest)}
    save_manifest(game_dir, man)
    return dest
