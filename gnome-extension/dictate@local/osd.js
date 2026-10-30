// Bottom-center dictation popup, drawn by GNOME Shell itself (like the volume OSD).
// Driven by the same text commands as osd.py:
//   listening [mic] | level <0..1> | transcribing | done <text> | empty | error <title|hint> | hide
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import St from 'gi://St';

import * as Animation from 'resource:///org/gnome/shell/ui/animation.js';
import * as Layout from 'resource:///org/gnome/shell/ui/layout.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BARS = 40;
// The top-bar icon's rainbow, spread across the waveform
const RAINBOW = [[255, 107, 107], [255, 184, 77], [76, 217, 100], [90, 200, 250], [191, 122, 240]];
const LINE_CHARS = 46; // what fits on one line of the 290 px text column

/** Break text into at most `max` lines at word boundaries (explicit breaks measure reliably in St). */
function wrapLines(text, max = 2) {
    const lines = [''];
    for (const word of text.split(/\s+/).filter(w => w)) {
        const last = lines.length - 1;
        if (!lines[last] || (lines[last] + ' ' + word).length <= LINE_CHARS) {
            lines[last] = lines[last] ? `${lines[last]} ${word}` : word;
        } else if (lines.length < max) {
            lines.push(word);
        } else {
            lines[last] = `${lines[last].slice(0, LINE_CHARS - 1).trimEnd()}…`;
            break;
        }
    }
    return lines.join('\n');
}

function rainbowAt(t) {
    const pos = t * (RAINBOW.length - 1);
    const i = Math.min(Math.floor(pos), RAINBOW.length - 2);
    const f = pos - i;
    const [a, b] = [RAINBOW[i], RAINBOW[i + 1]];
    const c = a.map((v, k) => Math.round(v + (b[k] - v) * f));
    return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

export const DictateOsd = GObject.registerClass(
class DictateOsd extends Clutter.Actor {
    _init() {
        super._init({
            x_expand: true,
            y_expand: true,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.END,
            visible: false,
            opacity: 0,
            layout_manager: new Clutter.BinLayout(),
        });
        this.add_constraint(new Layout.MonitorConstraint({primary: true, work_area: true}));

        const card = new St.BoxLayout({style_class: 'dictate-osd'});
        this.add_child(card);

        // Coloured badge: icon, or a spinner while transcribing
        this._badge = new St.Bin({style_class: 'dictate-osd-badge', y_align: Clutter.ActorAlign.CENTER});
        this._icon = new St.Icon({style_class: 'dictate-osd-icon', x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER});
        this._spinner = new Animation.Spinner(20);
        this._badgeBox = new St.Widget({layout_manager: new Clutter.BinLayout(), x_expand: true, y_expand: true});
        this._badgeBox.add_child(this._icon);
        this._badgeBox.add_child(this._spinner);
        this._spinner.set({x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER});
        this._badge.set_child(this._badgeBox);
        card.add_child(this._badge);

        const right = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true,
            y_align: Clutter.ActorAlign.CENTER, style_class: 'dictate-osd-text'});
        card.add_child(right);

        const top = new St.BoxLayout({style_class: 'dictate-osd-top'});
        this._title = new St.Label({style_class: 'dictate-osd-title', y_align: Clutter.ActorAlign.CENTER});
        this._mic = new St.Label({style_class: 'dictate-osd-mic', x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        this._timer = new St.Label({style_class: 'dictate-osd-timer', y_align: Clutter.ActorAlign.CENTER});
        top.add_child(this._title);
        top.add_child(this._mic);
        top.add_child(this._timer);
        right.add_child(top);

        this._wave = new St.BoxLayout({style_class: 'dictate-osd-wave'});
        this._bars = [];
        for (let i = 0; i < BARS; i++) {
            const bar = new St.Widget({
                style_class: 'dictate-osd-bar',
                style: `background-color: ${rainbowAt(i / (BARS - 1))};`,
                y_align: Clutter.ActorAlign.CENTER,
            });
            this._wave.add_child(bar);
            this._bars.push(bar);
        }
        right.add_child(this._wave);

        this._sub = new St.Label({style_class: 'dictate-osd-sub'});
        this._sub.clutter_text.set({ellipsize: Pango.EllipsizeMode.NONE, use_markup: true});
        right.add_child(this._sub);

        this._mode = 'hide';
        this._levels = new Array(BARS).fill(0);
        this._target = 0;
        this._tickId = 0;
        this._hideId = 0;
        Main.uiGroup.add_child(this);
    }

    // --- states -----------------------------------------------------------------------
    command(line) {
        const [cmd, ...rest] = line.trim().split(' ');
        const arg = rest.join(' ');
        switch (cmd) {
        case 'level':
            this._target = Math.max(this._target, Math.min(1, parseFloat(arg) || 0));
            break;
        case 'listening':
            this._show('listening', 'Listening', {icon: 'audio-input-microphone-symbolic', mic: arg,
                sub: `Press ${this._key('Insert')} or ${this._key('Enter')} to finish`});
            break;
        case 'transcribing':
            this._show('transcribing', 'Transcribing', {sub: 'Turning your words into text, on this device'});
            break;
        case 'done': {
            this._show('done', 'Typed', {icon: 'object-select-symbolic', sub: GLib.markup_escape_text(wrapLines(arg), -1),
                autohide: 2200});
            break;
        }
        case 'copied':
            this._show('copied', 'Copied to clipboard', {icon: 'edit-copy-symbolic',
                sub: GLib.markup_escape_text(wrapLines(arg), -1), autohide: 1800});
            break;
        case 'nofield':
            this._show('copied', 'No text field was active', {icon: 'edit-copy-symbolic',
                sub: `It's on your clipboard. Press ${this._key('Ctrl+V')} where you want it`, autohide: 5000});
            break;
        case 'empty':
            this._show('empty', "Didn't catch that", {icon: 'microphone-sensitivity-muted-symbolic',
                sub: 'Try speaking a little closer to the mic', autohide: 1800});
            break;
        case 'error': {
            const [title, hint = ''] = (arg || 'Something went wrong').split('|');
            this._show('error', title, {icon: 'dialog-warning-symbolic', sub: GLib.markup_escape_text(wrapLines(hint), -1),
                autohide: 4000});
            break;
        }
        case 'hide':
            this._hide();
            break;
        }
    }

    _key(name) {
        return `<span background="#ffffff26" foreground="#f5f5f7" weight="bold"> ${name} </span>`;
    }

    _show(mode, title, {icon = null, mic = '', sub = '', autohide = 0}) {
        if (this._mode !== mode)
            this._badge.remove_style_class_name(this._mode);
        this._badge.add_style_class_name(mode);
        this._mode = mode;

        this._icon.visible = !!icon;
        if (icon)
            this._icon.icon_name = icon;
        if (mode === 'transcribing')
            this._spinner.play();
        else
            this._spinner.stop();

        this._title.text = title;
        this._mic.text = mic ? `  ·  ${mic}` : '';
        this._timer.visible = this._wave.visible = mode === 'listening';
        this._sub.visible = !!sub;
        // Clutter keeps the previous markup's highlight ranges (e.g. the Insert/Enter key chips) when new
        // text has no tags of its own, painting them over the wrong letters: reset, and always wrap in a span
        this._sub.clutter_text.set_text('');
        this._sub.clutter_text.set_markup(`<span>${sub}</span>`);
        this._sub.set_style_class_name(mode === 'done' ? 'dictate-osd-preview' : 'dictate-osd-sub');

        if (mode === 'listening')
            this._startListening();
        else
            this._stopTicking();

        if (this._hideId) {
            GLib.source_remove(this._hideId);
            this._hideId = 0;
        }
        if (autohide) {
            this._hideId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, autohide, () => {
                this._hideId = 0;
                this._hide();
                return GLib.SOURCE_REMOVE;
            });
        }

        Main.uiGroup.set_child_above_sibling(this, null);
        if (!this.visible) {
            this.set({visible: true, opacity: 0, translation_y: 12});
            this.ease({opacity: 255, translation_y: 0, duration: 200, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        }
    }

    _hide() {
        this._stopTicking();
        this._spinner.stop();
        this._mode = 'hide';
        this.ease({
            opacity: 0,
            translation_y: 8,
            duration: 220,
            mode: Clutter.AnimationMode.EASE_IN_QUAD,
            onComplete: () => this.hide(),
        });
    }

    // --- waveform and timer ----------------------------------------------------------
    _startListening() {
        this._stopTicking();
        this._levels.fill(0);
        this._target = 0;
        this._started = GLib.get_monotonic_time();
        this._timer.text = '0:00';
        this._drawBars();
        this._tickId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 40, () => {
            this._levels.shift();
            this._levels.push(this._target);
            this._target *= 0.82; // decay between level updates
            this._drawBars();
            const secs = Math.floor((GLib.get_monotonic_time() - this._started) / 1e6);
            this._timer.text = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
            return GLib.SOURCE_CONTINUE;
        });
    }

    _drawBars() {
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        this._bars.forEach((bar, i) => {
            const lv = this._levels[i];
            bar.height = Math.round((3 + lv * 19) * scale);
            bar.opacity = lv < 0.03 ? 90 : 255;
        });
    }

    _stopTicking() {
        if (this._tickId) {
            GLib.source_remove(this._tickId);
            this._tickId = 0;
        }
    }

    destroy() {
        this._stopTicking();
        if (this._hideId)
            GLib.source_remove(this._hideId);
        super.destroy();
    }
});
