"""H1 Map Companion — turn your MCC maps into Performance Build maps.

GUI:  python companion.py
CLI:  python companion.py --convert <map.map> [--game <dir>]
"""
import argparse
import glob
import json
import os
import queue
import sys
import threading

import gate
import pipeline
import steamscan

CONFIG_PATH = os.path.join(pipeline.APPDIR, "config.json")


def load_config():
    try:
        return json.load(open(CONFIG_PATH))
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    os.makedirs(pipeline.APPDIR, exist_ok=True)
    with open(CONFIG_PATH, "w") as fh:
        json.dump(cfg, fh, indent=2)


def scan_sources(cfg):
    """Candidate .map files: MCC stock maps, Workshop packages, user folders."""
    found, seen = [], set()

    def add(folder, label):
        if not folder or not os.path.isdir(folder):
            return
        for f in sorted(glob.glob(os.path.join(folder, "*.map"))):
            p = os.path.normcase(os.path.abspath(f))
            if p not in seen:
                seen.add(p)
                found.append((f, label))

    if cfg.get("use_mcc", True):
        mcc = steamscan.mcc_map_dir()
        n = len(glob.glob(os.path.join(mcc or "", "*.map"))) if mcc else 0
        add(mcc, f"MCC stock maps ({n})")
    if cfg.get("use_workshop", True):
        for d in steamscan.workshop_dirs():
            add(d, "MCC Workshop: " + os.path.basename(os.path.dirname(d)))
    for d in cfg.get("source_dirs", []):
        add(d, "My folder: " + d)
    return found


def fmt_version(h):
    if h is None or h["magic"] != b"daeh":
        return "not a Halo map"
    return {13: "MCC map — will be converted",
            7: "PC map — copies straight in",
            609: "CE map — this game can't use CE files"}.get(
        h["version"], f"v{h['version']} — unsupported")


# ------------------------------------------------------------------ CLI ----

def cli_convert(map_path, cfg):
    game = cfg.get("game_dir") or ""
    hceek = steamscan.hceek_dir()
    if not hceek:
        sys.exit("Mod Tools (Halo: CE Mod Tools - MCC) not found in Steam.")
    z = pipeline.find_invader_zip(game, cfg)
    if not z:
        sys.exit("invader-*.zip not found (game tools\\ folder or config invader_zip).")
    print(":: converting", map_path)
    res = pipeline.convert_map(map_path, game, hceek, steamscan.mcc_map_dir(), z,
                               log=lambda m: print("  " + m))
    if res.get("skipped"):
        print("::", res["note"])
        return
    if game:
        print(":: installed ->", pipeline.install_map(game, res["map"], res["name"]))
    else:
        print(":: built ->", res["map"], "(no game folder set, not installed)")


# ------------------------------------------------------------------ GUI ----

def gui():
    import tkinter as tk
    from tkinter import filedialog, ttk, messagebox

    cfg = load_config()
    q = queue.Queue()

    root = tk.Tk()
    root.title("H1 Map Companion")
    root.geometry("900x620")

    frm = ttk.Frame(root, padding=10)
    frm.pack(fill="both", expand=True)

    game_var = tk.StringVar(value=cfg.get("game_dir", ""))
    use_mcc = tk.BooleanVar(value=cfg.get("use_mcc", True))
    use_ws = tk.BooleanVar(value=cfg.get("use_workshop", True))

    # ---- step 1: the game ------------------------------------------------
    s1 = ttk.LabelFrame(frm, text=" 1  Your game  ", padding=8)
    s1.pack(fill="x", pady=(0, 8))
    row1 = ttk.Frame(s1)
    row1.pack(fill="x")
    ttk.Entry(row1, textvariable=game_var).pack(side="left", fill="x", expand=True)
    ttk.Button(row1, text="Change…", width=10, command=lambda: pick_game()).pack(side="left", padx=6)
    game_hint = ttk.Label(s1, foreground="#666", text="")
    game_hint.pack(anchor="w")
    ttk.Label(s1, foreground="#666",
              text="The H1 Performance Build folder — the one with the game's .exe "
                   "files and the maps\\ folder inside it.").pack(anchor="w")

    # ---- step 2: sources --------------------------------------------------
    s2 = ttk.LabelFrame(frm, text=" 2  Where maps come from  ", padding=8)
    s2.pack(fill="x", pady=(0, 8))
    ttk.Checkbutton(s2, text="My MCC installation's maps",
                    variable=use_mcc, command=lambda: refresh()).pack(anchor="w")
    ttk.Checkbutton(s2, text="MCC Workshop maps I subscribed to",
                    variable=use_ws, command=lambda: refresh()).pack(anchor="w")
    ttk.Button(s2, text="Add another folder with .map files…",
               command=lambda: add_folder(), width=40).pack(anchor="w", pady=(4, 0))

    # ---- step 3: the list -------------------------------------------------
    s3 = ttk.LabelFrame(frm, text=" 3  Pick maps  ", padding=8)
    s3.pack(fill="both", expand=True, pady=(0, 8))
    tree = ttk.Treeview(s3, columns=("fmt", "status", "size", "src"),
                        show="tree headings", selectmode="extended")
    tree.heading("#0", text="Map")
    tree.column("#0", width=230)
    for c, head, w in (("fmt", "What it is", 230), ("status", "In your game?", 170),
                       ("size", "Size", 70), ("src", "Where it came from", 220)):
        tree.heading(c, text=head)
        tree.column(c, width=w, anchor="w")
    vsb = ttk.Scrollbar(s3, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")
    tree.bind("<Double-1>", lambda e: do_install_ask())
    tree.tag_configure("installed", foreground="#0a7d32")
    tree.tag_configure("bad", foreground="#b00000")

    # ---- actions ----------------------------------------------------------
    acts = ttk.Frame(frm)
    acts.pack(fill="x", pady=(0, 6))
    b_go = ttk.Button(acts, text="Install selected →", command=lambda: do_install_ask())
    b_go.pack(side="left")
    b_rm = ttk.Button(acts, text="Remove selected from my game", command=lambda: do_remove())
    b_rm.pack(side="left", padx=6)
    ttk.Button(acts, text="Refresh", command=lambda: refresh()).pack(side="left")
    ttk.Label(frm, foreground="#666",
              text="Double-click a row to install it too. MCC maps are converted first "
                   "(about 1–3 minutes each; the first one ever also sets things up, ~4 min). "
                   "CE (Custom Edition) maps can't be used by this game — only MCC maps and PC maps."
                   ).pack(anchor="w")

    bar = ttk.Progressbar(frm, mode="indeterminate")
    bar.pack(fill="x", pady=(6, 2))
    logbox = tk.Text(frm, height=7, state="disabled", wrap="word")
    logbox.pack(fill="both", expand=False)

    # ---- helpers ----------------------------------------------------------
    def log(msg):
        q.put(("log", str(msg)))

    def pick_game():
        d = filedialog.askdirectory()
        if d:
            game_var.set(d)
            adopt_game()

    def adopt_game():
        d = game_var.get()
        cfg["game_dir"] = d
        save_config(cfg)
        refresh()

    def add_folder():
        d = filedialog.askdirectory()
        if d:
            cfg.setdefault("source_dirs", [])
            if d not in cfg["source_dirs"]:
                cfg["source_dirs"].append(d)
                save_config(cfg)
            refresh()

    def refresh():
        cfg["use_mcc"] = bool(use_mcc.get())
        cfg["use_workshop"] = bool(use_ws.get())
        save_config(cfg)
        tree.delete(*tree.get_children())
        game = game_var.get()
        in_game = set()
        if game and os.path.isdir(os.path.join(game, "maps")):
            in_game = {os.path.splitext(f)[0].lower()
                       for f in os.listdir(os.path.join(game, "maps"))
                       if f.lower().endswith(".map")}
            game_hint.configure(text=f"Found {len(in_game)} maps in this folder. ✔")
        else:
            game_hint.configure(text="⚠ this folder has no maps\\ inside — pick the game folder first") if game \
                else game_hint.configure(text="not set yet")
        for f, label in scan_sources(cfg):
            h = gate.read_header(f)
            name = (h["name"] if h and h.get("name")
                    else os.path.splitext(os.path.basename(f))[0])
            fmt = fmt_version(h)
            tags = ("src::" + f,)
            if h and h["magic"] == b"daeh" and h["version"] != 7 and h["version"] != 13:
                tags += ("bad",)
            status = "yes ✔" if name.lower() in in_game else "no"
            tree.insert("", "end", text=name,
                        values=(fmt, status, f"{os.path.getsize(f)/1e6:.0f} MB", label),
                        tags=tags)
        if game:
            man = pipeline.load_manifest(game)
            for fname, ent in sorted(man.get("maps", {}).items()):
                base = os.path.splitext(fname)[0]
                p = os.path.join(game, "maps", fname)
                size = f"{os.path.getsize(p)/1e6:.0f} MB" if os.path.isfile(p) else "?"
                tree.insert("", "end", text=ent.get("name", base),
                            values=("installed map", "yes ✔ (by companion)",
                                    size, "in your game folder"),
                            tags=("installed", "installed::" + fname))

    def selected_sources():
        out = []
        for node in tree.selection():
            for t in tree.item(node, "tags"):
                if t.startswith("src::"):
                    out.append(t[5:])
        return out

    def do_install_ask():
        paths = selected_sources()
        if not paths:
            messagebox.showinfo("H1 Map Companion",
                                "Click one or more MCC maps in the list first.")
            return
        if not game_var.get():
            messagebox.showerror("H1 Map Companion",
                                 "Pick your game folder in step 1 first.")
            return
        bad = [p for p in paths
               if (gate.read_header(p) or {}).get("version") not in (7, 13)]
        if bad:
            messagebox.showinfo(
                "H1 Map Companion",
                "Some selected maps are CE (Custom Edition) or unknown files — this "
                "game can only use maps converted from MCC or PC-format maps.")
            return
        if not messagebox.askyesno(
                "H1 Map Companion",
                f"Install {len(paths)} map(s) into your game?\n\n"
                "MCC maps get converted first — give it a couple of minutes each."):
            return
        set_busy(True)
        threading.Thread(target=_install_worker, args=(paths,), daemon=True).start()

    def _install_worker(paths):
        game = game_var.get()
        hceek = steamscan.hceek_dir()
        z = pipeline.find_invader_zip(game, cfg)
        needs_conv = any((gate.read_header(p) or {}).get("version") == 13 for p in paths)
        if needs_conv and not hceek:
            log("You need the free Steam tool 'Halo: CE Mod Tools - MCC' installed "
                "(and its 'HCEEK (Extract).bat' run once) before MCC maps can convert.")
            q.put(("done",))
            return
        if needs_conv and not z:
            log("Could not find the game's tools\\invader-*.zip — put it beside the "
                "game or set invader_zip in " + CONFIG_PATH)
            q.put(("done",))
            return
        for p in paths:
            try:
                h = gate.read_header(p)
                if h["version"] == 7:
                    ok, why = gate.gate(p)
                    if not ok:
                        log(f"{os.path.basename(p)}: {why}")
                        continue
                    name = h["name"] or os.path.splitext(os.path.basename(p))[0]
                    dest = pipeline.install_map(game, p, name)
                    log(f"copied in -> {dest}")
                    continue
                log(f"converting {os.path.basename(p)} …")
                res = pipeline.convert_map(p, game, hceek,
                                           steamscan.mcc_map_dir(), z, log=log)
                dest = pipeline.install_map(game, res["map"], res["name"])
                log(f"done! '{res['name']}' is now in your game — "
                    "you'll see it in the MAPS menu next time you start the game")
            except Exception as e:
                log("FAILED: " + str(e)[:700])
        q.put(("done",))

    def do_remove():
        game = game_var.get()
        if not game:
            return
        man = pipeline.load_manifest(game)
        removed = False
        for node in tree.selection():
            for t in tree.item(node, "tags"):
                if t.startswith("installed::"):
                    fname = t[11:]
                    if fname in man.get("maps", {}):
                        try:
                            os.remove(os.path.join(game, "maps", fname))
                        except OSError as e:
                            log("couldn't remove: " + str(e))
                            continue
                        del man["maps"][fname]
                        removed = True
                        log("removed " + fname + " from your game")
                    else:
                        messagebox.showinfo(
                            "H1 Map Companion",
                            "Only maps installed by this app can be removed here.")
        if removed:
            pipeline.save_manifest(game, man)
            refresh()

    def set_busy(on):
        for b in (b_go, b_rm):
            b.state(["disabled"] if on else ["!disabled"])
        if on:
            bar.start(60)
        else:
            bar.stop()

    def pump():
        try:
            while True:
                item = q.get_nowait()
                if item[0] == "log":
                    logbox.configure(state="normal")
                    logbox.insert("end", item[1] + "\n")
                    logbox.see("end")
                    logbox.configure(state="disabled")
                elif item[0] == "done":
                    set_busy(False)
                    refresh()
        except queue.Empty:
            pass
        root.after(150, pump)

    set_busy(False)
    refresh()
    pump()
    root.mainloop()


def main():
    ap = argparse.ArgumentParser(description="H1 Map Companion")
    ap.add_argument("--convert", help="convert one map headlessly")
    ap.add_argument("--game", help="game folder (the one containing maps\\)")
    args = ap.parse_args()
    cfg = load_config()
    if args.game:
        cfg["game_dir"] = args.game
    if args.convert:
        cli_convert(args.convert, cfg)
        return
    gui()


if __name__ == "__main__":
    main()
