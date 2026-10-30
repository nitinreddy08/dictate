# Does each app report its text cursor to GNOME (needed for a mic bubble next to the cursor)?
# Run with: tools/test-shell/run.sh tools/test-shell/app_probe.sh   (results in $E/probe.txt)
# Every app runs inside the hidden session with a throwaway profile under $E/profiles, in its own
# process group, and only that group is stopped: your own windows are never touched.
P=$E/profiles; rm -rf "$P"; mkdir -p "$P/firefox"
export WAYLAND_DISPLAY=wayland-9 GDK_BACKEND=wayland

launch() { setsid "$@" > /dev/null 2>&1 & PG=$!; }
stop() { kill -TERM -- -"$PG" 2>/dev/null; sleep 2; kill -KILL -- -"$PG" 2>/dev/null; sleep 1; }
probe() {  # probe NAME [keys...]
    local name=$1; shift
    sleep 9
    for k in "$@"; do busctl --user call org.gnome.Shell /test/Shot test.Shot Keys s "$k"; sleep 1; done
    sleep 1.5
    echo "$name $(busctl --user call org.gnome.Shell /test/Shot test.Shot ImState | sed 's/^s //')" >> "$E/probe.txt"
    shot "app_$name"
}

launch gnome-text-editor --standalone; probe gnome-text-editor; stop
launch ptyxis --new-window --standalone; probe ptyxis; stop
launch nautilus --new-window; probe nautilus Control_L+l; stop
launch firefox --no-remote --new-instance --profile "$P/firefox" about:blank; probe firefox Control_L+l; stop
launch brave-browser --user-data-dir="$P/brave" --no-first-run --ozone-platform=wayland about:blank
probe brave Control_L+l; stop
launch brave-browser --user-data-dir="$P/brave2" --no-first-run --ozone-platform=wayland --enable-wayland-ime \
    --wayland-text-input-version=3 about:blank
probe brave+ime Control_L+l; stop
launch claude-desktop --user-data-dir="$P/claude" --ozone-platform=wayland; probe claude Tab; stop
launch claude-desktop --user-data-dir="$P/claude2" --ozone-platform=wayland --enable-wayland-ime \
    --wayland-text-input-version=3
probe claude+ime Tab; stop
launch "$HOME/.local/zed.app/libexec/zed-editor" --user-data-dir "$P/zed"; probe zed; stop
