"""H1 Map Companion — pick MCC maps, Convert, they land in the game's maps\\.

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
import traceback

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

    def add(folder):
        if not folder or not os.path.isdir(folder):
            return
        for f in sorted(glob.glob(os.path.join(folder, "*.map"))):
            p = os.path.normcase(os.path.abspath(f))
            if p not in seen:
                seen.add(p)
                found.append(f)

    mcc = steamscan.mcc_map_dir()
    if mcc and cfg.get("use_mcc", True):
        add(mcc)
    if cfg.get("use_workshop", True):
        for d in steamscan.workshop_dirs():
            add(d)
    for d in cfg.get("source_dirs", []):
        add(d)
    return found


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
        dest = pipeline.install_map(game, res["map"], res["name"])
        print(":: installed ->", dest)
    else:
        print(":: built ->", res["map"], "(no game folder set, not installed)")


# ------------------------------------------------------------------ GUI ----

def gui():
    import tkinter as tk
    from tkinter import filedialog, ttk, messagebox

    cfg = load_config()
    q = queue.Queue()
    scan_cache = []  # refreshed list of source map paths

    root = tk.Tk()
    root.title("H1 Map Companion")
    root.geometry("880x580")

    frm = ttk.Frame(root, padding=8)
    frm.pack(fill="both", expand=True)

    game_var = tk.StringVar(value=cfg.get("game_dir", ""))
    src_var = tk.StringVar(value="")

    ttk.Label(frm, text="Game folder:").grid(row=0, column=0, sticky="w")
    ttk.Entry(frm, textvariable=game_var).grid(row=0, column=1, sticky="we", padx=4)
    ttk.Button(frm, text="Browse", width=8, command=lambda: pick("game")).grid(row=0, column=2)
    ttk.Label(frm, text="Extra map folder:").grid(row=1, column=0, sticky="w")
    ttk.Entry(frm, textvariable=src_var).grid(row=1, column=1, sticky="we", padx=4)
    ttk.Button(frm, text="Add", width=8, command=add_source).grid(row=1, column=2)

    tree = ttk.Treeview(frm, columns=("status", "size", "source"),
                        show="tree headings", selectmode="extended")
    tree.heading("#0", text="Map")
    tree.column("#0", width=250)
    for c, head, w in (("status", "Status", 220), ("size", "Size", 80),
                       ("source", "Source", 220)):
        tree.heading(c, text=head)
        tree.column(c, width=w, anchor="w")
    tree.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=6)
    frm.rowconfigure(2, weight=1)
    frm.columnconfigure(1, weight=1)

    btns = ttk.Frame(frm)
    btns.grid(row=3, column=0, columnspan=3, sticky="we")
    b_refresh = ttk.Button(btns, text="Refresh", command=lambda: refresh())
    b_refresh.pack(side="left")
    b_conv = ttk.Button(btns, text="Convert + install selected", command=convert_sel)
    b_conv.pack(side="left", padx=6)
    b_rm = ttk.Button(btns, text="Remove companion maps", command=remove_sel)
    b_rm.pack(side="left")

    bar = ttk.Progressbar(frm, mode="indeterminate")
    bar.grid(row=4, column=0, columnspan=3, sticky="we", pady=(6, 2))
    logbox = tk.Text(frm, height=8, state="disabled", wrap="word")
    logbox.grid(row=5, column=0, columnspan=3, sticky="nsew")
    frm.rowconfigure(5, weight=1)

    def log(msg):
        q.put(("log", str(msg)))

    def pick(what):
        d = filedialog.askdirectory()
        if not d:
            return
        if what == "game":
            game_var.set(d)
            cfg["game_dir"] = d
            save_config(cfg)
            refresh()

    def add_source():
        d = src_var.get().strip()
        if d and os.path.isdir(d):
            cfg.setdefault("source_dirs", [])
            if d not in cfg["source_dirs"]:
                cfg["source_dirs"].append(d)
                save_config(cfg)
            refresh()

    def refresh():
        nonlocal scan_cache
        scan_cache = scan_sources(cfg)
        tree.delete(*tree.get_children())
        for f in scan_cache:
            h = gate.read_header(f)
            name = (h["name"] if h and h["name"]
                    else os.path.splitext(os.path.basename(f))[0])
            if h is None:
                status = "not a map"
            elif h["magic"] != b"daeh":
                status = "not a Halo cache"
            elif h["version"] == 7:
                ok, _ = gate.gate(f)
                status = "v7 — install directly" if ok else gate.gate(f)[1]
            elif h["version"] == 13:
                status = "MCC v13 — Convert to install"
            else:
                status = f"v{h['version']} — unsupported"
            src = os.path.basename(os.path.dirname(f))
            tree.insert("", "end", text=name, tags=(f"src::{f}",),
                        values=(status, f"{os.path.getsize(f)/1e6:.0f} MB", src))
        game = game_var.get()
        if game:
            for fname in sorted(pipeline.load_manifest(game).get("maps", {})):
                tree.insert("", "end", text=os.path.splitext(fname)[0],
                            tags=("installed::" + fname,),
                            values=("INSTALLED in game", "", "companion"))

    def convert_sel():
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("H1 Map Companion", "Select map(s) first.")
            return
        if not game_var.get():
            messagebox.showerror("H1 Map Companion", "Set the game folder first.")
            return
        cfg["game_dir"] = game_var.get()
        save_config(cfg)
        paths = []
        for node in sel:
            tags = tree.item(node, "tags")
            if tags and tags[0].startswith("src::"):
                paths.append(tags[0][5:])
        if not paths:
            messagebox.showinfo("H1 Map Companion",
                                "Select MCC maps from the list (not installed rows).")
            return
        set_busy(True)
        threading.Thread(target=_convert_worker, args=(paths,), daemon=True).start()

    def _convert_worker(paths):
        hceek = steamscan.hceek_dir()
        if not hceek:
            log("Mod Tools (Halo: CE Mod Tools - MCC) not found in Steam.")
            q.put(("done",))
            return
        z = pipeline.find_invader_zip(game_var.get(), cfg)
        if not z:
            log("invader-*.zip not found — put it in <game>\\tools\\ or set "
                "invader_zip in " + CONFIG_PATH)
            q.put(("done",))
            return
        for p in paths:
            try:
                log(f"--- {os.path.basename(p)}")
                res = pipeline.convert_map(p, game_var.get(), hceek,
                                           steamscan.mcc_map_dir(), z, log=log)
                if res.get("skipped"):
                    log(res["note"])
                    continue
                dest = pipeline.install_map(game_var.get(), res["map"], res["name"])
                log(f"installed -> {dest} — it shows up in-game on next launch")
            except Exception as e:
                log("FAILED: " + str(e)[:700])
        q.put(("done",))

    def remove_sel():
        game = game_var.get()
        if not game:
            return
        man = pipeline.load_manifest(game)
        removed = False
        for node in tree.selection():
            tags = tree.item(node, "tags")
            if tags and tags[0].startswith("installed::"):
                fname = tags[0][11:]
                if fname in man.get("maps", {}):
                    p = os.path.join(game, "maps", fname)
                    try:
                        os.remove(p)
                    except OSError as e:
                        log("remove failed: " + str(e))
                        continue
                    del man["maps"][fname]
                    removed = True
                    log("removed " + fname)
        if removed:
            pipeline.save_manifest(game, man)
            refresh()

    def set_busy(on):
        for b in (b_refresh, b_conv, b_rm):
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
    try:
        gui()
    except Exception:
        if "No module named" in traceback.format_exc() and "tkinter" in traceback.format_exc():
            traceback.print_exc()
            print("no tkinter in this Python — install the 'python-tkinter' package "
                  "or use: python companion.py --convert <map>")
        else:
            raise


if __name__ == "__main__":
    main()
