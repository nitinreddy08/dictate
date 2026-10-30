// Test-only helper for tools/test-shell/run.sh: lets scripts screenshot the hidden session,
// open the Dictate menu and click the Dictate icon. Never installed in a real session.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const IFACE = `<node><interface name="test.Shot">
  <method name="Shot"><arg type="s" direction="in"/></method>
  <method name="OpenMenu"/>
  <method name="ClickIcon"><arg type="u" direction="in"/></method>
  <method name="ImState"><arg type="s" direction="out"/></method>
  <method name="Keys"><arg type="s" direction="in"/></method>
</interface></node>`;

export default class ShotExtension extends Extension {
    enable() {
        // Remember the text-cursor rectangle apps report through GNOME's input method
        this._rect = null;
        this._imSignal = Main.inputMethod.connect('cursor-location-changed', (im, rect) => {
            this._rect = [rect.origin.x, rect.origin.y, rect.size.width, rect.size.height].map(Math.round);
        });
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, {
            Shot: path => {
                const stream = Gio.File.new_for_path(path).replace(null, false, Gio.FileCreateFlags.NONE, null);
                new Shell.Screenshot().screenshot(false, stream).then(() => stream.close(null)).catch(logError);
            },
            OpenMenu: () => Main.panel.statusArea['dictate@local'].toggleMenu(),
            // {"imFocus": bool, "cursor": [x,y,w,h]|null, "app": wm_class} for the focused window
            ImState: () => JSON.stringify({
                imFocus: !!Main.inputMethod.currentFocus,
                cursor: this._rect,
                app: global.display.focus_window?.get_wm_class() ?? null,
            }),
            // Press keys, e.g. "Control_L+l" or "Tab"
            Keys: combo => {
                this._kbd ??= Clutter.get_default_backend().get_default_seat()
                    .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
                const keys = combo.split('+').map(k => Clutter[`KEY_${k}`]);
                const t = GLib.get_monotonic_time();
                keys.forEach(k => this._kbd.notify_keyval(t, k, Clutter.KeyState.PRESSED));
                [...keys].reverse().forEach(k => this._kbd.notify_keyval(t + 1000, k, Clutter.KeyState.RELEASED));
            },
            ClickIcon: button => {
                const icon = Main.panel.statusArea['dictate@local'];
                const [x, y] = icon.get_transformed_position();
                const [w, h] = icon.get_transformed_size();
                this._ptr ??= Clutter.get_default_backend().get_default_seat()
                    .create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
                const t = GLib.get_monotonic_time();
                this._ptr.notify_absolute_motion(t, x + w / 2, y + h / 2);
                this._ptr.notify_button(t + 1000, button, Clutter.ButtonState.PRESSED);
                this._ptr.notify_button(t + 60000, button, Clutter.ButtonState.RELEASED);
            },
        });
        this._dbus.export(Gio.DBus.session, '/test/Shot');
    }

    disable() {
        Main.inputMethod.disconnect(this._imSignal);
        this._dbus.unexport();
    }
}
