"""The GUI's logic, tested without a display.

Everything that can be wrong lives above the Tk layer — `CONTROLS`,
`style_from`, `settings_from`, `preview` — so it is plain data and pure
functions, and this file never opens a window. The widget wiring underneath is
deliberately thin enough that reading it is cheaper than driving it.

The load-bearing test is `test_every_style_field_has_a_control`. A knob that is
in `Style` but not in `CONTROLS` is invisible in the panel and impossible to
reach — the same failure mode the config file's reflection guard exists to catch,
one layer up.
"""
import dataclasses

import pytest

# tkinter is stdlib but not always packaged: a uv-managed Python on Linux
# has no `_tkinter`, and neither does a slim container. The GUI is optional,
# so the whole module skips rather than failing the suite.
pytest.importorskip("tkinter")
from conftest import DIALECTS, load_pair
from conftest import SAMPLES as SAMPLES_ALL

from sqlalign.formatter import format_sql
from sqlalign.gui import (
    CONTROLS,
    SAMPLES,
    as_toml,
    default_settings,
    disabled_reason,
    preview,
    settings_from,
    style_from,
)
from sqlalign.style import ALL_ALIGN_TARGETS, HOUSE, PRESETS, Style, preset_style

# ---- the panel covers the whole surface ----------------------------------

def test_every_style_field_has_a_control():
    """A knob absent from CONTROLS cannot be reached from the panel at all."""
    fields = {f.name for f in dataclasses.fields(Style)}
    controls = {c["name"] for c in CONTROLS}
    assert fields == controls, f"missing from the panel: {sorted(fields - controls)}"


def test_every_control_names_a_real_field():
    fields = {f.name for f in dataclasses.fields(Style)}
    assert {c["name"] for c in CONTROLS} <= fields


@pytest.mark.parametrize("control", CONTROLS, ids=lambda c: c["name"])
def test_each_control_is_well_formed(control):
    assert control["kind"] in {"choice", "flag", "number", "targets"}
    assert control["label"]
    if control["kind"] == "choice":
        assert len(control["choices"]) >= 2
    if control["kind"] == "number":
        low, high = control["range"]
        assert low < high


def test_choices_match_what_style_accepts():
    """A choice the panel offers but Style rejects is a crash waiting for a
    click; one Style accepts but the panel omits is an unreachable setting."""
    for control in CONTROLS:
        if control["kind"] != "choice":
            continue
        for choice in control["choices"]:
            Style(**{control["name"]: choice})          # must not raise
        with pytest.raises(ValueError):
            Style(**{control["name"]: "definitely-not-valid"})


# ---- values round-trip ---------------------------------------------------

def _formats_the_same(a, b):
    """Two styles agree on every golden. Used instead of `==` where the panel
    legitimately normalises `align_targets` to its leaf names."""
    for sid in SAMPLES_ALL:
        inp = load_pair(sid)[0]
        dialect = DIALECTS.get(sid, "postgres")
        if format_sql(inp, dialect, a).text != format_sql(inp, dialect, b).text:
            return False
    return True


def test_defaults_reproduce_the_house_style():
    assert _formats_the_same(style_from(default_settings()), HOUSE)


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_every_preset_round_trips(name):
    """Picking a preset fills the controls; reading them back must format the
    same, or the panel would silently drift from what it displays.

    Not literal equality: the panel offers only the LEAF align targets, so a
    style using the `aliases` union comes back as its two children. Same enabled
    kinds, different frozenset — so the assertion is on the output, checked here
    against every golden rather than one sample."""
    style = preset_style(name)
    restored = style_from(settings_from(style))
    for sid in SAMPLES_ALL:
        inp = load_pair(sid)[0]
        dialect = DIALECTS.get(sid, "postgres")
        assert (format_sql(inp, dialect, style).text
                == format_sql(inp, dialect, restored).text), f"{name} drifted on sample {sid}"


def test_the_unset_blank_lines_sentinel_round_trips():
    """The panel has no empty spinbox, so -1 stands for "unset". It has to come
    back as None rather than as a negative count."""
    values = default_settings()
    assert values["blank_lines_between_statements"] == -1
    assert style_from(values).blank_lines_between_statements is None

    values["blank_lines_between_statements"] = 2
    assert style_from(values).blank_lines_between_statements == 2


def test_width_zero_is_carried_through_as_the_off_sentinel():
    values = default_settings()
    values["width"] = 0
    assert style_from(values).width.width == 0


def test_the_panel_offers_only_non_overlapping_targets():
    """`aliases` is the union of `column_aliases` and `table_aliases`. Offering
    all three would present two real choices as three checkboxes, and unchecking
    the parent would appear to do nothing, because the enabled kinds are a union
    and the children still cover it."""
    from sqlalign.gui import PANEL_TARGETS
    from sqlalign.style import ALIGN_TARGETS

    assert "aliases" not in PANEL_TARGETS
    assert {"column_aliases", "table_aliases"} <= set(PANEL_TARGETS)
    for name in PANEL_TARGETS:
        others = [ALIGN_TARGETS[o] for o in PANEL_TARGETS if o != name]
        assert not any(ALIGN_TARGETS[name] < o for o in others), f"{name} is a union"


def test_align_targets_are_a_set_of_valid_names():
    values = default_settings()
    assert values["align_targets"] <= ALL_ALIGN_TARGETS
    values["align_targets"] = {"column_aliases"}
    assert style_from(values).align_targets == frozenset({"column_aliases"})


def test_a_spinbox_string_is_accepted():
    """Tk hands numbers back as strings when the user types rather than clicks."""
    values = default_settings()
    values["select_indent"] = "4"
    assert style_from(values).select_indent == 4


# ---- the preview ---------------------------------------------------------

def test_preview_matches_the_engine():
    """What the pane shows must be what the CLI would write — the whole point."""
    values = default_settings()
    text, _ = preview(SAMPLES["postgres"], values, "postgres")
    assert text == format_sql(SAMPLES["postgres"], "postgres", HOUSE).text


def test_preview_reports_the_statement_count():
    _, status = preview("select a from t;\nselect b from u;\n", default_settings(), "postgres")
    assert "2 statements, 2 formatted" in status


def test_the_statement_count_is_not_written_1_statements():
    """Visible in the status bar on every single-statement edit, which is most
    of them."""
    _, status = preview("select a from t;", default_settings(), "postgres")
    assert status.startswith("1 statement,"), status


def test_preview_names_what_declined():
    _, status = preview("select * from t pivot (sum(x) for y in (1, 2)) p;",
                        default_settings(), "postgres")
    assert "1 declined" in status and "PIVOT" in status


def test_preview_reports_an_invalid_setting_instead_of_raising():
    """A GUI that disappears mid-edit is unusable, so this path returns a status
    line rather than propagating."""
    values = default_settings()
    values["select_indent"] = 0                      # Style requires >= 1
    text, status = preview("select a from t;", values, "postgres")
    assert text == ""
    assert "invalid setting" in status


def test_preview_survives_unparseable_input():
    text, status = preview("this is not sql at all (((;", default_settings(), "postgres")
    assert "declined" in status
    assert text.strip() == "this is not sql at all (((;"


@pytest.mark.parametrize("dialect", ["postgres", "redshift", "tsql"])
def test_each_dialect_has_a_sample_that_formats(dialect):
    text, status = preview(SAMPLES[dialect], default_settings(), dialect)
    assert "declined" not in status, status
    assert text.strip()


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_the_sample_formats_under_every_preset(preset):
    values = settings_from(preset_style(preset))
    _, status = preview(SAMPLES["postgres"], values, "postgres")
    assert "declined" not in status, status


# ---- config round-trip ---------------------------------------------------

def test_saved_config_is_what_show_config_prints():
    """The GUI must not grow its own config dialect.

    Compared with the header stripped rather than as whole strings: the file
    carries a comment block saying where it came from, and asserting on the
    settings alone is the actual invariant. Stripping comments also proves the
    header cannot smuggle a setting past this test.
    """
    from sqlalign.configfile import describe

    values = settings_from(preset_style("gitlab"))
    written = [line for line in as_toml(values).splitlines()
               if line.strip() and not line.startswith("#")]
    expected = [line for line in describe(preset_style("gitlab")).splitlines()
                if line.strip() and not line.startswith("#")]
    assert written == expected


def test_saved_config_loads_back_into_the_same_style(tmp_path):
    from sqlalign import configfile

    values = settings_from(preset_style("river"))
    path = tmp_path / ".sqlalign.toml"
    path.write_text(as_toml(values))
    loaded = configfile.build_style(configfile.load_settings(path)[0])
    assert _formats_the_same(loaded, preset_style("river"))


# ---- the CLI entry point -------------------------------------------------

def test_gui_flag_exists_and_does_not_need_files(monkeypatch):
    """`--gui` is the one mode with nothing to format, so it must not trip the
    "files are required" check."""
    import sqlalign.gui as gui_module
    from sqlalign.cli import main

    called = {}

    def stub(dialect):
        called["d"] = dialect
        return 0

    monkeypatch.setattr(gui_module, "run", stub)
    assert main(["--gui"]) == 0
    assert called["d"] == "postgres"


def test_gui_passes_the_dialect_through(monkeypatch):
    import sqlalign.gui as gui_module
    from sqlalign.cli import main

    called = {}

    def stub(dialect):
        called["d"] = dialect
        return 0

    monkeypatch.setattr(gui_module, "run", stub)
    main(["--gui", "--dialect", "tsql"])
    assert called["d"] == "tsql"


# ---- the widget tree actually builds ------------------------------------

def _root(mapped=False):
    """A Tk root, or a skip if this environment has no display.

    Hidden by default so the suite does not flash windows. `mapped=True` for the
    clipboard tests: Tk cannot own the CLIPBOARD selection from a withdrawn
    window, so those need it on screen briefly.
    """
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as e:                        # headless CI
        pytest.skip(f"no display: {e}")
    if mapped:
        root.geometry("400x300+0+0")
    else:
        root.withdraw()
    return root


def test_the_interface_constructs():
    """The one part a pure-logic test cannot reach. Catches an exception during
    construction, a control that fails to render, and a missing pane."""
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        assert set(parts["widgets"]) == {c["name"] for c in CONTROLS}
        assert parts["output"].get("1.0", "end-1c").startswith("SELECT cust.customer_id")
        assert "1 statement, 1 formatted" in parts["status"].cget("text")
    finally:
        root.destroy()


def test_changing_a_control_reformats_the_preview():
    """The wiring itself: moving a widget must reach the engine and come back."""
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        before = parts["output"].get("1.0", "end-1c")
        parts["widgets"]["keyword_case"].set("lower")
        root.update_idletasks()
        after = parts["output"].get("1.0", "end-1c")
        assert before.startswith("SELECT") and after.startswith("select"), after[:40]
    finally:
        root.destroy()


def test_toggling_an_align_target_reformats_the_preview():
    """The targets group has its own callback shape — a per-checkbox closure —
    so it is wired separately from every other control and tested separately."""
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        before = parts["output"].get("1.0", "end-1c")
        # `operators` is the one whose effect the sample makes visible: the two
        # WHERE conditions have different widths, so the `=` column pads.
        assert "cust.segment    = " in before, before
        parts["widgets"]["align_targets"]["operators"].set(False)
        root.update_idletasks()
        after = parts["output"].get("1.0", "end-1c")
        assert "cust.segment = " in after, after
    finally:
        root.destroy()


# ---- everything stays reachable -----------------------------------------

def _find(node, cls):
    found = []
    for child in node.winfo_children():
        if isinstance(child, cls):
            found.append(child)
        found += _find(child, cls)
    return found


@pytest.mark.parametrize("size", [(1180, 760), (1000, 600), (900, 500)])
def test_every_setting_is_reachable_at_any_window_size(size):
    """The panel needs ~900px of controls, which is taller than the window on any
    laptop. It shipped without a scrollbar, so the last several controls — the
    two `$$`/templating checkboxes among them — could not be clicked at all."""
    import tkinter as tk

    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        root.geometry("{}x{}".format(*size))
        root.update_idletasks()
        root.update()

        canvases = _find(root, tk.Canvas)
        assert len(canvases) == 1, "the settings column should scroll in a canvas"
        region = canvases[0].cget("scrollregion").split()
        assert region, "no scrollregion — the panel cannot scroll"
        assert int(float(region[3])) >= parts["panel"].winfo_reqheight(), (
            "scrollregion does not cover the controls, so the bottom ones are lost")
    finally:
        root.destroy()


def test_both_text_panes_scroll_in_both_directions():
    """`wrap="none"` keeps the columns intact, which makes a horizontal
    scrollbar mandatory rather than optional: without one a long line is cut off
    with no way to reach the rest of it."""
    from tkinter import ttk

    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        for pane in ("source", "output"):
            holder = parts[pane].master
            orients = {str(b.cget("orient")) for b in _find(holder, ttk.Scrollbar)}
            assert orients == {"vertical", "horizontal"}, f"{pane}: {orients}"
    finally:
        root.destroy()


def test_a_long_line_is_scrollable_rather_than_lost():
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        parts["source"].delete("1.0", "end")
        # One very wide item, not many: sqlalign puts each select item on its own
        # line, so a long LIST produces short lines. A long single expression is
        # what actually overflows.
        parts["source"].insert("1.0", "select " + "x" * 400 + " as v from t;")
        parts["render"]()
        root.update_idletasks()
        # xview is (start, end) as fractions; end < 1 means there is more to the
        # right, which the horizontal scrollbar can reach.
        assert parts["output"].xview()[1] < 1.0, "long line fits, pick a longer one"
    finally:
        root.destroy()


# ---- keyboard and window limits -----------------------------------------

def _mod(root):
    return "Command" if root.tk.call("tk", "windowingsystem") == "aqua" else "Control"


def test_copy_formatted_writes_the_output():
    """The action itself, invoked directly.

    Not via a synthesised keystroke: `event_generate` does not dispatch modifier
    combinations in this environment — a probe binding does not fire either — so
    a key-event test would be asserting on the window server rather than on this
    code. Worse, `clipboard_get` then returns whatever was on the clipboard
    BEFORE, which reads as a pass. The accelerator wiring is checked separately,
    below, by inspecting the bindings.
    """
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        written = []
        root.clipboard_append = lambda text, **_: written.append(text)
        root.clipboard_clear = lambda **_: written.clear()
        parts["actions"]["copy"]()
        assert written and written[0].startswith("SELECT cust.customer_id")
    finally:
        root.destroy()


def test_the_accelerators_are_bound_to_the_right_actions():
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        mod = _mod(root)
        assert parts["accelerators"] == {
            f"<{mod}-Shift-C>": parts["actions"]["copy"],
            f"<{mod}-o>": parts["actions"]["open"],
            f"<{mod}-s>": parts["actions"]["save"],
        }
    finally:
        root.destroy()


def test_plain_copy_is_left_alone():
    """The regression guard. `bind_all` registers on the "all" bindtag, which is
    LAST — so a plain Cmd/Ctrl+C binding runs AFTER the Text class binding has
    done an ordinary copy, and overwrites the clipboard with the whole formatted
    output. Selecting a word and copying it silently gave you the wrong thing.
    The fix is the Shift modifier; this asserts plain copy stays unbound."""
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        mod = _mod(root)
        assert f"<{mod}-c>" not in parts["accelerators"]
        assert not root.bind_all(f"<{mod}-c>"), "the app bound plain copy"
        assert parts["source"].bindtags()[-1] == "all", (
            "bind_all is no longer last; the ordering this guards has changed")
    finally:
        root.destroy()


def test_the_window_cannot_be_shrunk_below_usable():
    """It defaulted to a 72x15 minimum, at which point nothing is reachable."""
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        root.update_idletasks()
        width, height = root.minsize()
        assert width >= parts["panel"].winfo_reqwidth(), "narrower than the settings column"
        assert height >= 400
    finally:
        root.destroy()


# ---- theme and focus -----------------------------------------------------

def test_the_read_only_pane_follows_the_theme():
    """It was a hardcoded light grey against `systemTextColor`, which is dynamic
    — so in dark mode the formatted SQL was light text on a light background.
    Taking the theme's own window background pairs it with the default text
    colour, which is the combination every ttk.Label already uses."""
    from tkinter import ttk

    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        theme_bg = ttk.Style().lookup("TFrame", "background")
        assert str(parts["output"].cget("background")) == str(theme_bg)
        assert not str(parts["output"].cget("background")).startswith("#"), (
            "a literal colour cannot follow light/dark mode")
    finally:
        root.destroy()


def test_the_input_pane_has_focus_at_startup():
    """So you can type without clicking first. `focus_get` only reports when the
    application is frontmost, which a scripted window is not — `focus_lastfor`
    is the durable answer: who receives focus when the window activates."""
    from sqlalign.gui import build

    root = _root(mapped=True)
    try:
        parts = build(root, "postgres")
        root.update()
        assert root.focus_lastfor() is parts["source"]
    finally:
        root.destroy()


def test_every_control_is_reachable_by_tab():
    """A control you can see but cannot tab to is unreachable for anyone not
    using a mouse."""
    from tkinter import ttk

    from sqlalign.gui import build

    root = _root(mapped=True)
    try:
        parts = build(root, "postgres")
        root.update()
        walk, current = [], parts["source"]
        for _ in range(200):
            nxt = current.tk_focusNext()
            if nxt is None or str(nxt) in {str(w) for w in walk}:
                break
            walk.append(nxt)
            current = nxt
        reached = {str(w) for w in walk} | {str(parts["source"])}
        controls = _find(root, (ttk.Combobox, ttk.Checkbutton, ttk.Spinbox, ttk.Button))
        # A greyed-out control is deliberately out of the ring — tabbing onto
        # something you cannot change is worse than skipping it. Enable
        # everything first so this tests reachability, not the greying.
        parts["widgets"]["select_placement"].set("own_line")
        parts["widgets"]["clause_keyword_align"].set("river")
        root.update()
        walk, current = [], parts["source"]
        for _ in range(200):
            nxt = current.tk_focusNext()
            if nxt is None or str(nxt) in {str(w) for w in walk}:
                break
            walk.append(nxt)
            current = nxt
        reached = {str(w) for w in walk} | {str(parts["source"])}
        missed = [c for c in controls if str(c) not in reached]
        assert not missed, [type(c).__name__ for c in missed]
        # The output pane is deliberately outside the tab ring: it is read-only,
        # and Copy formatted reaches its content without focusing it.
        assert str(parts["output"]) not in reached
    finally:
        root.destroy()


# ---- large files do not freeze the window -------------------------------

def _settle(root, parts, seconds=15):
    import time
    deadline = time.time() + seconds
    while time.time() < deadline and "formatting" in parts["status"].cget("text"):
        root.update()
        time.sleep(0.03)


def test_a_large_file_does_not_block_the_ui():
    """Formatting is linear — roughly 2ms a statement — so 1400 lines is ~380ms
    of frozen window on every typing pause. Past the threshold it runs on a
    worker and `render` returns immediately."""
    import time

    from sqlalign.gui import SAMPLES, build

    root = _root()
    try:
        parts = build(root, "postgres")
        root.update()
        parts["source"].delete("1.0", "end")
        parts["source"].insert("1.0", SAMPLES["postgres"] * 200)

        start = time.perf_counter()
        parts["render"]()
        blocked_ms = (time.perf_counter() - start) * 1000
        assert blocked_ms < 100, f"render blocked for {blocked_ms:.0f}ms"
    finally:
        root.destroy()


def test_the_background_result_is_actually_delivered():
    """The first attempt handed results back with `root.after` from the worker.
    Tk methods must not be called off-thread at all — `after` included — and it
    silently never dispatches, so the preview just stopped updating for any file
    past the threshold. That is worse than the freeze it replaced, and only a
    test that checks the CONTENT catches it: the status line and the first line
    of output both look fine."""
    from sqlalign.gui import SAMPLES, build

    root = _root()
    try:
        parts = build(root, "postgres")
        root.update()
        parts["source"].delete("1.0", "end")
        parts["source"].insert("1.0", SAMPLES["postgres"] * 200)
        parts["render"]()
        _settle(root, parts)

        lines = int(parts["output"].index("end-1c").split(".")[0])
        assert lines > 1000, f"only {lines} lines — the result never arrived"
        assert "200 statements, 200 formatted" in parts["status"].cget("text")
    finally:
        root.destroy()


def test_a_superseded_result_is_dropped():
    """Type again while a big format is in flight and the stale answer must not
    land on top of the newer one."""
    from sqlalign.gui import SAMPLES, build

    root = _root()
    try:
        parts = build(root, "postgres")
        root.update()
        parts["source"].delete("1.0", "end")
        parts["source"].insert("1.0", SAMPLES["postgres"] * 200)
        parts["render"]()                       # in flight

        parts["source"].delete("1.0", "end")    # superseded before it lands
        parts["source"].insert("1.0", "select a from t;")
        parts["render"]()
        _settle(root, parts)

        assert parts["output"].get("1.0", "end-1c").strip() == "SELECT a\nFROM t;"
    finally:
        root.destroy()


def test_small_edits_stay_synchronous():
    """Below the threshold the round trip costs more than the work, and staying
    synchronous is what keeps the rest of these tests deterministic."""
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        parts["source"].delete("1.0", "end")
        parts["source"].insert("1.0", "select a from t;")
        parts["render"]()
        # No settling, no update() — it must already be there.
        assert parts["output"].get("1.0", "end-1c").strip() == "SELECT a\nFROM t;"
    finally:
        root.destroy()


# ---- file actions fail visibly, not silently ----------------------------

def test_a_non_utf8_sql_file_still_opens(tmp_path):
    """SQL dumps are not always UTF-8 — latin-1 is common in older exports. This
    used to raise into Tk's handler: a traceback on stderr that nobody sees, and
    a window that appeared to ignore the click."""
    from sqlalign.gui import build

    path = tmp_path / "latin1.sql"
    path.write_bytes("select 'caf\xe9' as name from t;\n".encode("latin-1"))

    root = _root()
    try:
        parts = build(root, "postgres")
        assert parts["actions"]["read_text"](path).strip() == "select 'café' as name from t;"
    finally:
        root.destroy()


def test_a_utf8_file_is_never_mangled_by_the_fallback(tmp_path):
    """Decode strictly first — falling straight to latin-1 would turn every
    multi-byte character into mojibake."""
    from sqlalign.gui import build

    path = tmp_path / "utf8.sql"
    path.write_text("select 'café — naïve' as s;\n", encoding="utf-8")

    root = _root()
    try:
        parts = build(root, "postgres")
        assert "café — naïve" in parts["actions"]["read_text"](path)
    finally:
        root.destroy()


def test_a_failed_config_load_leaves_the_panel_untouched(tmp_path):
    """Half-applying a broken config would leave the controls describing a style
    that is not in effect."""
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        before = dict(parts["values"])
        before_output = parts["output"].get("1.0", "end-1c")

        from sqlalign import configfile
        bad = tmp_path / ".sqlalign.toml"
        bad.write_text('comma_position = "sideways"\n')
        with pytest.raises(configfile.ConfigError):
            configfile.build_style(configfile.load_settings(bad)[0])

        assert dict(parts["values"]) == before
        assert parts["output"].get("1.0", "end-1c") == before_output
    finally:
        root.destroy()


# ---- the actions are always reachable ------------------------------------

@pytest.mark.parametrize("size", [(1180, 760), (1000, 600), (900, 500)])
def test_the_action_buttons_never_need_scrolling(size):
    """They started inside the scrolling settings column, which put every button
    below the fold at any normal window height — the primary actions were the
    hardest things in the window to reach. Only a screenshot showed it."""
    from tkinter import ttk

    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        root.geometry("{}x{}".format(*size))
        root.update_idletasks()
        root.update()

        height = root.winfo_height()
        for button in _find(root, ttk.Button):
            top = button.winfo_rooty() - root.winfo_rooty()
            assert top + button.winfo_height() <= height, (
                f"{button.cget('text')!r} is off-screen at {size}")
            assert not str(button).startswith(str(parts["panel"])), (
                f"{button.cget('text')!r} is inside the scrolling area")
    finally:
        root.destroy()


def test_both_panes_have_matching_chrome():
    """Tk gives an editable Text a heavy focus border and a disabled one none,
    so two widgets that should read as siblings looked like different kinds of
    thing."""
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        for attribute in ("relief", "borderwidth", "highlightthickness"):
            assert str(parts["source"].cget(attribute)) == str(parts["output"].cget(attribute)), (
                f"panes differ on {attribute}")
    finally:
        root.destroy()


# ---- controls that do nothing look like it ------------------------------

def _states(root, cls):
    return [str(w.cget("state")) for w in _find(root, cls)]


def test_a_setting_that_does_nothing_is_greyed_out():
    """`…indent` means nothing while the SELECT list is inline, and `…river
    gutter` means nothing while the clause keywords are flush left. Leaving them
    live is a lie about what the setting does — you can change the number and
    watch nothing happen."""
    from tkinter import ttk

    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        root.update()
        assert parts["widgets"]["select_placement"].get() == "inline"
        assert parts["widgets"]["clause_keyword_align"].get() == "left"
        assert _states(root, ttk.Spinbox)[:2] == ["disabled", "disabled"], (
            "the indent and gutter should start greyed out")

        parts["widgets"]["select_placement"].set("own_line")
        root.update()
        assert _states(root, ttk.Spinbox)[0] == "normal", "indent stayed greyed"
        assert _states(root, ttk.Spinbox)[1] == "disabled", "gutter should still be greyed"

        parts["widgets"]["clause_keyword_align"].set("river")
        root.update()
        assert _states(root, ttk.Spinbox)[1] == "normal", "gutter stayed greyed"
    finally:
        root.destroy()


def test_the_target_checkboxes_grey_out_with_alignment_off():
    from tkinter import ttk

    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        root.update()
        targets = list(parts["widgets"]["align_targets"])
        assert targets

        parts["widgets"]["align"].set(False)
        root.update()
        assert "disabled" in _states(root, ttk.Checkbutton)

        parts["widgets"]["align"].set(True)
        root.update()
        assert _states(root, ttk.Checkbutton).count("disabled") == 0
    finally:
        root.destroy()


def test_a_preset_updates_the_greying_too():
    """Picking a preset changes the dependencies, so the enabled states have to
    follow — otherwise river's gutter stays greyed while it is in use."""
    from tkinter import ttk

    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        root.update()
        assert _states(root, ttk.Spinbox)[1] == "disabled"

        parts["widgets"]["clause_keyword_align"].set("river")   # what `river` sets
        root.update()
        assert _states(root, ttk.Spinbox)[1] == "normal"
    finally:
        root.destroy()


# ---- the preset box tells the truth --------------------------------------

def test_preset_named_identifies_every_preset():
    from sqlalign.gui import CUSTOM, preset_named

    for name in sorted(PRESETS):
        assert preset_named(settings_from(preset_style(name))) == name
    assert preset_named(default_settings()) == "house"

    modified = default_settings()
    modified["keyword_case"] = "lower"
    assert preset_named(modified) == CUSTOM


def test_preset_named_compares_output_not_style_equality():
    """The panel offers only the leaf align targets, so a preset built from the
    `aliases` union comes back as its two children — a different frozenset that
    formats identically. Comparing Style objects would call every preset custom."""
    from sqlalign.gui import preset_named, style_from

    restored = style_from(settings_from(preset_style("house")))
    assert restored != preset_style("house"), "the premise changed; simplify preset_named"
    assert preset_named(settings_from(preset_style("house"))) == "house"


def _preset_box(parts):
    """The preset control, from `build`'s own return value.

    This used to scan the tree for a Combobox whose values contained `house` --
    a guess that would silently pick the wrong widget the day a second combobox
    offered that word, and would have kept passing while testing nothing.
    """
    return parts["preset"]


def test_the_preset_box_goes_custom_when_a_setting_changes():
    """It used to keep saying `house` after you had changed three things —
    claiming a style that was not in effect, the same lie as leaving a dead
    control enabled."""
    from sqlalign.gui import CUSTOM, build

    root = _root()
    try:
        parts = build(root, "postgres")
        box = _preset_box(parts)
        assert box.get() == "house"

        parts["widgets"]["keyword_case"].set("lower")
        root.update()
        assert box.get() == CUSTOM
    finally:
        root.destroy()


def test_it_recovers_when_the_change_is_undone():
    """Sticking on `(custom)` forever would be its own small lie."""
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        box = _preset_box(parts)
        parts["widgets"]["keyword_case"].set("lower")
        root.update()
        parts["widgets"]["keyword_case"].set("upper")
        root.update()
        assert box.get() == "house"
    finally:
        root.destroy()


def test_picking_a_preset_still_applies_it():
    """The resync must not swallow a genuine pick."""
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        _preset_box(parts).set("river")
        root.update()
        assert parts["widgets"]["clause_keyword_align"].get() == "river"
        assert _preset_box(parts).get() == "river"
    finally:
        root.destroy()


def test_choosing_custom_from_the_list_changes_nothing():
    """It is a readout, not a style — selecting it should be inert rather than
    resetting anything."""
    from sqlalign.gui import CUSTOM, build

    root = _root()
    try:
        parts = build(root, "postgres")
        before = dict(parts["values"])
        _preset_box(parts).set(CUSTOM)
        root.update()
        assert dict(parts["values"]) == before
    finally:
        root.destroy()


def test_the_exported_preset_handle_drives_the_on_screen_widget():
    """Exporting a variable that no widget reads would make every preset test
    above green while the box on screen did nothing."""
    from tkinter import ttk

    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        boxes = [w for w in _find(root, ttk.Combobox)
                 if w.cget("textvariable") == str(parts["preset"])]
        assert len(boxes) == 1, "the preset variable is not wired to exactly one box"
        parts["preset"].set("river")
        root.update()
        assert boxes[0].get() == "river"
    finally:
        root.destroy()


# ---- the settings column is scrollable, and the scroll actually reaches ----

def test_scrolling_brings_the_last_setting_into_view():
    """The probe this replaces measured REQUESTED sizes and called the window
    clean while five buttons sat below the fold. Requested size is what a widget
    wants; this asks where the last control actually landed on screen, and
    whether scrolling to the end puts it inside the visible canvas.
    """
    from sqlalign.gui import CONTROLS, build

    root = _root(mapped=True)
    try:
        root.geometry("1180x620+0+0")
        parts = build(root, "postgres")
        root.update_idletasks()
        root.update()
        tray, panel = parts["tray"], parts["panel"]
        assert tray.winfo_height() < panel.winfo_reqheight(), (
            "the panel already fits at this size; the test would prove nothing")

        # The widget for the last control, found through the panel rather than
        # by name -- what matters is the thing a user has to reach and click.
        last = _labelled(panel, CONTROLS[-1]["label"])
        assert last.winfo_rooty() > tray.winfo_rooty() + tray.winfo_height(), (
            "the last setting is already visible; nothing left to scroll to")

        tray.yview_moveto(1.0)
        root.update_idletasks()
        root.update()

        assert tray.yview()[1] == pytest.approx(1.0), "scrolled to the end, not at it"
        top = tray.winfo_rooty()
        assert top <= last.winfo_rooty(), "the last setting sits above the viewport"
        assert last.winfo_rooty() + last.winfo_height() <= top + tray.winfo_height(), (
            "scrolled all the way down and the last setting is still cut off")
    finally:
        root.destroy()


def _labelled(parent, text):
    """The control sitting on the row whose label is `text`."""
    from tkinter import ttk
    row = next(w for w in _find(parent, (ttk.Label, ttk.Checkbutton))
               if w.cget("text") == text)
    if isinstance(row, ttk.Checkbutton):
        return row
    return next(w for w in _find(row.master, (ttk.Combobox, ttk.Spinbox, ttk.Entry))
                if w.winfo_rooty() == row.winfo_rooty())


# ---- the window says which file it is holding ----------------------------

def test_the_title_names_the_open_file():
    """It read `sqlalign` forever, so a window with a file open looked exactly
    like one holding the built-in sample."""
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        assert root.title() == "sqlalign"
        parts["state"]["path"] = "/tmp/models/orders.sql"
        parts["retitle"]()
        assert root.title() == "sqlalign — orders.sql"
        parts["state"]["path"] = None
        parts["retitle"]()
        assert root.title() == "sqlalign", "closing the document must clear the name"
    finally:
        root.destroy()


def test_opening_a_file_records_it_and_retitles(tmp_path, monkeypatch):
    from tkinter import filedialog

    from sqlalign.gui import build

    sql = tmp_path / "orders.sql"
    sql.write_text("select a   from t;\n")

    root = _root()
    try:
        parts = build(root, "postgres")
        monkeypatch.setattr(filedialog, "askopenfilename", lambda **_: str(sql))
        parts["actions"]["open"]()
        assert parts["state"]["path"] == str(sql)
        assert root.title() == "sqlalign — orders.sql"
        assert parts["output"].get("1.0", "end-1c") == "SELECT a\nFROM t;\n"
    finally:
        root.destroy()


def test_save_defaults_to_the_file_that_was_opened(tmp_path, monkeypatch):
    """Formatting a file in place is what the CLI does and what the button is
    nearly always for; it used to open a blank dialog and make you navigate
    back to the file you had just opened."""
    from tkinter import filedialog

    from sqlalign.gui import build

    sql = tmp_path / "orders.sql"
    sql.write_text("select a   from t;\n")

    root = _root()
    try:
        parts = build(root, "postgres")
        monkeypatch.setattr(filedialog, "askopenfilename", lambda **_: str(sql))
        parts["actions"]["open"]()

        seen = {}
        monkeypatch.setattr(filedialog, "asksaveasfilename",
                            lambda **kw: (seen.update(kw), str(sql))[1])
        parts["actions"]["save"]()
        assert seen["initialfile"] == "orders.sql"
        assert seen["initialdir"] == str(tmp_path)
        assert sql.read_text() == "SELECT a\nFROM t;\n", "in place, once confirmed"
        # Byte-for-byte what `sqlalign <file>` writes -- both preserve whether
        # the input ended in a newline, so the two paths cannot disagree.
        again = tmp_path / "cli.sql"
        again.write_text("select a   from t;\n")
        from sqlalign.cli import main as cli
        cli([str(again)])
        assert again.read_text() == sql.read_text()
    finally:
        root.destroy()


def test_save_offers_no_default_when_nothing_was_opened():
    """`initialdir` pointing at wherever the process happens to be is worse than
    letting the dialog use its own last-used location."""
    from tkinter import filedialog

    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        seen = {}
        import pytest as _pytest
        monkeypatch = _pytest.MonkeyPatch()
        monkeypatch.setattr(filedialog, "asksaveasfilename",
                            lambda **kw: (seen.update(kw), "")[1])
        parts["actions"]["save"]()
        monkeypatch.undo()
        assert "initialfile" not in seen and "initialdir" not in seen
    finally:
        root.destroy()


def test_opening_a_file_resets_undo():
    """One Cmd+Z must not resurrect the previous file's text."""
    from sqlalign.gui import build

    root = _root()
    try:
        parts = build(root, "postgres")
        src = parts["source"]
        src.insert("end", "\n-- an edit to the sample")
        src.edit_separator()
        src.delete("1.0", "end")
        src.insert("1.0", "select b from u;")
        src.edit_reset()
        import tkinter as tk
        with pytest.raises(tk.TclError, match="nothing to undo"):
            src.edit_undo()
    finally:
        root.destroy()


# ---- closing the window must not be blocked by work in flight ------------

def test_the_format_worker_is_a_daemon():
    """It was a ThreadPoolExecutor, and `shutdown(wait=False)` does not cancel
    RUNNING work — the interpreter joins the pool's threads at exit regardless.
    Closing the window part-way through a large file left the app alive with no
    window for as long as the format took: measured at 6.4s against 1.3s now.
    """
    import threading

    from sqlalign.gui import build

    root = _root()
    try:
        build(root, "postgres")
        workers = [t for t in threading.enumerate() if t.name == "sqlalign-fmt"]
        assert workers, "no format worker running"
        assert all(t.daemon for t in workers), "a live format would hold the app open"
    finally:
        root.destroy()


def test_superseded_requests_are_dropped_not_formatted(monkeypatch):
    """Typing on a large file used to queue a format per keystroke burst and run
    every one to completion, discarding all but the last result.

    This has to COUNT the formats. Asserting that the pane ends up showing the
    latest text passes either way — the pump already drops stale results at
    display time — so the obvious version of this test was green against a
    worker with the skip removed, which is how it was first written.
    """
    from sqlalign import gui
    from sqlalign.gui import _ASYNC_CHARS, SAMPLES, build

    calls = []
    real = gui.preview
    monkeypatch.setattr(gui, "preview",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])

    root = _root()
    try:
        parts = build(root, "postgres")
        big = (SAMPLES["postgres"].strip() + "\n") * 40
        assert len(big) > _ASYNC_CHARS, "below the threshold this never leaves the thread"

        src = parts["source"]
        for i in range(5):                      # five renders before any finishes
            src.delete("1.0", "end")
            src.insert("1.0", big + f"select {i} as n;\n")
            parts["render"]()

        formatted = _drain(root, parts)
        assert formatted.rstrip().endswith("SELECT 4 AS n;"), "showed a stale result"
        assert len(calls) < 5, f"formatted every superseded request: {len(calls)} of 5"
    finally:
        root.destroy()


def _drain(root, parts, timeout=60.0):
    """Pump Tk until the background format settles, then read the output pane."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if "formatting" not in parts["status"].cget("text"):
            return parts["output"].get("1.0", "end-1c")
        time.sleep(0.02)
    raise AssertionError(f"never settled: {parts['status'].cget('text')!r}")


def test_the_guide_does_not_claim_more_knobs_than_exist():
    """It said nineteen while `Style` had eighteen. A count written out in prose
    drifts the first time a field is added or removed, and nothing catches it."""
    import dataclasses
    import pathlib
    import re

    from sqlalign.style import Style

    words = {18: "Eighteen", 19: "Nineteen", 20: "Twenty"}
    guide = pathlib.Path(__file__).resolve().parent.parent / "docs/guide/getting-started.md"
    claimed = re.search(r"(\w+) knobs is more than", guide.read_text())
    assert claimed, "the sentence this pins was reworded; update or drop this test"
    expected = words[len(dataclasses.fields(Style))]
    assert claimed.group(1) == expected, (
        f"the guide says {claimed.group(1).lower()} knobs; Style has "
        f"{len(dataclasses.fields(Style))}")


# ---- why a control is greyed out -------------------------------------------
#
# A greyed control with no explanation reads as a bug in the panel. The setting
# is real and will do something -- just not until the control it depends on says
# so, which is a sentence the panel can say.

@pytest.mark.parametrize("name,off,expected", [
    ("align_targets", {"align": False}, "needs align = true"),
    ("select_indent", {"select_placement": "inline"}, "needs select_placement = own_line"),
    ("river_gutter", {"clause_keyword_align": "left"}, "needs clause_keyword_align = river"),
])
def test_a_greyed_control_says_why(name, off, expected):
    values = dict(default_settings(), **off)
    assert disabled_reason(name, values) == expected


@pytest.mark.parametrize("name,on", [
    ("align_targets", {"align": True}),
    ("select_indent", {"select_placement": "own_line"}),
    ("river_gutter", {"clause_keyword_align": "river"}),
])
def test_no_reason_is_given_when_the_control_is_live(name, on):
    """Two of these are greyed out under the HOUSE defaults, which is correct --
    the defaults put the select list inline and the keywords left. The dependency
    has to be satisfied explicitly."""
    assert disabled_reason(name, dict(default_settings(), **on)) is None


def test_a_control_with_no_dependency_never_has_a_reason():
    assert disabled_reason("keyword_case", default_settings()) is None


def test_an_unknown_control_has_no_reason():
    assert disabled_reason("no_such_setting", default_settings()) is None


def test_every_dependent_control_can_produce_a_reason():
    """Whatever `needs` a control declares, the panel must be able to explain it.
    A new dependency added without a spelling would grey a control silently."""
    from sqlalign.gui import CONTROLS

    for control in CONTROLS:
        need = control.get("needs")
        if need is None:
            continue
        depends_on, wanted = need
        unmet = "__not_this__" if not isinstance(wanted, bool) else not wanted
        reason = disabled_reason(control["name"], dict(default_settings(),
                                                       **{depends_on: unmet}))
        assert reason and depends_on in reason, control["name"]


# ---- the header on a saved config ------------------------------------------

def test_the_saved_config_says_where_it_came_from():
    """It lands in a repository where the next person to open it chose none of
    it, and an unexplained file of eighteen settings invites deletion."""
    text = as_toml(default_settings())
    assert "settings panel" in text
    assert "sqlalign --init" in text, "the alternative is worth naming"


def test_the_saved_config_names_the_preset_when_there_is_one():
    assert "`gitlab` preset" in as_toml(settings_from(preset_style("gitlab")))


def test_a_customised_config_claims_no_preset():
    """Width is the case that matters: `preset_named` compares formatted output
    of one short sample, which a changed width does not alter, so it would still
    have called this the gitlab preset. The header uses an exact comparison."""
    values = dict(settings_from(preset_style("gitlab")), width=71)
    assert "preset's values" not in as_toml(values)


def test_the_dialect_is_recorded_as_a_comment_not_a_setting():
    """`dialect` is CLI-only and has no config key: writing it as a setting
    would produce a file that fails to load."""
    import tomllib

    text = as_toml(default_settings(), dialect="tsql")
    assert "--dialect tsql" in text
    assert "dialect" not in tomllib.loads(text)


@pytest.mark.parametrize("preset", ["house", "compact", "dbt", "gitlab", "river", "trailing"])
def test_every_saved_config_parses(preset):
    import tomllib

    tomllib.loads(as_toml(settings_from(preset_style(preset)), dialect="postgres"))
