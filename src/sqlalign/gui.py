"""`sqlalign --gui` — a settings panel next to a live preview.

Nineteen knobs is more than anyone will read about. The fastest way to learn what
one does is to change it and watch the SQL move, which is what this is for: every
control re-runs the REAL engine on the text in the pane, so what you see is what
the CLI would write.

Tkinter rather than the local web page the original spec called for. The web
version means a server, a port, a browser tab and a second language in the
repository, to draw a settings panel next to a text box. Tkinter is in the
standard library, so this costs zero new dependencies and ships with the same
`pip install`, which matters more than usual for a tool whose install story is
"one dependency".

Everything above the Tk layer is plain data and pure functions: `CONTROLS`,
`style_from`, `settings_from`, so the part that can be wrong is testable
without a display. The widget wiring underneath is deliberately thin.
"""
from __future__ import annotations

import pathlib
import queue
import sys
import threading

from sqlalign.config import Width
from sqlalign.formatter import format_sql
from sqlalign.style import ALIGN_TARGETS, ALL_ALIGN_TARGETS, PRESETS, Style, preset_style

# `aliases` is the union of `column_aliases` and `table_aliases`, kept as a name
# so committed configs that use it keep working. A panel that offered all three
# would present two real choices as three checkboxes, and unchecking the parent
# would appear to do nothing: the enabled kinds are a union, so the children
# still cover it. The panel therefore offers the LEAF targets only, and expands
# a loaded `aliases` into them.
_UNION_TARGETS = {
    name: frozenset(
        leaf for leaf, kinds in ALIGN_TARGETS.items()
        if leaf != name and kinds < ALIGN_TARGETS[name])
    for name in ALIGN_TARGETS
}
PANEL_TARGETS = tuple(sorted(t for t in ALL_ALIGN_TARGETS if not _UNION_TARGETS[t]))


def _expand(targets):
    """A style's targets as the leaf set the panel shows."""
    out = set()
    for name in targets:
        out |= _UNION_TARGETS[name] or {name}
    return out

# One entry per Style field, in the order the panel shows them. `kind` picks the
# widget: choice -> combobox, flag -> checkbox, number -> spinbox, targets -> a
# column of checkboxes. A test asserts this covers every field, so a new knob
# cannot quietly go missing from the panel the way it would from a hand-kept list.
#
# `needs` marks a control that only does anything when another is set a
# particular way: the select indent means nothing while the list is inline, the
# river gutter means nothing while the keywords are flush left, and no alignment
# target means anything with alignment off. Those are greyed out rather than
# left live, because a spinbox you can change that changes nothing is a lie
# about what the setting does.
CONTROLS: tuple[dict, ...] = (
    {"name": "keyword_case", "kind": "choice", "choices": ("upper", "lower"),
     "label": "Keyword case"},
    {"name": "align", "kind": "flag", "label": "Align columns"},
    {"name": "align_targets", "kind": "targets", "label": "Aligned columns",
     "needs": ("align", True)},
    {"name": "comma_position", "kind": "choice", "choices": ("leading", "trailing"),
     "label": "Comma position"},
    {"name": "boolean_operator_position", "kind": "choice",
     "choices": ("leading", "trailing"), "label": "AND / OR position"},
    {"name": "select_placement", "kind": "choice", "choices": ("inline", "own_line"),
     "label": "SELECT list"},
    {"name": "select_indent", "kind": "number", "range": (1, 12), "label": "…indent",
     "needs": ("select_placement", "own_line")},
    {"name": "on_placement", "kind": "choice", "choices": ("inline", "own_line"),
     "label": "JOIN ON"},
    {"name": "clause_keyword_align", "kind": "choice", "choices": ("left", "river"),
     "label": "Clause keywords"},
    {"name": "river_gutter", "kind": "number", "range": (2, 20), "label": "…river gutter",
     "needs": ("clause_keyword_align", "river")},
    {"name": "table_alias_style", "kind": "choice", "choices": ("bare", "as"),
     "label": "Table aliases"},
    {"name": "neq_style", "kind": "choice", "choices": ("!=", "<>"), "label": "Not-equal"},
    {"name": "decimal_style", "kind": "choice", "choices": ("NUMERIC", "DECIMAL"),
     "label": "Decimal type"},
    {"name": "width", "kind": "number", "range": (0, 200), "label": "Width (0 = off)"},
    {"name": "body_blank_lines", "kind": "number", "range": (0, 4), "label": "$$ body blanks"},
    {"name": "blank_lines_between_statements", "kind": "number", "range": (-1, 4),
     "label": "Between statements (-1 = auto)"},
    {"name": "format_dollar_bodies", "kind": "flag", "label": "Format $$ bodies"},
    {"name": "protect_templating", "kind": "flag", "label": "Mask Jinja / dbt"},
)

# Exercises most of the surface at once: a stacked select list with aliases, a
# multi-table FROM with a join condition, a boolean continuation, a GROUP BY and
# an ORDER BY. Changing almost any control moves something visible here, and in
# the context of the other knobs rather than in isolation.
SAMPLES = {
    "postgres": (
        "select cust.customer_id, cust.email, sum(ord.total) as lifetime_value\n"
        "from customers cust\n"
        "inner join orders ord on ord.customer_id = cust.customer_id\n"
        "left join shipping_addresses addr on addr.order_id = ord.order_id\n"
        "where ord.order_date >= '2026-07-01' and cust.segment = 'enterprise'\n"
        "group by cust.customer_id, cust.email\n"
        "order by lifetime_value desc;\n"
    ),
    "tsql": (
        "select top 10 cust.customer_id, cust.email\n"
        "from customers cust\n"
        "inner join orders ord on ord.customer_id = cust.customer_id\n"
        "where ord.total > 0;\n"
    ),
}
SAMPLES["redshift"] = SAMPLES["postgres"]

# Above this many characters, formatting moves off the Tk thread. Measured at
# roughly 2ms a statement (~250 chars), so this is about 90ms of work: under
# the debounce, and well under the point where a freeze is perceptible.
_ASYNC_CHARS = 4000


# Shown in the preset box once the settings no longer match any preset. The
# box is a starting point, not a mode, but left showing "house" after you have
# changed three things it claims a style that is not in effect.
CUSTOM = "(custom)"


def preset_named(values: dict) -> str:
    """The preset these settings ARE, or `CUSTOM`.

    Compared on formatted OUTPUT rather than on Style equality: the panel offers
    only the leaf align targets, so a preset built from the `aliases` union comes
    back as its two children: a different frozenset that formats identically.
    """
    from sqlalign.formatter import format_sql

    try:
        mine = format_sql(SAMPLES["postgres"], "postgres", style_from(values)).text
    except ValueError:
        return CUSTOM
    for name in sorted(PRESETS):
        if format_sql(SAMPLES["postgres"], "postgres", preset_style(name)).text == mine:
            return name
    return CUSTOM


def default_settings() -> dict:
    """The panel's starting values, read off `Style`'s own defaults."""
    house = Style()
    values = {}
    for control in CONTROLS:
        name = control["name"]
        if name == "width":
            values[name] = house.width.width
        elif name == "align_targets":
            values[name] = _expand(house.align_targets)
        elif name == "blank_lines_between_statements":
            # The panel has no "unset"; -1 stands for it, which is why the label
            # says so. A spinbox that could be blank would be worse.
            values[name] = -1 if house.blank_lines_between_statements is None else \
                house.blank_lines_between_statements
        else:
            values[name] = getattr(house, name)
    return values


def settings_from(style: Style) -> dict:
    """Panel values for an existing `Style` — used when a preset is picked or a
    config file is loaded, so the controls show what is actually in effect."""
    values = default_settings()
    for control in CONTROLS:
        name = control["name"]
        if name == "width":
            values[name] = style.width.width
        elif name == "align_targets":
            values[name] = _expand(style.align_targets)
        elif name == "blank_lines_between_statements":
            values[name] = -1 if style.blank_lines_between_statements is None else \
                style.blank_lines_between_statements
        else:
            values[name] = getattr(style, name)
    return values


def style_from(values: dict) -> Style:
    """A `Style` from panel values. Raises ValueError on an invalid combination,
    which the panel reports rather than swallowing."""
    kwargs = {}
    for control in CONTROLS:
        name = control["name"]
        value = values[name]
        if name == "width":
            kwargs["width"] = Width(width=int(value))
        elif name == "align_targets":
            kwargs["align_targets"] = frozenset(value)
        elif name == "blank_lines_between_statements":
            kwargs[name] = None if int(value) < 0 else int(value)
        elif control["kind"] == "number":
            kwargs[name] = int(value)
        else:
            kwargs[name] = value
    return Style(**kwargs)


def preview(sql: str, values: dict, dialect: str) -> tuple[str, str]:
    """`(formatted, status)` for the preview pane.

    Never raises: an invalid setting or an engine failure becomes a status line,
    because a GUI that disappears when a spinbox is mid-edit is unusable.
    """
    try:
        style = style_from(values)
    except ValueError as e:
        return "", f"invalid setting — {e}"
    try:
        result = format_sql(sql, dialect, style)
    except Exception as e:                     # pragma: no cover - defence only
        return "", f"{type(e).__name__}: {e}"
    declined = len(result.declines)
    plural = "" if result.statements == 1 else "s"
    status = (f"{result.statements} statement{plural}, "
              f"{result.statements - declined} formatted")
    if declined:
        causes = ", ".join(sorted({d.reason for d in result.declines}))
        status += f" — {declined} declined ({causes})"
    return result.text, status


def _exactly_a_preset(values: dict) -> str | None:
    """The preset `values` exactly is, or None.

    Stricter than `preset_named`, deliberately. That compares the formatted
    output of one sample, which is the right question for the panel's dropdown:
    two settings that format identically are the same choice to a reader.

    It is the wrong question for a claim written into a file. `width` does not
    change that sample, so a project that set width to 71 would still be told it
    had the preset's values -- which is a sentence in their repository that is
    not true.
    """
    for name in sorted(PRESETS):
        if settings_from(preset_style(name)) == values:
            return name
    return None


def disabled_reason(name: str, values: dict) -> str | None:
    """Why the control named `name` is inert right now, or None if it is not.

    A greyed-out control with no explanation reads as a bug in the panel. The
    setting is real and will do something -- just not until the control it
    depends on says so, and that is a sentence the panel can say.

    Kept a plain function so it can be tested without a display; the whole
    module is otherwise only reachable through tkinter.
    """
    for control in CONTROLS:
        if control["name"] != name:
            continue
        need = control.get("needs")
        if need is None:
            return None
        depends_on, wanted = need
        if values.get(depends_on) == wanted:
            return None
        spelled = str(wanted).lower() if isinstance(wanted, bool) else f"{wanted}"
        return f"needs {depends_on} = {spelled}"
    return None


def as_toml(values: dict, *, dialect: str | None = None) -> str:
    """The panel's settings as a `.sqlalign.toml`.

    The values are written LIVE rather than commented out, which is the opposite
    of what `--init` writes and for the opposite reason: these are choices
    someone just made in the panel, not a menu they have yet to read.

    The body is `describe`, the same text `--show-config` prints, so what the
    GUI saves is what the CLI reads. The header exists because this file lands
    in a repository where the next person to open it did not choose any of it.
    """
    from sqlalign.configfile import describe

    header = [
        "# sqlalign configuration, written from the settings panel (`sqlalign --gui`).",
        "#",
        "# Every setting is written out, so this pins the style as it was chosen.",
        "# `sqlalign --init` writes the same settings commented out instead, if you",
        "# would rather follow a preset as it changes.",
        "#",
        "# Reference: https://sqlalign.lumaru.app/v1/settings.html",
    ]
    preset = _exactly_a_preset(values)
    if preset is not None:
        header.insert(1, f"# These are the `{preset}` preset's values.")
    if dialect:
        # `dialect` is CLI-only and has no config key, so it is recorded as a
        # comment rather than written as a setting that would fail to load.
        header.append("#")
        header.append(f"# Previewed with --dialect {dialect}, which has no config key.")
    return "\n".join(header) + "\n\n" + describe(style_from(values)) + "\n"


def run(dialect: str = "postgres") -> int:      # pragma: no cover - needs a display
    """Open the window. Returns a process exit code."""
    try:
        import tkinter as tk
    except ImportError:
        print("sqlalign: --gui needs tkinter, which is missing from this Python.\n"
              "          On Debian/Ubuntu: apt install python3-tk\n"
              "          On macOS with pyenv: install a Python built against Tcl/Tk.",
              file=sys.stderr)
        return 2

    root = tk.Tk()
    build(root, dialect)
    root.mainloop()
    return 0


def build(root, dialect: str = "postgres") -> dict:
    """Populate `root` with the whole interface, and return its parts.

    Separate from `run` so a test can construct the real widget tree and check it
    without entering the event loop, which is the only part of this file a test
    genuinely cannot drive.
    """
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root.title("sqlalign")
    root.geometry("1180x760")

    values = default_settings()
    state = {"dialect": dialect, "job": None, "seq": 0, "pump": None, "quiet": False,
             "path": None,
             "done": queue.Queue(), "want": queue.Queue()}

    def close():
        if state["pump"] is not None:
            root.after_cancel(state["pump"])
        root.destroy()
        # Nothing to shut down: the worker is a daemon, so a format still in
        # flight cannot hold the process open. A ThreadPoolExecutor could and
        # did: `shutdown(wait=False)` does not cancel RUNNING work, and the
        # interpreter joins the pool's threads at exit regardless, so closing
        # the window part-way through a large file left the app alive, with no
        # window, for as long as the format took.

    root.protocol("WM_DELETE_WINDOW", close)

    outer = ttk.Frame(root, padding=8)
    outer.pack(fill="both", expand=True)

    # The settings column scrolls. Eighteen controls plus the target checkboxes
    # need ~900px, which is taller than the window on any laptop: without this
    # without it the last few controls are unreachable.
    side = ttk.Frame(outer)
    side.pack(side="left", fill="y", padx=(0, 8))
    # The actions pin to the bottom of the column, OUTSIDE the scrolling area.
    # They were inside it, which put every button below the fold at any normal
    # window height: the primary actions were the hardest things to reach.
    action_bar = ttk.Frame(side)
    action_bar.pack(side="bottom", fill="x", pady=(8, 0))
    tray = tk.Canvas(side, highlightthickness=0, borderwidth=0)
    rail = ttk.Scrollbar(side, orient="vertical", command=tray.yview)
    tray.configure(yscrollcommand=rail.set)
    rail.pack(side="right", fill="y")
    tray.pack(side="left", fill="both", expand=True)
    panel = ttk.Frame(tray)
    slot = tray.create_window((0, 0), window=panel, anchor="nw")

    def _fit(_=None):
        tray.configure(scrollregion=tray.bbox("all"), width=panel.winfo_reqwidth())

    panel.bind("<Configure>", _fit)
    tray.bind("<Configure>", lambda e: tray.itemconfigure(slot, width=e.width))

    def _wheel(event):
        # Tk reports wheel deltas differently per platform; normalise to lines.
        step = -1 if event.delta > 0 else 1
        if event.num == 4:
            step = -1
        elif event.num == 5:
            step = 1
        tray.yview_scroll(step, "units")

    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        tray.bind_all(sequence, _wheel, add="+")

    panes = ttk.Frame(outer)
    panes.pack(side="left", fill="both", expand=True)

    # ---- panes -----------------------------------------------------------
    split = ttk.PanedWindow(panes, orient="vertical")
    split.pack(fill="both", expand=True)
    mono = ("Menlo", 12) if root.tk.call("tk", "windowingsystem") == "aqua" else ("TkFixedFont", 10)

    def scrolled(parent, **options):
        """A Text with both scrollbars. `wrap="none"` keeps the columns intact,
        which makes a horizontal scrollbar mandatory rather than optional: a
        long line is otherwise just cut off with no way to reach the rest."""
        holder = ttk.Frame(parent)
        # Matched chrome on both panes. Left to Tk's defaults the editable one
        # draws a heavy focus border and the read-only one draws none, so two
        # widgets that should read as siblings looked like different kinds of
        # thing.
        text = tk.Text(holder, wrap="none", font=mono, relief="solid",
                       borderwidth=1, highlightthickness=0, **options)
        down = ttk.Scrollbar(holder, orient="vertical", command=text.yview)
        across = ttk.Scrollbar(holder, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=down.set, xscrollcommand=across.set)
        text.grid(row=0, column=0, sticky="nsew")
        down.grid(row=0, column=1, sticky="ns")
        across.grid(row=1, column=0, sticky="ew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        return holder, text

    in_frame = ttk.Labelframe(split, text="Input", padding=4)
    holder, source = scrolled(in_frame, undo=True, height=12)
    holder.pack(fill="both", expand=True)
    split.add(in_frame, weight=1)

    # The read-only pane takes the THEME's window background rather than a
    # literal colour. A hardcoded light grey pairs with `systemTextColor`, which
    # is dynamic, so in dark mode it was light text on a light background.
    # Window-background against default text is the pairing every ttk.Label
    # already uses, so it is legible in both by construction.
    read_only_bg = ttk.Style().lookup("TFrame", "background") or None
    out_frame = ttk.Labelframe(split, text="Formatted", padding=4)
    holder, output = scrolled(out_frame, height=16, state="disabled",
                              **({"background": read_only_bg} if read_only_bg else {}))
    holder.pack(fill="both", expand=True)
    split.add(out_frame, weight=1)

    status = ttk.Label(panes, text="", anchor="w")
    status.pack(fill="x", pady=(6, 0))

    def show(text, note):
        output.configure(state="normal")
        output.delete("1.0", "end")
        output.insert("1.0", text)
        output.configure(state="disabled")
        status.configure(text=note)

    def render(*_):
        """Reformat and show the result.

        Formatting is linear in file size: about 2ms a statement, so ~380ms for
        1400 lines and near a second for 3500. On the Tk thread that is a visible
        freeze every time typing pauses, in exactly the case "Open SQL…" invites.
        Above `_ASYNC_CHARS` it moves to a worker; below it the round trip would
        cost more than the work, and staying synchronous keeps small edits (and
        the tests) immediate.

        `format_sql` is safe to call off-thread: it carries style on a contextvar,
        which is per-thread, verified under concurrent mixed-style runs.
        """
        sql, dialect = source.get("1.0", "end-1c"), state["dialect"]
        if len(sql) < _ASYNC_CHARS:
            show(*preview(sql, values, dialect))
            return

        state["seq"] += 1
        seq = state["seq"]
        snapshot = dict(values)
        status.configure(text="formatting…")

        state["want"].put((seq, sql, snapshot, dialect))

    def schedule(*_):
        """Coalesce keystrokes: reformatting on every character makes typing
        feel heavy on a large paste."""
        if state["job"] is not None:
            root.after_cancel(state["job"])
        state["job"] = root.after(150, render)

    source.bind("<KeyRelease>", schedule)

    def worker():
        """One long-lived daemon thread, formatting the most recent request.

        Results go back through a queue, NOT through `root.after`: Tk methods
        must not be called from another thread at all, `after` included, and
        doing so silently never dispatches: the preview simply stopped updating
        for any file past the threshold, which is worse than the freeze it
        replaced.

        Requests overtaken while it was busy are dropped rather than formatted
        and thrown away, so typing does not queue one format per keystroke
        burst.
        """
        while True:
            seq, sql, snapshot, dialect = state["want"].get()
            if seq == state["seq"]:
                state["done"].put((seq, preview(sql, snapshot, dialect)))

    threading.Thread(target=worker, name="sqlalign-fmt", daemon=True).start()

    def pump():
        """Drain finished background formats, on the Tk thread where it is safe
        to touch widgets. Results older than the latest request are dropped --
        they describe text the user has already typed past."""
        try:
            while True:
                job, result = state["done"].get_nowait()
                if job == state["seq"]:
                    show(*result)
        except queue.Empty:
            pass
        state["pump"] = root.after(60, pump)

    pump()

    # ---- controls --------------------------------------------------------
    def row(parent, label):
        line = ttk.Frame(parent)
        line.pack(fill="x", pady=1)
        ttk.Label(line, text=label, width=22, anchor="w").pack(side="left")
        return line

    top = ttk.Labelframe(panel, text="Start from", padding=6)
    top.pack(fill="x")

    dialect_var = tk.StringVar(value=dialect)

    def on_dialect(*_):
        state["dialect"] = dialect_var.get()
        current = source.get("1.0", "end-1c").strip()
        if current in {s.strip() for s in SAMPLES.values()}:
            source.delete("1.0", "end")
            source.insert("1.0", SAMPLES[state["dialect"]])
        render()

    line = row(top, "Dialect")
    box = ttk.Combobox(line, textvariable=dialect_var, values=["postgres", "redshift", "tsql"],
                       state="readonly", width=12)
    box.pack(side="left", fill="x", expand=True)
    dialect_var.trace_add("write", on_dialect)

    widgets: dict = {}
    boxes: dict = {}          # name -> the actual widgets, for enabling/disabling
    notes: dict = {}          # name -> the label that says why it is greyed out

    def refresh_enabled():
        """Grey out every control whose `needs` is not currently satisfied."""
        for control in CONTROLS:
            need = control.get("needs")
            if need is None:
                continue
            depends_on, wanted = need
            state = "normal" if values[depends_on] == wanted else "disabled"
            for widget in boxes.get(control["name"], ()):
                widget.configure(state=state if state == "normal"
                                 or control["kind"] != "choice" else "disabled")
            # Say WHY, rather than leaving a greyed control looking broken.
            note = notes.get(control["name"])
            if note is not None:
                note.configure(text=disabled_reason(control["name"], values) or "")

    def refresh_widgets():
        """Push `values` back into the controls, after a preset or a file load."""
        for control in CONTROLS:
            name = control["name"]
            if control["kind"] == "targets":
                for target, var in widgets[name].items():
                    var.set(target in values[name])
            else:
                widgets[name].set(values[name])
        refresh_enabled()

    preset_var = tk.StringVar(value="house")

    def on_preset(*_):
        if state["quiet"] or preset_var.get() == CUSTOM:
            return
        values.update(settings_from(preset_style(preset_var.get())))
        refresh_widgets()
        render()

    line = row(top, "Preset")
    ttk.Combobox(line, textvariable=preset_var, values=[*sorted(PRESETS), CUSTOM],
                 state="readonly", width=12).pack(side="left", fill="x", expand=True)
    preset_var.trace_add("write", on_preset)

    settings = ttk.Labelframe(panel, text="Settings", padding=6)
    settings.pack(fill="both", expand=True, pady=(8, 0))

    def resync_preset():
        """Keep the preset box honest as individual settings are changed."""
        name = preset_named(values)
        if preset_var.get() != name:
            state["quiet"] = True          # not a user pick; do not re-apply it
            preset_var.set(name)
            state["quiet"] = False

    def bind(name, var):
        def changed(*_):
            values[name] = var.get()
            refresh_enabled()
            resync_preset()
            render()
        var.trace_add("write", changed)

    for control in CONTROLS:
        name, kind = control["name"], control["kind"]
        if control.get("needs"):
            # One label per dependent control, filled in by refresh_enabled. Packed
            # before the control itself so the reason sits above what it explains.
            notes[name] = ttk.Label(settings, text="", foreground="grey")
            notes[name].pack(anchor="w")
        if kind == "flag":
            var = tk.BooleanVar(value=values[name])
            box = ttk.Checkbutton(settings, text=control["label"], variable=var)
            box.pack(anchor="w", pady=1)
            boxes[name] = [box]
            bind(name, var)
        elif kind == "choice":
            var = tk.StringVar(value=values[name])
            line = row(settings, control["label"])
            box = ttk.Combobox(line, textvariable=var, values=list(control["choices"]),
                               state="readonly", width=12)
            box.pack(side="left", fill="x", expand=True)
            boxes[name] = [box]
            bind(name, var)
        elif kind == "number":
            var = tk.IntVar(value=values[name])
            line = row(settings, control["label"])
            low, high = control["range"]
            box = ttk.Spinbox(line, from_=low, to=high, textvariable=var, width=10)
            box.pack(side="left", fill="x", expand=True)
            boxes[name] = [box]
            bind(name, var)
        elif kind == "targets":
            heading = ttk.Label(settings, text=control["label"])
            heading.pack(anchor="w", pady=(6, 0))
            group: dict = {}
            boxes[name] = []
            for target in PANEL_TARGETS:
                var = tk.BooleanVar(value=target in values[name])
                box = ttk.Checkbutton(settings, text=f"  {target}", variable=var)
                box.pack(anchor="w")
                boxes[name].append(box)

                # `key` is bound here, not read from the enclosing loop: the
                # callback fires long after the loop has moved on.
                def changed(*_, key=name, t=target, v=var):
                    values[key] = set(values[key]) | {t} if v.get() else \
                        set(values[key]) - {t}
                    render()
                var.trace_add("write", changed)
                group[target] = var
            widgets[name] = group
            continue
        widgets[name] = var

    # ---- actions ---------------------------------------------------------
    def report(action, error):
        """Every file action goes through here. Without it an unreadable file, an
        unwritable path or a non-UTF-8 SQL dump raised into Tk's handler: a
        traceback on stderr that nobody sees, and a window that appears to ignore
        the click entirely."""
        messagebox.showerror("sqlalign", f"Could not {action}.\n\n{error}")

    def read_text(path):
        """SQL dumps are not always UTF-8 — latin-1 is common in older exports,
        and failing on one byte is not a reason to refuse the file. Decode
        strictly first so a genuinely UTF-8 file is never mangled, and fall back
        only when it is not one."""
        raw = pathlib.Path(path).read_bytes()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")

    def retitle():
        """macOS convention: the window is named for the document it holds.

        Without this the title read `sqlalign` forever, so a window that had a
        file open looked exactly like one holding the built-in sample, and the
        only way to tell which file you were formatting was to recognise its
        contents.
        """
        root.title(f"sqlalign — {pathlib.Path(state['path']).name}"
                   if state["path"] else "sqlalign")

    def open_file():
        path = filedialog.askopenfilename(filetypes=[("SQL", "*.sql"), ("All", "*.*")])
        if not path:
            return
        try:
            text = read_text(path)
        except OSError as e:
            report("open that file", e)
            return
        source.delete("1.0", "end")
        source.insert("1.0", text)
        # A fresh document, so the previous file's undo history is not this
        # file's: one Cmd+Z should not resurrect the last file's text.
        source.edit_reset()
        state["path"] = path
        retitle()
        render()

    def save_output():
        # Defaulted to the file that was opened, because formatting a file in
        # place is what the CLI does and what the button is nearly always for.
        # Defaulted, not silent: the dialog still asks before overwriting.
        here = pathlib.Path(state["path"]) if state["path"] else None
        path = filedialog.asksaveasfilename(
            defaultextension=".sql", filetypes=[("SQL", "*.sql")],
            **({"initialdir": str(here.parent), "initialfile": here.name} if here else {}))
        if not path:
            return
        try:
            pathlib.Path(path).write_text(output.get("1.0", "end-1c"))
        except OSError as e:
            report("save there", e)
            return
        state["path"] = path
        retitle()

    def save_config():
        path = filedialog.asksaveasfilename(initialfile=".sqlalign.toml")
        if not path:
            return
        try:
            pathlib.Path(path).write_text(as_toml(values, dialect=dialect))
        except OSError as e:
            report("save there", e)

    def load_config():
        from sqlalign import configfile
        path = filedialog.askopenfilename(filetypes=[("TOML", "*.toml"), ("All", "*.*")])
        if not path:
            return
        try:
            settings_data, _ = configfile.load_settings(path)
            values.update(settings_from(configfile.build_style(settings_data)))
        except Exception as e:
            report("load that config", e)
            return
        refresh_widgets()
        resync_preset()
        render()

    def copy_output():
        root.clipboard_clear()
        root.clipboard_append(output.get("1.0", "end-1c"))

    # The output pane is read-only, so selecting it by hand to copy is the one
    # thing every user will want to do and the one thing the widget makes least
    # obvious. Give it a button AND a shortcut.
    aqua = root.tk.call("tk", "windowingsystem") == "aqua"
    mod, shown = ("Command", "⌘") if aqua else ("Control", "Ctrl+")

    # Copy-formatted is SHIFT-modified deliberately. Binding plain Cmd/Ctrl+C
    # globally stomps ordinary copy: select a word in the input pane, press it,
    # and you get the whole formatted output instead of your selection.
    actions = action_bar
    bound: dict = {}
    for label, key, command in (
        (f"Copy formatted  {shown}⇧C" if aqua else f"Copy formatted  {shown}Shift+C",
         "Shift-C", copy_output),
        (f"Open SQL…  {shown}O", "o", open_file),
        (f"Save formatted…  {shown}S", "s", save_output),
        ("Load config…", None, load_config),
        ("Save config…", None, save_config),
    ):
        ttk.Button(actions, text=label, command=command).pack(fill="x", pady=1)
        if key:
            # bind_all so the shortcut works wherever focus happens to be. Note
            # "all" is the LAST bindtag, so a binding here runs AFTER the Text
            # class binding, which is exactly why plain Cmd/Ctrl+C would have
            # overwritten an ordinary copy rather than being shadowed by it.
            sequence = f"<{mod}-{key}>"
            root.bind_all(sequence, lambda _e, fn=command: (fn(), "break")[1])
            bound[sequence] = command

    # Below this the panes stop being usable: the settings column alone needs
    # ~360px, and a preview narrower than the sample is not a preview.
    root.update_idletasks()
    root.minsize(side.winfo_reqwidth() + 520, 420)

    refresh_enabled()
    source.insert("1.0", SAMPLES[dialect])
    render()
    # Type immediately rather than having to click the pane first.
    source.focus_set()
    return {"source": source, "output": output, "status": status, "values": values,
            "widgets": widgets, "render": render, "panel": panel, "preset": preset_var,
            "tray": tray,
            "state": state, "retitle": retitle,
            "actions": {"copy": copy_output, "open": open_file, "save": save_output,
                        "load_config": load_config, "save_config": save_config,
                        "read_text": read_text},
            "accelerators": bound}
