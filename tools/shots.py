"""Screenshot the real GUI in a set of states, so a layout regression is visible.

Every automated probe I wrote against this window measured geometry, and geometry
answered the wrong question: a run where all five action buttons sat below the
fold came back clean. This drives the actual widget tree and captures pixels.

    uv run python tools/shots.py [name ...]      -> /tmp/sqlalign-shots/*.png
"""
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalign.gui import PRESETS, SAMPLES, build

OUT = Path("/tmp/sqlalign-shots")

DECLINED = "select * from t pivot (sum(x) for y in (1, 2)) p;"
BROKEN = "select from where ((("


def settle(root, rounds=25, pause=0.02):
    """Pump the event loop until the window server has caught up.

    Without this the harness captured a stale frame: `root.title()` returned the
    new title while the pixels still showed the old one. A screenshot tool that
    races the compositor reports regressions that are not there and -- worse --
    hides ones that are.
    """
    for _ in range(rounds):
        root.update_idletasks()
        root.update()
        time.sleep(pause)


def shoot(root, name):
    """Capture the window's own rectangle -- `screencapture -R` takes points."""
    settle(root)
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    # The titlebar sits above winfo_rooty; include it, it is part of the window.
    path = OUT / f"{name}.png"
    subprocess.run(
        ["screencapture", "-x", f"-R{x},{y - 28},{w},{h + 28}", str(path)], check=True)
    print(f"{name:24} {w}x{h}")


def scenarios(root, gui):
    src = gui["source"]

    def sql(text):
        src.delete("1.0", "end")
        src.insert("1.0", text)
        gui["render"]()
        root.update()

    yield "default", lambda: None
    for preset in sorted(PRESETS):
        yield f"preset-{preset}", lambda p=preset: gui["preset"].set(p)
    yield "reset", lambda: gui["preset"].set("house")

    yield "declined", lambda: sql(DECLINED)
    yield "parse-error", lambda: sql(BROKEN)
    yield "empty", lambda: sql("")
    yield "restored", lambda: sql(SAMPLES["postgres"])

    def opened():
        path = Path("/tmp/sqlalign-shots/monthly_revenue.sql")
        path.write_text(SAMPLES["postgres"])
        gui["state"]["path"] = str(path)
        gui["retitle"]()

    yield "opened-file", opened
    yield "no-file", lambda: (gui["state"].__setitem__("path", None), gui["retitle"]())

    yield "panel-bottom", lambda: gui["tray"].yview_moveto(1.0)
    yield "panel-top", lambda: gui["tray"].yview_moveto(0.0)

    for w, h in [(1180, 760), (980, 620), (860, 520), (760, 460)]:
        yield f"size-{w}x{h}", lambda w=w, h=h: root.geometry(f"{w}x{h}")


def main(argv):
    OUT.mkdir(exist_ok=True)
    for stale in OUT.glob("*.png"):
        stale.unlink()
    root = tk.Tk()
    gui = build(root)
    root.update()
    wanted = set(argv)
    for name, act in scenarios(root, gui):
        act()
        root.update_idletasks()
        root.update()
        if not wanted or name in wanted:
            shoot(root, name)
    root.destroy()
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1:])
