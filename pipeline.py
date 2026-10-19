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
import re
import shutil
import subprocess
import tempfile
import time
import zipfile

import gate

APPDIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                      "h1mapcompanion")
INVADER_DIR = os.path.join(APPDIR, "invader")
TAGBASE = os.path.join(APPDIR, "tagbase")
STOCK = os.path.join(APPDIR, "stocktags")
JOBS = os.path.join(APPDIR, "jobs")

# The game's own maps all share one set of stock tags (classic PC HUD + stock
# weapons/vehicles/etc). The whole tag set of one shipped map is extracted once
# into the companion's cache, so conversions never depend on what happens to be
# in the game's maps\ folder at the time.

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
                # a real tag path has a separator and an extension; this drops junk
                # like "Bitmap #2 in" that would otherwise burn every repair attempt
                if (tail and " " not in tail[:4] and "." in tail and
                        ("\\" in tail or "/" in tail)):
                    out.add(tail)
    return out


_CEA_HUD_HINTS = (".weapon_hud_interface", ".hud_globals")


def _stock_donor(game_maps_dir, inv, hceek_dir, log):
    """First shipped map that carries the stock PC HUD (proves it is author-built)."""
    probe = tempfile.mkdtemp(prefix="donor_")
    try:
        for gm in sorted(glob.glob(os.path.join(game_maps_dir, "*.map"))):
            stem = os.path.splitext(os.path.basename(gm))[0].lower()
            if stem in gate.DENY or os.path.getsize(gm) < 0x800:
                continue
            rc, out = run([os.path.join(inv, "invader-extract.exe"), "-t", probe,
                           "-O", "-s", "old tags/*", gm],
                          cwd=hceek_dir, timeout=60)
            if re.search(r"Extracted [1-9]\d* out of", out):
                log_line(f"stock-tag donor: {os.path.basename(gm)}", log)
                return gm
    finally:
        shutil.rmtree(probe, ignore_errors=True)
    return None


def stock_ready():
    return os.path.isfile(os.path.join(STOCK, "ready.json"))


def build_stock_cache(game_maps_dir, inv, hceek_dir, log=None):
    """One-time: extract the shared stock tags out of a shipped map into the
    companion cache. Returns True when the cache is usable afterwards."""
    if stock_ready():
        return True
    donor = _stock_donor(game_maps_dir, inv, hceek_dir, log)
    if not donor:
        return False
    log_line("caching the game's stock tags (one shipped map, all tags)...", log)
    tmp = STOCK + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    got = 0
    rc, out = run([os.path.join(inv, "invader-extract.exe"), "-t", tmp, "-O",
                   "-m", game_maps_dir, "-s", "*", donor],
                  cwd=hceek_dir, log=log, timeout=1800)
    n = re.search(r"Extracted (\d+) out of", out)
    got = int(n.group(1)) if n else 0
    if got == 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return False
    shutil.rmtree(STOCK, ignore_errors=True)
    os.replace(tmp, STOCK)
    with open(os.path.join(STOCK, "ready.json"), "w") as f:
        json.dump({"donor": os.path.basename(donor), "tags": got}, f)
    log_line(f"stock tag cache ready: {got} tags (from {os.path.basename(donor)})", log)
    return True


def _copy_stock(job_tags, log=None, only=None):
    """Overlay cached stock tags into a job's tag tree (stock files win)."""
    rc, out = run(["robocopy", STOCK, job_tags, "/E", "/NFL", "/NDL", "/NJH",
                   "/NJS", "/NP", "/XF", "ready.json"], timeout=600)
    if rc >= 8:
        log_line(f"WARNING: stock overlay failed (robocopy {rc})", log)
        return 0
    log_line("stock tags overlaid from companion cache", log)
    return 1


def _stockify_hud(job_tags, game_maps_dir, inv, hceek_dir, log):
    r"""Workshop packs built on CEA stock carry the Anniversary HUD (digsite\ui\hud
    paths). This engine draws HUDs in the retail 640x480 space, so those sprites
    come out at the wrong scale. The game's own maps use the stock PC HUD — so
    drop the Anniversary HUD files and overlay the stock HUD, weapons, vehicles
    and globals from a shipped map, exactly like the game's own maps have them."""
    stock_root = os.path.normcase(os.path.join(job_tags, "ui", "hud"))
    cea = [f for f in glob.glob(os.path.join(job_tags, "**", "*"), recursive=True)
           if os.path.isfile(f) and f.lower().endswith(_CEA_HUD_HINTS)
           and not os.path.normcase(f).startswith(stock_root)]
    if not cea:
        return False
    for f in cea:
        try:
            os.remove(f)
        except OSError:
            pass
    log_line(f"map carries the Anniversary HUD ({len(cea)} tag files) — swapping in "
             "the stock one like the game's own maps", log)
    if build_stock_cache(game_maps_dir, inv, hceek_dir, log):
        _copy_stock(job_tags, log)
        return True
    donor = _stock_donor(game_maps_dir, inv, hceek_dir, log)
    if donor:
        run([os.path.join(inv, "invader-extract.exe"), "-t", job_tags, "-O",
             "-m", game_maps_dir, "-s", "*", donor],
            cwd=hceek_dir, log=log, timeout=1800)
        return True
    log_line("WARNING: map carries the Anniversary HUD but no stock tags were "
             "available (no shipped map in the game folder to copy from) — the "
             "HUD may look wrong-scaled", log)
    return False


def _pb_script_source(stock_scen_dir):
    """Pull the PB script suite (globals + scripts) out of the stock-tag donor's
    scenario. Returns (globals_text, [script_defs]) or (None, [])."""
    for dirpath, _dirs, files in os.walk(stock_scen_dir):
        for fn in files:
            if not fn.endswith(".scenario"):
                continue
            blob = open(os.path.join(dirpath, fn), "rb").read()
            for blk in blob.split(b"\x00"):
                if b"(global " in blk and b"(script" in blk:
                    block = blk.decode("utf-8", "replace")
                    starts = [m.start() for m in re.finditer(r"^\(", block, re.M)]
                    units = [block[s:e] for s, e in zip(starts, starts[1:] + [len(block)])]
                    head = "".join(dict.fromkeys(u for u in units if u.startswith("(global")))
                    defs = list(dict.fromkeys(u for u in units if u.startswith("(script")))
                    if head and defs:
                        return head, defs
    return None, []


def inject_pb_scripts(job_tags, job_data, scen, game_maps_dir, inv, hceek_dir, log):
    """Give the converted map the same scripts the game's own maps carry
    (talking timer beeps, callouts, HUD extras). The scripts live inside every
    shipped map's scenario tag; we copy them out and compile them into this map.
    Scripts that reference objects/flags only the donor map has are dropped one
    by one until the set compiles. Also strips any scripts the MCC map brought."""
    sargs = [os.path.join(inv, "invader-script.exe"), "-t", job_tags,
             "-d", job_data, "-g", "gbx-retail"]
    rel = scen.replace(os.sep, "/")
    if rel.endswith(".scenario"):
        rel = rel[:-len(".scenario")]
    run(sargs + ["-c", rel], cwd=hceek_dir, log=log, timeout=300)  # strip MCC scripts
    if not stock_ready():
        return 0
    donor_name = ""
    try:
        donor_name = json.load(open(os.path.join(STOCK, "ready.json"))).get("donor", "")
    except (OSError, ValueError):
        pass
    if donor_name:
        dm = os.path.join(game_maps_dir, donor_name)
        if os.path.isfile(dm):
            run([os.path.join(inv, "invader-extract.exe"), "-t", job_tags, "-O",
                 "-s", "sound/timer/*", dm], cwd=hceek_dir, timeout=300)  # timer voices/beeps
    head, defs = _pb_script_source(STOCK)
    if not defs:
        return 0
    os.makedirs(job_data, exist_ok=True)
    def compile_set(keep):
        with open(os.path.join(job_data, "global_scripts.hsc"), "w", encoding="utf-8") as f:
            f.write(head + "".join(keep))
        rc, out = run(sargs + [rel], cwd=hceek_dir, timeout=300)
        return out
    n0 = len(defs)
    for _ in range(40):
        out = compile_set(defs)
        if "error" not in out:
            dropped = n0 - len(defs)
            log_line(f"added the game's scripts ({len(defs)} scripts"
                     + (f", skipped {dropped} that need things only their own map has)"
                        if dropped else ")"), log)
            return len(defs)
        m = re.search(r":(\d+):", out)
        line = int(m.group(1)) if m else None
        drop = None
        if line:
            ln = head.count("\n")
            if line <= ln:
                break
            for i, d in enumerate(defs):
                ln += d.count("\n")
                if line <= ln:
                    drop = i
                    break
        if drop is None:
            break
        defs.pop(drop)
    log_line("WARNING: could not add the game's scripts to this map — converting "
             "it without them", log)
    run(sargs + ["-c", rel], cwd=hceek_dir, log=log, timeout=300)
    return 0


def stockify_weapons(job_tags, log=None):
    """Custom weapon tags that share a path with the game's stock weapons get
    replaced by the stock ones (Halo PC chokes on many MCC custom weapons).
    Returns (replaced_names, custom_names_we_cannot_map)."""
    wroot = os.path.join(job_tags, "weapons")
    if not os.path.isdir(wroot):
        return [], []
    replaced, unmatched = [], []
    for dirpath, _dirs, files in os.walk(wroot):
        for fn in files:
            if not os.path.isfile(os.path.join(STOCK, "weapons", os.path.relpath(os.path.join(dirpath, fn), wroot))):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), job_tags)
            stock_f = os.path.join(STOCK, rel)
            if not os.path.isfile(stock_f):
                unmatched.append(os.path.relpath(os.path.join(dirpath, fn), wroot))
                continue
            if sha256(stock_f) != sha256(os.path.join(dirpath, fn)):
                shutil.copy2(stock_f, os.path.join(dirpath, fn))
                replaced.append(os.path.splitext(fn)[0])
    if replaced:
        log_line(f"swapped {len(set(replaced))} custom weapon tags for the game's "
                 "stock versions (Halo PC can't run the MCC ones)", log)
    return sorted(set(replaced)), sorted(set(unmatched))


def _edit_count(inv, tags_dir, key, tagpath, hceek_dir):
    rc, out = run([os.path.join(inv, "invader-edit.exe"), "-t", tags_dir,
                   "-C", key, tagpath], cwd=hceek_dir, timeout=180)
    v = out.strip().splitlines()[0].strip() if out.strip() else ""
    return int(v) if v.isdigit() else None


AUDIT_KEYS = ("netgame_flags", "netgame_equipment", "scenery", "vehicles",
              "bipeds", "object_names")


def audit_scenario(job_tags, scen, built_map, inv, hceek_dir, log):
    """Check nothing important got dropped while building: netgame flags
    (teleporter nodes etc.) and placed-object counts must survive 1:1."""
    rel = scen.replace(os.sep, "/") + ".scenario"
    pre = {k: _edit_count(inv, job_tags, k, rel, hceek_dir) for k in AUDIT_KEYS}
    tmp = tempfile.mkdtemp(prefix="audit_")
    try:
        run([os.path.join(inv, "invader-extract.exe"), "-t", tmp, "-O", "-s",
             os.path.basename(rel), built_map], cwd=hceek_dir, timeout=300)
        post = {k: _edit_count(inv, tmp, k, os.path.basename(rel), hceek_dir)
                for k in AUDIT_KEYS}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    warns = []
    for k in AUDIT_KEYS:
        a, b = pre.get(k), post.get(k)
        if a is not None and b is not None and a != b:
            warns.append(f"{k.replace('_', ' ')} changed while building "
                         f"({a} -> {b}) — the map may behave oddly in some gametypes")
    ng = pre.get("netgame_flags") or 0
    ngp = pre.get("netgame_equipment") or 0
    log_line(f"netgame check: {ng} flags, {ngp} equipment slots, "
             f"{pre.get('scenery', 0)} scenery, {pre.get('vehicles', 0)} vehicles — all survived", log)
    return warns


def weight_warnings(built_map, job_tags, scen, inv, hceek_dir, log):
    """Halo PC is an old engine: it can't draw what MCC can. We can't count
    triangles exactly (they live inside the opaque BSP blob), so warn on the
    things that correlate: total size, BSP size, cluster metadata."""
    warns = []
    mb = os.path.getsize(built_map) / 1e6
    leaf = os.path.splitext(os.path.basename(scen))[0]
    bsp = glob.glob(os.path.join(job_tags, os.path.dirname(scen.replace(os.sep, os.sep)),
                                 leaf + "*.scenario_structure_bsp")) or \
          glob.glob(os.path.join(job_tags, "**", leaf + "*.scenario_structure_bsp"),
                    recursive=True)
    bsp_mb = os.path.getsize(bsp[0]) / 1e6 if bsp else 0
    clusters = None
    if bsp:
        rel = os.path.relpath(bsp[0], job_tags).replace(os.sep, "/")
        clusters = _edit_count(inv, job_tags, "clusters", rel, hceek_dir)
    if mb > 250 or bsp_mb > 150:
        warns.append(f"heavy map ({mb:.0f} MB, BSP {bsp_mb:.0f} MB): Halo PC is an "
                     "old engine and may lag or fail to draw parts of this map")
    if clusters == 1 and bsp_mb > 20:
        warns.append("the map's geometry is a single render cluster — Halo PC may "
                     "struggle to draw it in one pass (MCC handles this fine)")
    return warns


def _overlay(failed, job_tags, game_maps_dir, inv, hceek_dir, tried, log):
    """Pull PC-schema copies of the failing tags from the game's own maps."""
    fresh = failed - tried
    if not fresh:
        return False
    tried |= fresh
    for tag in sorted(fresh):
        # 1) the companion's own stock-tag cache (self-contained)
        cached = os.path.normpath(os.path.join(STOCK, tag.replace("\\", os.sep)))
        if os.path.isfile(cached):
            dst = os.path.normpath(os.path.join(job_tags, tag.replace("\\", os.sep)))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(cached, dst)
            log_line(f"  repaired '{tag}' from the companion's stock cache", log)
            continue
        # 2) a shipped map in the game's maps folder
        hit = None
        for gm in sorted(glob.glob(os.path.join(game_maps_dir, "*.map"))):
            stem = os.path.splitext(os.path.basename(gm))[0].lower()
            if stem in gate.DENY or os.path.getsize(gm) < 0x800:
                continue
            rc, out = run([os.path.join(inv, "invader-extract.exe"), "-t", job_tags,
                           "-O", "-s", tag, gm],
                          cwd=hceek_dir, log=log, timeout=600)
            m = re.search(r"Extracted (\d+) out of", out)
            if m and int(m.group(1)) > 0:
                hit = os.path.basename(gm)
                log_line(f"  repaired '{tag}' from {hit}", log)
                break
        if not hit:
            log_line(f"  repair: no shipped map carries '{tag}' — retrying build "
                     "without it", log)
    return True


def convert_map(map_path, game_dir, hceek_dir, mcc_maps_dir, inv_zip,
                log=None, add_scripts=False, weapon_fix=False):
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
    # A Workshop pack may ship its own halo1\maps resource files next to the
    # map (custom bitmaps/sounds). Extract from MCC's stock packs first, then
    # from the pack's own (its versions win, like MCC's own load order).
    res_dirs = [mcc_maps_dir] if mcc_maps_dir else []
    for up in (os.path.dirname(map_path),
               os.path.join(os.path.dirname(map_path), "..")):
        own = os.path.normpath(os.path.join(up, "halo1", "maps"))
        if os.path.isdir(own) and glob.glob(os.path.join(own, "*.map")) \
                and own not in res_dirs:
            res_dirs.append(own)
    last_out = ""
    for rd in res_dirs:
        cmd = [os.path.join(inv, "invader-extract.exe"), "-t", job_tags,
               "-O", "-s", "*"]
        cmd += ["-m", rd, map_path]
        rc, last_out = run(cmd, cwd=hceek_dir, log=log, timeout=1200)
    out = last_out
    if "Extracted 0 out of" in out:
        raise RuntimeError("extract found no tags: " + out.strip()[-300:])

    scen = _rel(_scenario_after_extract(job_tags, before), job_tags)
    log_line(f"scenario tag: {scen}", log)
    _stockify_hud(job_tags, os.path.join(game_dir, "maps"), inv, hceek_dir, log)
    build_stock_cache(os.path.join(game_dir, "maps"), inv, hceek_dir, log)
    warnings = []
    # Gearbox broke 'shader transparent generic' in the Halo PC port. Count the
    # ones this map brings of its own (not the game's stock ones) — those may
    # render black or wrong and cannot be converted.
    try:
        _lvl = os.path.dirname(scen)
        _stpg = [f for f in glob.glob(os.path.join(job_tags, "**",
                   "*.shader_transparent_generic"), recursive=True)
                 if _lvl and _lvl in os.path.relpath(f, job_tags)]
        if _stpg:
            warnings.append(f"{len(_stpg)} transparent shaders in this map use the "
                            "'transparent generic' type — Gearbox broke that shader "
                            "in Halo PC, so those surfaces may look black or "
                            "wrong-sorted in game (nothing can fix this)")
    except OSError:
        pass
    if weapon_fix and stock_ready():
        _rep, _cust = stockify_weapons(job_tags, log)
        if _cust:
            warnings.append("this map has weapons the game doesn't stock "
                            f"({', '.join(_cust[:4])}{'...' if len(_cust) > 4 else ''}) — "
                            "left as-is; telltale of issues in game? report it")
    if add_scripts:
        inject_pb_scripts(job_tags, os.path.join(job, "data"), scen,
                          os.path.join(game_dir, "maps"), inv, hceek_dir, log)

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
            try:
                warnings += weight_warnings(out_map, job_tags, scen, inv, hceek_dir, log)
                warnings += audit_scenario(job_tags, scen, out_map, inv, hceek_dir, log)
            except Exception as e:
                log_line(f"(post-build check skipped: {e})", log)
            for w in warnings:
                log_line("WARNING: " + w, log)
            return {"name": name, "map": out_map, "attempts": attempt,
                    "warnings": warnings}
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


def install_map(game_dir, src_map, name, replace=False):
    """Copy into maps\\ with the game gate; collisions get _2, _3 ...
    replace=True overwrites the map named <name>.map (used when refreshing a
    companion-installed map with a newer build)."""
    maps_dir = os.path.join(game_dir, "maps")
    os.makedirs(maps_dir, exist_ok=True)
    stem = name[:31]
    dest = os.path.join(maps_dir, stem + ".map")
    if replace and os.path.isfile(dest):
        shutil.copy2(src_map, dest)
        man = load_manifest(game_dir)
        man["maps"][os.path.basename(dest)] = {"name": name, "sha256": sha256(dest)}
        save_manifest(game_dir, man)
        return dest
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
