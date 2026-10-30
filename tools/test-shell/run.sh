#!/bin/bash
# Run a test script inside a hidden, headless GNOME Shell with the Dictate extension loaded.
# The session gets its own settings and extensions (never your live ones); everything lives in RAM under
# $XDG_RUNTIME_DIR/dictate-shelltest. Inside the script you can use:
#   shot NAME        screenshot to $XDG_RUNTIME_DIR/dictate-shelltest/shots/NAME.png
#   osd "COMMAND"    drive the popup (same commands as dictate.py sends)
#   busctl --user call org.gnome.Shell /test/Shot test.Shot ClickIcon u 1|3   (left/right click the icon)
# Usage: tools/test-shell/run.sh SCRIPT.sh
HERE=$(cd "$(dirname "$0")" && pwd); E=$XDG_RUNTIME_DIR/dictate-shelltest
mkdir -p $E/config $E/data/gnome-shell/extensions $E/shots
ln -sfn "$HERE/../../gnome-extension/dictate@local" $E/data/gnome-shell/extensions/dictate@local
ln -sfn "$HERE/shot@test" $E/data/gnome-shell/extensions/shot@test
export XDG_CONFIG_HOME=$E/config XDG_DATA_HOME=$E/data E
unset DISPLAY WAYLAND_DISPLAY XAUTHORITY
exec dbus-run-session -- bash -c '
gsettings set org.gnome.shell enabled-extensions "[\"dictate@local\", \"shot@test\"]"
gsettings set org.gnome.desktop.interface color-scheme prefer-dark
gnome-shell --headless --virtual-monitor 1366x768 --wayland-display wayland-9 > $E/shell.log 2>&1 & P=$!
sleep 12
shot() { busctl --user call org.gnome.Shell /test/Shot test.Shot Shot s "$E/shots/$1.png"; sleep 0.6; }
osd() { busctl --user call org.gnome.Shell /org/gnome/Shell/Extensions/Dictate org.gnome.Shell.Extensions.Dictate Osd s "$1"; }
source "'"$(realpath "$1")"'"
kill $P; wait $P 2>/dev/null; true'
