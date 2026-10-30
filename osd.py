#!/usr/bin/python3
"""Bottom-center on-screen popup (like GNOME's volume OSD) for dictate.

Runs with the system Python (needs python3-gi / GTK 3). Reads commands on stdin:
  listening | level <0..1> | transcribing | done <text> | empty | error <text> | hide
"""
import os
import sys
import time

os.environ["GDK_BACKEND"] = "x11"  # Wayland apps can't place windows; XWayland popups can

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gdk, Gtk  # noqa: E402

KEY = os.environ.get("DICTATE_KEY_LABEL", "Insert")
BARS = 28
WIDTH = 360
MARGIN = 18  # transparent room around the card for its shadow

CSS = b"""
window { background-color: transparent; }
#card {
  background-color: rgba(32, 32, 36, 0.96);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 22px;
  padding: 12px 18px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
}
#badge { min-width: 38px; min-height: 38px; border-radius: 19px; }
#badge.listening    { background-color: rgba(255, 69, 58, 0.18); }
#badge.transcribing { background-color: rgba(10, 132, 255, 0.18); }
#badge.done         { background-color: rgba(48, 209, 88, 0.18); }
#badge.empty        { background-color: rgba(255, 255, 255, 0.08); }
#badge.error        { background-color: rgba(255, 159, 10, 0.18); }
#badge image { -gtk-icon-style: symbolic; }
#badge.listening image { color: #ff453a; }
#badge.done image      { color: #30d158; }
#badge.empty image     { color: #a1a1a6; }
#badge.error image     { color: #ff9f0a; }
#badge spinner { color: #0a84ff; }
#title { color: #f5f5f7; font-size: 15px; font-weight: 700; }
#timer { color: #a1a1a6; font-size: 13px; font-feature-settings: "tnum"; }
#sub   { color: #a1a1a6; font-size: 12.5px; }
#sub.preview { color: #e5e5ea; font-size: 13px; }
.bar { min-width: 4px; border-radius: 2px; background-color: #ff453a; }
.bar.idle { background-color: rgba(255, 69, 58, 0.35); }
"""


class OSD(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_app_paintable(True)
        self.set_accept_focus(False)
        self.set_keep_above(True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        outer = Gtk.Box(margin=MARGIN)
        card = Gtk.Box(name="card", spacing=14)
        card.set_size_request(WIDTH, -1)
        outer.add(card)
        self.add(outer)

        # Left: coloured icon badge
        self.badge = Gtk.Box(name="badge", valign=Gtk.Align.CENTER)
        self.icon = Gtk.Image()
        self.icon.set_pixel_size(18)
        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(18, 18)
        for w in (self.icon, self.spinner):
            w.set_halign(Gtk.Align.CENTER)
            w.set_valign(Gtk.Align.CENTER)
            self.badge.pack_start(w, True, True, 0)
        card.pack_start(self.badge, False, False, 0)

        # Right: title row, then waveform or subtitle
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, valign=Gtk.Align.CENTER)
        row = Gtk.Box(spacing=8)
        self.title = Gtk.Label(name="title", xalign=0)
        self.timer = Gtk.Label(name="timer", xalign=1)
        row.pack_start(self.title, True, True, 0)
        row.pack_end(self.timer, False, False, 0)
        right.pack_start(row, False, False, 0)

        self.wave = Gtk.Box(spacing=3, valign=Gtk.Align.CENTER)
        self.wave.set_size_request(-1, 20)
        self.bars, self.levels = [], [0.0] * BARS
        for _ in range(BARS):
            b = Gtk.Box(valign=Gtk.Align.CENTER)
            b.get_style_context().add_class("bar")
            self.wave.pack_start(b, True, True, 0)
            self.bars.append(b)
        right.pack_start(self.wave, False, False, 0)

        self.sub = Gtk.Label(name="sub", xalign=0)
        self.sub.set_line_wrap(True)
        self.sub.set_lines(2)
        self.sub.set_ellipsize(3)  # end
        self.sub.set_max_width_chars(40)
        self.sub.set_use_markup(True)
        right.pack_start(self.sub, False, False, 0)
        card.pack_start(right, True, True, 0)
        outer.show_all()

        self.mode, self.started, self.target_level = "hide", 0.0, 0.0
        self.hide_id = None
        self.opacity, self.fade_to = 0.0, 0.0
        GLib.timeout_add(40, self.tick)
        GLib.io_add_watch(sys.stdin, GLib.IO_IN | GLib.IO_HUP, self.on_input)

    # --- rendering -------------------------------------------------------------
    def set_bars(self):
        idle = self.target_level < 0.02
        for b, lv in zip(self.bars, self.levels):
            b.set_size_request(-1, int(3 + lv * 17))
            ctx = b.get_style_context()
            (ctx.add_class if idle else ctx.remove_class)("idle")

    def tick(self):
        if self.mode == "listening":
            self.levels = self.levels[1:] + [self.target_level]
            self.target_level *= 0.85  # decay between level updates
            self.set_bars()
            secs = int(time.time() - self.started)
            self.timer.set_text(f"{secs // 60}:{secs % 60:02d}")
        # fade in/out
        if abs(self.opacity - self.fade_to) > 0.01:
            self.opacity += (self.fade_to - self.opacity) * 0.45
            Gtk.Widget.set_opacity(self, self.opacity)
            if self.fade_to == 0 and self.opacity < 0.03:
                self.opacity = 0
                self.hide()
        return True

    def place(self):
        self.resize(1, 1)
        size = self.get_preferred_size()[1]
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        area = mon.get_workarea()
        self.move(area.x + (area.width - size.width) // 2, area.y + area.height - size.height - 110)
        if os.environ.get("OSD_DEBUG"):
            print(f"osd at {self.get_position()} size {size.width}x{size.height}", file=sys.stderr, flush=True)

    def show_state(self, mode, title, sub="", icon=None, autohide=None, preview=False):
        self.mode = mode
        ctx = self.badge.get_style_context()
        for c in ("listening", "transcribing", "done", "empty", "error"):
            ctx.remove_class(c)
        ctx.add_class(mode)
        self.spinner.set_visible(mode == "transcribing")
        (self.spinner.start if mode == "transcribing" else self.spinner.stop)()
        self.icon.set_visible(icon is not None)
        if icon:
            self.icon.set_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
            self.icon.set_pixel_size(18)
        self.title.set_text(title)
        self.timer.set_visible(mode == "listening")
        self.wave.set_visible(mode == "listening")
        sctx = self.sub.get_style_context()
        (sctx.add_class if preview else sctx.remove_class)("preview")
        self.sub.set_markup(sub)
        self.sub.set_visible(bool(sub))
        if mode == "listening":
            self.started, self.target_level = time.time(), 0.0
            self.levels = [0.0] * BARS
            self.set_bars()
            self.timer.set_text("0:00")
        self.place()
        if not self.get_visible():
            self.opacity = 0
            Gtk.Widget.set_opacity(self, 0)
            self.show()
        self.fade_to = 1.0
        if self.hide_id:
            GLib.source_remove(self.hide_id)
            self.hide_id = None
        if autohide:
            self.hide_id = GLib.timeout_add(autohide, self.hide_osd)

    def hide_osd(self):
        self.hide_id = None
        self.mode = "hide"
        self.spinner.stop()
        self.fade_to = 0.0
        return False

    # --- input -----------------------------------------------------------------
    def on_input(self, src, cond):
        line = sys.stdin.readline()
        if not line:
            Gtk.main_quit()
            return False
        cmd, _, arg = line.strip().partition(" ")
        chip = '<span background="#ffffff22" foreground="#f5f5f7" weight="bold"> {} </span>'
        if cmd == "level":
            try:
                self.target_level = max(self.target_level, min(1.0, float(arg)))
            except ValueError:
                pass
        elif cmd == "listening":
            self.show_state("listening", "Listening", icon="audio-input-microphone-symbolic")
            mic = f"{GLib.markup_escape_text(arg)}  ·  " if arg else ""
            self.sub.set_markup(f"{mic}Press {chip.format(KEY)} or {chip.format('Enter')} to finish")
            self.sub.set_visible(True)
        elif cmd == "transcribing":
            self.show_state("transcribing", "Transcribing", "Converting speech to text on this device")
        elif cmd == "done":
            self.show_state("done", "Typed", GLib.markup_escape_text(arg or ""), "object-select-symbolic",
                            2200, preview=True)
        elif cmd == "empty":
            self.show_state("empty", "Didn't catch that", "Try speaking a little closer to the mic",
                            "microphone-sensitivity-muted-symbolic", 1800)
        elif cmd == "error":
            title, _, hint = (arg or "Something went wrong").partition("|")
            self.show_state("error", title, GLib.markup_escape_text(hint), "dialog-warning-symbolic", 4000)
        elif cmd == "hide":
            self.hide_osd()
        return True


OSD()
Gtk.main()
