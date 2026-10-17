"""Find Steam installs of MCC (976730) and Halo: CE Mod Tools - MCC (1532190).

Parses <steam>/steamapps/libraryfolders.vdf for library paths, then each
library's appmanifest_<appid>.acf for installdir. No Steam API, no running
Steam process needed.
"""
import glob
import os
import re


def steam_roots():
    roots = []
    pf = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    default = os.path.join(pf, "Steam")
    if os.path.isdir(default):
        roots.append(default)
    vdfs = [os.path.join(default, "steamapps", "libraryfolders.vdf")]
    for extra in glob.glob(os.path.join(os.path.expanduser("~"), "AppData", "Local",
                                        "Steam", "steamapps", "libraryfolders.vdf")):
        vdfs.append(extra)
    for vdf in vdfs:
        if not os.path.isfile(vdf):
            continue
        try:
            text = open(vdf, encoding="utf-8", errors="replace").read()
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                p = m.group(1).replace("\\\\", "\\").replace("\\", os.sep)
                if os.path.isdir(p) and p not in roots:
                    roots.append(p)
        except OSError:
            pass
    return roots


def _acf(path):
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
        appid = re.search(r'"appid"\s+"(\d+)"', text)
        name = re.search(r'"name"\s+"([^"]+)"', text)
        installdir = re.search(r'"installdir"\s+"([^"]+)"', text)
        state = re.search(r'"StateFlags"\s+"(\d+)"', text)
        if not (appid and installdir):
            return None
        # StateFlags 4 = fully installed
        if state and state.group(1) != "4":
            return None
        return {"appid": appid.group(1),
                "name": name.group(1) if name else "",
                "installdir": installdir.group(1)}
    except OSError:
        return None


def find_app(appid):
    """Return install path for a Steam appid, or None."""
    for root in steam_roots():
        for acf in glob.glob(os.path.join(root, "steamapps", "appmanifest_*.acf")):
            info = _acf(acf)
            if info and info["appid"] == appid:
                p = os.path.join(root, "steamapps", "common", info["installdir"])
                if os.path.isdir(p):
                    return p
    return None


def mcc_dir():
    return find_app("976730")


def hceek_dir():
    d = find_app("1532190")
    if d and os.path.isdir(os.path.join(d, "HCEEK", "tags")):
        return d  # Mod Tools not extracted: tags missing
    return d


def mcc_map_dir():
    """MCC halo1/maps folder (stock v13 maps + resource packs for -m)."""
    d = mcc_dir()
    if d:
        p = os.path.join(d, "halo1", "maps")
        if os.path.isdir(p):
            return p
    return None


def workshop_dirs():
    """Workshop package 'maps' folders for MCC custom maps."""
    out = []
    for root in steam_roots():
        out += glob.glob(os.path.join(root, "steamapps", "workshop", "content",
                                      "976730", "*", "maps"))
        out += glob.glob(os.path.join(root, "steamapps", "workshop", "content",
                                      "976730", "*", "halo1", "maps"))
    return out
