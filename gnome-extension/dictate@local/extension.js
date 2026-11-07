import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Graphene from 'gi://Graphene';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {DictateOsd} from './osd.js';

// dictate.socket (systemd) starts the dictation service when something connects here
const SOCKET = GLib.getenv('DICTATE_SOCKET') ?? GLib.build_filenamev([GLib.get_user_runtime_dir(), 'dictate.sock']);
// Written by dictate.py: "listening", "transcribing" or "idle"
const STATE_FILE = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'dictate.state']);
// Backups kept by dictate.py in RAM: recent dictations (JSON lines), and the last failed recording
const HISTORY_FILE = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'dictate-history.jsonl']);
const RECOVERED_FILE = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'dictate.recovered.wav']);

// Apps where paste is Ctrl+Shift+V
const TERMINALS = ['gnome-terminal-server', 'org.gnome.terminal', 'kgx', 'org.gnome.console',
    'org.gnome.ptyxis', 'ptyxis', 'kitty', 'alacritty', 'foot', 'org.wezfurlong.wezterm', 'wezterm',
    'tilix', 'com.gexperts.tilix', 'terminator', 'xterm', 'konsole', 'org.kde.konsole', 'ghostty',
    'com.mitchellh.ghostty', 'blackbox', 'com.raggesilver.blackbox'];

const DBUS_IFACE = `
<node>
  <interface name="org.gnome.Shell.Extensions.Dictate">
    <method name="Ping"/>
    <method name="Osd">
      <arg type="s" direction="in" name="command"/>
    </method>
    <method name="Type">
      <arg type="s" direction="in" name="text"/>
      <arg type="s" direction="out" name="method"/>
    </method>
  </interface>
</node>`;

/** Send "toggle" or "stop" to the dictation service (systemd starts it if needed). */
function sendCommand(cmd) {
    const client = new Gio.SocketClient();
    client.connect_async(Gio.UnixSocketAddress.new(SOCKET), null, (c, res) => {
        try {
            const conn = c.connect_finish(res);
            conn.get_output_stream().write_bytes_async(new GLib.Bytes(new TextEncoder().encode(cmd)),
                GLib.PRIORITY_DEFAULT, null, (stream, r) => {
                    try {
                        stream.write_bytes_finish(r);
                    } finally {
                        conn.close(null);
                    }
                });
        } catch (e) {
            Main.notifyError('Dictate', 'Dictation is not set up: systemctl --user enable --now dictate.socket');
        }
    });
}

// "Rainbow sparkle": four rainbow sound bars and a gold twinkle, laid out on a 16×16 grid
const BARS = [
    {color: '#ff6b6b', x: 0.6, y: 7, height: 5.5},
    {color: '#ffb84d', x: 4, y: 4.5, height: 10.5},
    {color: '#4cd964', x: 7.4, y: 6.5, height: 8},
    {color: '#bf7af0', x: 10.8, y: 9, height: 4},
];
const SPARKLE = {x: 10.2, y: 0.2, size: 6}; // 6×6 star centred at (13.2, 3.2)
const ICON_SCALE = 1.25; // drawn at 20×20 so it reads at the same weight as the top bar's text

const DictateButton = GObject.registerClass({
    Signals: {'state-changed': {param_types: [GObject.TYPE_STRING]}},
}, class DictateButton extends PanelMenu.Button {
    _init(extensionPath, osd) {
        super._init(0.0, 'Dictate', false);
        this._osd = osd;
        // GNOME's own click gesture would open the menu on every click; use ours instead:
        // left click starts/stops dictation, right click opens recent dictations and Retry
        this._clickGesture.set_enabled(false);
        const click = new Clutter.ClickGesture({recognize_on_press: true, required_button: 0});
        click.connect('recognize', gesture => {
            if (gesture.get_button() === Clutter.BUTTON_SECONDARY)
                this.toggleMenu();
            else
                this._toggle();
        });
        this.add_action(click);

        const s = St.ThemeContext.get_for_stage(global.stage).scale_factor * ICON_SCALE;
        const icon = new St.Widget({
            layout_manager: new Clutter.FixedLayout(),
            width: 16 * s,
            height: 16 * s,
            y_align: Clutter.ActorAlign.CENTER,
        });

        this._bars = BARS.map(({color, x, y, height}) => {
            const bar = new St.Widget({
                style: `background-color: ${color}; border-radius: ${s}px;`,
                x: x * s,
                y: y * s,
                width: 2.4 * s,
                height: height * s,
                pivot_point: new Graphene.Point({x: 0.5, y: 0.5}),
            });
            icon.add_child(bar);
            return bar;
        });

        this._sparkle = new St.Icon({
            gicon: Gio.FileIcon.new(Gio.File.new_for_path(GLib.build_filenamev([extensionPath, 'sparkle.svg']))),
            icon_size: Math.round(SPARKLE.size * ICON_SCALE),
            x: SPARKLE.x * s,
            y: SPARKLE.y * s,
            pivot_point: new Graphene.Point({x: 0.5, y: 0.5}),
        });
        icon.add_child(this._sparkle);
        this.add_child(icon);


        this._state = null;
        // Back on screen (after a fullscreen app, or after another extension
        // moved the icon): pick the animation up again. The sparkle is the
        // icon's last child, so by the time it is mapped every bar is too.
        this._sparkle.connect('notify::mapped', () => {
            if (this._sparkle.mapped)
                this._animate();
        });
        this._stateFile = Gio.File.new_for_path(STATE_FILE);
        this._monitor = this._stateFile.monitor_file(Gio.FileMonitorFlags.NONE, null);
        this._monitor.connect('changed', () => this._readState());
        this._readState();
    }

    toggleMenu() {
        if (!this.menu.isOpen)
            this._buildMenu(); // GNOME won't open an empty menu, so fill it first
        this.menu.toggle();
    }

    _buildMenu() {
        this.menu.removeAll();
        let entries = [];
        try {
            const [, bytes] = GLib.file_get_contents(HISTORY_FILE);
            entries = new TextDecoder().decode(bytes).split('\n').filter(l => l).map(l => JSON.parse(l)).reverse();
        } catch (e) {
            // No dictations yet this session
        }
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('Recent dictations · click to copy'));
        if (!entries.length) {
            const empty = new PopupMenu.PopupMenuItem('Nothing dictated yet');
            empty.setSensitive(false);
            this.menu.addMenuItem(empty);
        }
        for (const {time, text} of entries) {
            const oneLine = text.replace(/\s+/g, ' ');
            const label = oneLine.length > 56 ? `${oneLine.slice(0, 55).trimEnd()}…` : oneLine;
            const item = new PopupMenu.PopupMenuItem(label);
            item.add_child(new St.Label({text: this._ago(time), style_class: 'dictate-menu-time',
                x_expand: true, x_align: Clutter.ActorAlign.END, y_align: Clutter.ActorAlign.CENTER}));
            item.connect('activate', () => {
                St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, text);
                this._osd.command(`copied ${text}`);
            });
            this.menu.addMenuItem(item);
        }
        if (GLib.file_test(RECOVERED_FILE, GLib.FileTest.EXISTS)) {
            this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            this.menu.addAction('Retry last recording', () => sendCommand('retry'));
        }
        if (entries.length) {
            this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            this.menu.addAction('Clear history', () => GLib.unlink(HISTORY_FILE));
        }
    }

    _ago(time) {
        const mins = Math.round((Date.now() / 1000 - time) / 60);
        if (mins < 1)
            return 'just now';
        if (mins < 60)
            return `${mins} min ago`;
        return `${Math.round(mins / 60)} h ago`;
    }

    _toggle() {
        // Hand keyboard focus straight back to the window you were typing in,
        // so the dictated text lands there and not in the top bar.
        global.stage.set_key_focus(null);
        sendCommand('toggle');
    }

    _readState() {
        this._stateFile.load_contents_async(null, (file, res) => {
            let state = 'idle';
            try {
                const [, bytes] = file.load_contents_finish(res);
                state = new TextDecoder().decode(bytes).trim();
            } catch (e) {
                // No state file yet: dictation hasn't run since login
            }
            this._setState(state);
            this.emit('state-changed', state);
        });
    }

    // --- animation ---------------------------------------------------------------
    // Ease back and forth between two property sets until _stopAll() is called.
    // While the icon is off screen (the top bar hidden for a fullscreen app),
    // GNOME finishes every ease instantly, so looping on would recurse without
    // end. Stop instead; _animate() starts again when the icon is back.
    _loop(actor, from, to, duration, delay = 0) {
        const step = (props, wait) => {
            actor.ease({
                ...props,
                duration,
                delay: wait,
                mode: Clutter.AnimationMode.EASE_IN_OUT_SINE,
                onStopped: finished => {
                    if (finished && actor.mapped)
                        step(props === to ? from : to, 0);
                },
            });
        };
        step(to, delay);
    }

    _stopAll() {
        for (const actor of [...this._bars, this._sparkle]) {
            actor.remove_all_transitions();
            actor.set({scale_x: 1, scale_y: 1, opacity: 255, rotation_angle_z: 0});
        }
    }

    _setState(state) {
        if (state === this._state)
            return;
        this._state = state;
        this._animate();
    }

    _animate() {
        this._stopAll();
        const state = this._state;
        if (state === 'listening') {
            // Bars bounce one after another, sparkle twinkles
            this._bars.forEach((bar, i) =>
                this._loop(bar, {scale_y: 1}, {scale_y: 0.4}, 450, i * 150));
            this._twinkle();
        } else if (state === 'transcribing') {
            this._twinkle();
        }
    }

    _twinkle() {
        this._loop(this._sparkle,
            {scale_x: 0.55, scale_y: 0.55, opacity: 64, rotation_angle_z: 0},
            {scale_x: 1.1, scale_y: 1.1, opacity: 255, rotation_angle_z: 45}, 400);
    }

    destroy() {
        this._stopAll();
        this._monitor?.cancel();
        this._monitor = null;
        super.destroy();
    }
});

/** Inserts dictated text into the focused app. */
class TextInserter {
    constructor() {
        const seat = Clutter.get_default_backend().get_default_seat();
        this._keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    }

    /** Returns how the text was delivered: "commit" (typed into the focused text field), "paste" (pasted,
     *  and left on the clipboard as a backup), or "clipboard" (nothing could take it: only copied). */
    type(text) {
        // Apps that talk to GNOME's input method take the whole string directly: any character, instantly
        if (Main.inputMethod.currentFocus) {
            Main.inputMethod.commit(text);
            return 'commit';
        }
        const clipboard = St.Clipboard.get_default();
        clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
        // No app window has focus (e.g. you're on the desktop): keep it on the clipboard for you to paste
        const win = global.display.focus_window;
        if (!win || win.get_window_type() === Meta.WindowType.DESKTOP)
            return 'clipboard';
        // Other apps (X11/Electron) may have a text field GNOME can't see: paste it, and leave it on the
        // clipboard so it isn't lost if no field was active
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 60, () => {
            this._paste();
            return GLib.SOURCE_REMOVE;
        });
        return 'paste';
    }

    _paste() {
        const wmClass = (global.display.focus_window?.get_wm_class() ?? '').toLowerCase();
        const keys = TERMINALS.includes(wmClass)
            ? [Clutter.KEY_Control_L, Clutter.KEY_Shift_L, Clutter.KEY_v]
            : [Clutter.KEY_Control_L, Clutter.KEY_v];
        const t = GLib.get_monotonic_time();
        keys.forEach(k => this._keyboard.notify_keyval(t, k, Clutter.KeyState.PRESSED));
        [...keys].reverse().forEach(k => this._keyboard.notify_keyval(t, k, Clutter.KeyState.RELEASED));
    }

    destroy() {
        this._keyboard.run_dispose();
    }
}

/** Enter finishes dictation, but is only taken while you're listening. (Insert stays a GNOME Settings
 *  shortcut, so it keeps working even if this extension isn't loaded.) Mutter drops the grab if
 *  GNOME Shell restarts, so Enter can never stay stuck. */
class EnterKey {
    constructor() {
        this._action = 0;
        this._signal = global.display.connect('accelerator-activated', (d, action) => {
            if (action === this._action)
                sendCommand('stop');
        });
    }

    setListening(listening) {
        if (listening && !this._action) {
            const action = global.display.grab_accelerator('Return', Meta.KeyBindingFlags.NONE);
            if (action === Meta.KeyBindingAction.NONE)
                return;
            Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action),
                Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW | Shell.ActionMode.POPUP);
            this._action = action;
        } else if (!listening && this._action) {
            global.display.ungrab_accelerator(this._action);
            Main.wm.allowKeybinding(Meta.external_binding_name_for_action(this._action), Shell.ActionMode.NONE);
            this._action = 0;
        }
    }

    destroy() {
        this.setListening(false);
        global.display.disconnect(this._signal);
    }
}

export default class DictateExtension extends Extension {
    enable() {
        this._inserter = new TextInserter();
        this._enter = new EnterKey();
        this._osd = new DictateOsd();
        this._indicator = new DictateButton(this.path, this._osd);
        this._indicator.connect('state-changed', (b, state) => this._enter.setListening(state === 'listening'));
        // Append to the left box, after the Desktop and Apps buttons
        Main.panel.addToStatusArea(this.uuid, this._indicator, -1, 'left');

        this._dbus = Gio.DBusExportedObject.wrapJSObject(DBUS_IFACE, {
            Ping: () => {},
            Osd: command => this._osd.command(command),
            Type: text => {
                try {
                    return this._inserter.type(text);
                } catch (e) {
                    logError(e, 'dictate: typing failed');
                    return 'failed';
                }
            },
        });
        this._dbus.export(Gio.DBus.session, '/org/gnome/Shell/Extensions/Dictate');
    }

    disable() {
        this._dbus?.unexport();
        this._dbus = null;
        this._osd?.destroy();
        this._osd = null;
        this._indicator?.destroy();
        this._indicator = null;
        this._enter?.destroy();
        this._enter = null;
        this._inserter?.destroy();
        this._inserter = null;
    }
}
