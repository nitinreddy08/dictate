"""The rainbow tray icon and the bottom-center popup (Qt). Same look and states as the GNOME version:
  listening [mic] | level <0..1> | transcribing | done <text> | nofield <text> | copied <text> |
  empty | error <title|hint> | hide
"""
import html
import math
import time

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLayout,
                               QSizePolicy, QVBoxLayout, QWidget)

RAINBOW = [(255, 107, 107), (255, 184, 77), (76, 217, 100), (90, 200, 250), (191, 122, 240)]
GOLD = QColor(255, 214, 10)
FONTS = ["Segoe UI Variable Text", "Segoe UI", "Inter", "Ubuntu", "Cantarell", "Sans Serif"]
LINE_CHARS = 46  # what fits on one line of the text column


def font(size, bold=False):
    f = QFont()
    f.setFamilies(FONTS)
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


def rainbow_at(t):
    pos = t * (len(RAINBOW) - 1)
    i = min(int(pos), len(RAINBOW) - 2)
    f = pos - i
    a, b = RAINBOW[i], RAINBOW[i + 1]
    return QColor(*(round(x + (y - x) * f) for x, y in zip(a, b)))


def wrap_lines(text, max_lines=2):
    """Break text into at most `max_lines` lines at word boundaries, ending in … if it doesn't fit."""
    lines = [""]
    for word in text.split():
        if not lines[-1] or len(lines[-1]) + 1 + len(word) <= LINE_CHARS:
            lines[-1] = f"{lines[-1]} {word}".strip()
        elif len(lines) < max_lines:
            lines.append(word)
        else:
            lines[-1] = lines[-1][:LINE_CHARS - 1].rstrip() + "…"
            break
    return "\n".join(lines)


# --- the rainbow sparkle icon ------------------------------------------------------------------

# Four rainbow sound bars and a gold twinkle, on a 16×16 grid (same design as the GNOME top-bar icon)
BARS = [((255, 107, 107), 0.6, 7, 5.5), ((255, 184, 77), 4, 4.5, 10.5),
        ((76, 217, 100), 7.4, 6.5, 8), ((191, 122, 240), 10.8, 9, 4)]


def star(cx, cy, r):
    """A four-point twinkle."""
    path = QPainterPath()
    k = r * 0.3
    pts = [(0, -r), (k, -k), (r, 0), (k, k), (0, r), (-k, k), (-r, 0), (-k, -k)]
    path.moveTo(cx + pts[0][0], cy + pts[0][1])
    for x, y in pts[1:]:
        path.lineTo(cx + x, cy + y)
    path.closeSubpath()
    return path


def render_icon(size=32, state="idle", t=0.0):
    """The tray icon as a QPixmap. While listening the bars bounce and the sparkle twinkles; while
    transcribing only the sparkle twinkles. `t` is the animation time in seconds."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 16
    for i, (rgb, x, y, h) in enumerate(BARS):
        scale = 1.0
        if state == "listening":
            scale = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(t * 7 - i * 1.1))
        cy, hh = y + h / 2, h * scale
        p.setBrush(QColor(*rgb))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(x * s, (cy - hh / 2) * s, 2.2 * s, hh * s), 1.1 * s, 1.1 * s)
    tw = 1.0
    if state in ("listening", "transcribing"):
        tw = 0.55 + 0.55 * (0.5 + 0.5 * math.sin(t * 8))
    p.setBrush(GOLD)
    p.drawPath(star(13.2 * s, 3.2 * s, 3 * s * tw))
    p.end()
    return pm


def app_icon():
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(render_icon(size))
    return icon


# --- the popup -------------------------------------------------------------------------------

BADGE_COLORS = {
    "listening": (QColor(255, 69, 58), QColor(255, 69, 58, 41)),
    "transcribing": (QColor(10, 132, 255), QColor(10, 132, 255, 41)),
    "done": (QColor(48, 209, 88), QColor(48, 209, 88, 41)),
    "copied": (QColor(10, 132, 255), QColor(10, 132, 255, 41)),
    "empty": (QColor(161, 161, 166), QColor(255, 255, 255, 20)),
    "error": (QColor(255, 159, 10), QColor(255, 159, 10, 41)),
}


class Badge(QWidget):
    """The coloured circle with a glyph: mic, spinner, check, copy, muted mic or warning."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(42, 42)
        self.mode, self.t0 = "listening", time.monotonic()

    def paintEvent(self, event):
        fg, bg = BADGE_COLORS.get(self.mode, BADGE_COLORS["empty"])
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawEllipse(0, 0, 42, 42)
        pen = QPen(fg, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        c = QPointF(21, 21)
        if self.mode in ("listening", "empty"):
            p.setBrush(fg)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(17.5, 11, 7, 12.5), 3.5, 3.5)  # mic capsule
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(14, 13, 14, 14.5), 200 * 16, 140 * 16)  # stand
            p.drawLine(QPointF(21, 27.5), QPointF(21, 31))
            if self.mode == "empty":
                p.drawLine(QPointF(13.5, 12), QPointF(28.5, 30))
        elif self.mode == "transcribing":
            angle = ((time.monotonic() - self.t0) * 360 * 1.2) % 360
            p.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 60), 2.4))
            p.drawEllipse(c, 9, 9)
            p.setPen(QPen(fg, 2.4, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(12, 12, 18, 18), int(-angle * 16), 100 * 16)
        elif self.mode == "done":
            path = QPainterPath(QPointF(14.5, 21.5))
            path.lineTo(19, 26)
            path.lineTo(28, 16)
            p.drawPath(path)
        elif self.mode == "copied":
            p.drawRoundedRect(QRectF(17, 15, 11, 13), 2, 2)
            p.drawPolyline([QPointF(14, 24.5), QPointF(14, 12.5), QPointF(23.5, 12.5)])
        elif self.mode == "error":
            path = QPainterPath(QPointF(21, 12))
            path.lineTo(30, 29)
            path.lineTo(12, 29)
            path.closeSubpath()
            p.drawPath(path)
            p.drawLine(QPointF(21, 18), QPointF(21, 23))
            p.drawPoint(QPointF(21, 26))
        p.end()


class Wave(QWidget):
    """The rainbow waveform: 40 bars that scroll with your voice level."""

    BARS = 40

    def __init__(self):
        super().__init__()
        self.setFixedHeight(22)
        self.levels = [0.0] * self.BARS

    def push(self, level):
        self.levels = self.levels[1:] + [level]
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        step = self.width() / self.BARS
        for i, lv in enumerate(self.levels):
            c = rainbow_at(i / (self.BARS - 1))
            c.setAlpha(255 if lv >= 0.03 else 90)
            h = 3 + lv * 19
            p.setBrush(c)
            p.drawRoundedRect(QRectF(i * step, (22 - h) / 2, 4, h), 2, 2)
        p.end()


def key_chip(name):
    return (f'<span style="background-color:#3a3a40; color:#f5f5f7; font-weight:600;">'
            f'&nbsp;{html.escape(name)}&nbsp;</span>')


class Popup(QWidget):
    """Bottom-center card above the taskbar. Never takes focus and lets clicks through, so whatever you
    were typing into keeps the keyboard."""

    MARGIN = 20  # transparent room around the card for its shadow

    def __init__(self, hotkey="Insert"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.hotkey = hotkey

        outer = QVBoxLayout(self)
        outer.setSizeConstraint(QLayout.SetFixedSize)  # the window always hugs its content, shrinking too
        outer.setContentsMargins(self.MARGIN, self.MARGIN, self.MARGIN, self.MARGIN)
        self.card = QFrame(objectName="card")
        self.card.setStyleSheet("#card { background-color: rgba(30, 30, 34, 247); border-radius: 22px;"
                                " border: 1px solid rgba(255, 255, 255, 18); }")
        shadow = QGraphicsDropShadowEffect(blurRadius=28, offset=QPoint(0, 6), color=QColor(0, 0, 0, 115))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        row = QHBoxLayout(self.card)
        row.setContentsMargins(14, 14, 20, 14)
        row.setSpacing(14)
        self.badge = Badge()
        row.addWidget(self.badge, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(0)
        self.title = QLabel(font=font(11.5, bold=True), styleSheet="color: #f5f5f7;")
        self.mic = QLabel(font=font(9.5), styleSheet="color: #8e8e93;")
        self.mic.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.timer_label = QLabel(font=font(10), styleSheet="color: #a1a1a6;")
        top.addWidget(self.title)
        top.addWidget(self.mic, 1)
        top.addWidget(self.timer_label)
        col.addLayout(top)
        self.wave = Wave()
        col.addWidget(self.wave)
        self.sub = QLabel(font=font(9.5), textFormat=Qt.RichText, wordWrap=True)
        self.sub.setFixedWidth(290)
        col.addWidget(self.sub)
        row.addLayout(col)
        self.card.setFixedWidth(380)

        self.mode, self.target, self.started = "hide", 0.0, 0.0
        self.tick = QTimer(self, interval=40, timeout=self._tick)
        self.hide_timer = QTimer(self, singleShot=True, timeout=self.hide_popup)
        self.fade = QPropertyAnimation(self, b"windowOpacity", duration=200)
        self.slide = QPropertyAnimation(self, b"pos", duration=200, easingCurve=QEasingCurve.OutQuad)

    def prewarm(self):
        """Draw every state once, off-screen, at start-up. The first time text and shadows are drawn,
        Windows loads fonts and sets up effects, which can freeze the app for seconds; doing it now means
        your first dictation starts instantly and no audio is lost while the popup appears."""
        for cmd in ("listening Microphone", "transcribing", "done Ready", "error Ready|Ready"):
            self.command(cmd)
            self._place()
            self.grab()
        self.hide_timer.stop()
        self.tick.stop()
        self.fade.stop()
        self.slide.stop()
        self.mode = "hide"
        self.hide()

    # --- commands (same protocol as the GNOME popup) ---
    def command(self, line):
        cmd, _, arg = line.strip().partition(" ")
        if cmd == "level":
            try:
                self.target = max(self.target, min(1.0, float(arg)))
            except ValueError:
                pass
        elif cmd == "listening":
            self.show_state("listening", "Listening", mic=arg,
                            sub=f"Press {key_chip(self.hotkey)} or {key_chip('Enter')} to finish")
        elif cmd == "transcribing":
            self.show_state("transcribing", "Transcribing", sub="Turning your words into text, on this device")
        elif cmd == "done":
            self.show_state("done", "Typed", sub=self._text(arg), preview=True, autohide=2200)
        elif cmd == "copied":
            self.show_state("copied", "Copied to clipboard", sub=self._text(arg), preview=True, autohide=1800)
        elif cmd == "nofield":
            self.show_state("copied", "No text box to type into",
                            sub=f"It's on your clipboard. Press {key_chip('Ctrl+V')} where you want it",
                            autohide=5000)
        elif cmd == "empty":
            self.show_state("empty", "Didn't catch that", sub="Try speaking a little closer to the mic",
                            autohide=1800)
        elif cmd == "error":
            title, _, hint = (arg or "Something went wrong").partition("|")
            self.show_state("error", title, sub=self._text(hint), autohide=4000)
        elif cmd == "hide":
            self.hide_popup()

    @staticmethod
    def _text(text):
        return html.escape(wrap_lines(text)).replace("\n", "<br>")

    def show_state(self, mode, title, mic="", sub="", preview=False, autohide=0):
        self.mode = self.badge.mode = mode
        self.title.setText(title)
        self.mic.setText(f"  ·  {mic}" if mic else "")
        listening = mode == "listening"
        self.timer_label.setVisible(listening)
        self.wave.setVisible(listening)
        self.sub.setStyleSheet(f"color: {'#e5e5ea' if preview else '#a1a1a6'};")
        self.sub.setFont(font(10 if preview else 9.5))
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        if listening:
            self.wave.levels = [0.0] * Wave.BARS
            self.target, self.started = 0.0, time.monotonic()
            self.timer_label.setText("0:00")
        self.tick.start()
        self.hide_timer.stop()
        if autohide:
            self.hide_timer.start(autohide)
        self.badge.update()
        self._appear()

    def _place(self):
        # Re-measure for the new state: titles change width, the waveform and timer come and go
        for label in (self.title, self.mic, self.timer_label, self.sub):
            label.updateGeometry()
        self.card.layout().invalidate()
        self.layout().invalidate()
        self.layout().activate()
        area = QGuiApplication.primaryScreen().availableGeometry()
        return QPoint(area.x() + (area.width() - self.width()) // 2,
                      area.y() + area.height() - self.height() - 48 + self.MARGIN)

    def _appear(self):
        end = self._place()
        if not self.isVisible() or self.windowOpacity() < 0.99:
            self.setWindowOpacity(0.0)
            self.move(end + QPoint(0, 12))
            self.show()
            self.fade.stop()
            self.fade.setStartValue(self.windowOpacity())
            self.fade.setEndValue(1.0)
            self.fade.start()
            self.slide.stop()
            self.slide.setStartValue(self.pos())
            self.slide.setEndValue(end)
            self.slide.start()
        else:
            self.move(end)
        self.raise_()

    def hide_popup(self):
        self.mode = "hide"
        self.tick.stop()
        self.fade.stop()
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(0.0)
        self.fade.finished.connect(self._hidden)
        self.fade.start()

    def _hidden(self):
        self.fade.finished.disconnect(self._hidden)
        if self.mode == "hide":
            self.hide()

    def _tick(self):
        if self.mode == "listening":
            self.wave.push(self.target)
            self.target *= 0.82  # decay between level updates
            secs = int(time.monotonic() - self.started)
            self.timer_label.setText(f"{secs // 60}:{secs % 60:02d}")
        elif self.mode == "transcribing":
            self.badge.update()
