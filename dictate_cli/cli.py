"""dictate: private, offline dictation.

  dictate setup               guided setup: pick a model, download it, test your mic, choose a shortcut
  dictate models              list models; --use KEY / --add KEY / --remove KEY
  dictate doctor              check that everything works
  dictate start | stop | toggle | retry
  dictate vocab               open your vocabulary list
  dictate uninstall           remove Dictate's settings, models and autostart
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import time

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn,
                           TransferSpeedColumn)
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from dictate_core import __version__, catalog, download
from dictate_core.config import (VOCABULARY_TEMPLATE, config_dir, data_dir, load_settings, save_settings,
                                 vocabulary_file)

IS_WINDOWS = sys.platform == "win32"
console = Console(highlight=False)
RAINBOW = ["#ff6b6b", "#ffb84d", "#4cd964", "#5ac8fa", "#bf7af0"]
ACCENT = "#5ac8fa"
MODELS_DIR = os.path.join(data_dir(), "models")


# --- looks ---------------------------------------------------------------------------------

def rainbow(text, bold=True):
    t = Text()
    for i, ch in enumerate(text):
        t.append(ch, style=f"{'bold ' if bold else ''}{RAINBOW[i * len(RAINBOW) // max(1, len(text))]}")
    return t


def banner():
    bars = Text()
    for h, c in zip("▃▇▅▂", RAINBOW[:3] + RAINBOW[4:]):
        bars.append(h, style=c)
    bars.append("✦", style="bold #ffd60a")
    title = Text.assemble(bars, "  ", rainbow("Dictate"), (f"  v{__version__}", "dim"))
    sub = Text("Speak anywhere, type nowhere · private and offline", style="dim")
    credit = Text.assemble(("Made by ", "dim"), ("Nitin Reddy", "bold #ffd60a"), (" · ", "dim"),
                           ("github.com/nitinreddy08", f"underline {ACCENT}"))
    console.print(Panel(Group(Align.center(title), Align.center(sub), Text(""), Align.center(credit)),
                        box=box.ROUNDED, border_style="#3a3a40", padding=(1, 4)))


def step(n, total, title):
    console.print()
    console.print(Text.assemble((f" {n}/{total} ", f"bold black on {ACCENT}"), "  ", (title, "bold")))
    console.print()


def ok(msg):
    console.print(Text.assemble(("  ✔ ", "bold #30d158"), msg))


def warn(msg):
    console.print(Text.assemble(("  ! ", "bold #ff9f0a"), msg))


def fail(msg):
    console.print(Text.assemble(("  ✘ ", "bold #ff453a"), msg))


def dots(n, total=5):
    return Text.assemble(("●" * n, "#30d158"), ("●" * (total - n), "#3a3a40"))


# --- system checks --------------------------------------------------------------------------

def cpu_name():
    """The marketing name, e.g. "11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz"."""
    try:
        if IS_WINDOWS:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        if sys.platform.startswith("linux"):
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        if sys.platform == "darwin":
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                                  text=True).stdout.strip()
    except Exception:
        pass
    return platform.processor() or platform.machine()


def windows_name():
    """Windows 11 still reports itself as version 10; the build number tells them apart."""
    build = sys.getwindowsversion().build
    return f"Windows {11 if build >= 22000 else 10} (build {build})"


def system_info():
    import psutil

    info = {
        "os": windows_name() if IS_WINDOWS else f"{platform.system()} {platform.release()}",
        "cpu": cpu_name(),
        "cores": psutil.cpu_count(logical=False) or 1,
        "memory_gb": psutil.virtual_memory().total / 2**30,
        "free_disk_gb": shutil.disk_usage(os.path.dirname(MODELS_DIR) if os.path.exists(
            os.path.dirname(MODELS_DIR)) else os.path.expanduser("~")).free / 2**30,
    }
    try:
        import sounddevice as sd

        info["mic"] = sd.query_devices(kind="input")["name"]
    except Exception:
        info["mic"] = None
    return info


def show_system(info):
    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("System", info["os"])
    t.add_row("Processor", f"{info['cpu']} · {info['cores']} cores")
    t.add_row("Memory", f"{info['memory_gb']:.1f} GB")
    t.add_row("Free disk", f"{info['free_disk_gb']:.1f} GB")
    t.add_row("Microphone", info["mic"] or Text("none found: connect one before dictating", style="#ff9f0a"))
    console.print(t)


# --- models ---------------------------------------------------------------------------------

def model_table(recommended, current=None):
    t = Table(box=box.ROUNDED, border_style="#3a3a40", header_style="bold", padding=(0, 1), expand=False)
    t.add_column("#", justify="right", style="dim")
    t.add_column("Model")
    t.add_column("Download", justify="right")
    t.add_column("Memory", justify="right")
    t.add_column("Speed¹", justify="right")
    t.add_column("Accuracy")
    t.add_column("Pros and cons", max_width=46)
    for i, m in enumerate(catalog.MODELS.values(), 1):
        name = Text(m.key, style="bold")
        if m.key == recommended:
            name.append("  ★ recommended", style="bold #ffd60a")
        if download.is_installed(MODELS_DIR, m.key):
            name.append("  ✔ installed", style="#30d158")
        if m.key == current and download.is_installed(MODELS_DIR, m.key):
            name.append("  · in use", style=ACCENT)
        size = f"{m.download_mb / 1000:.1f} GB" if m.download_mb >= 1000 else f"{m.download_mb} MB"
        speed = f"{'~' if not m.measured else ''}{m.seconds:g} s"
        notes = Text.assemble(("+ ", "#30d158"), m.pros, "\n", ("− ", "#ff453a"), m.cons, "\n",
                              (f"Best for: {m.best_for}", "dim"))
        t.add_row(str(i), name, size, f"{m.memory_mb / 1000:.1f} GB" if m.memory_mb >= 1000 else
                  f"{m.memory_mb} MB", speed, dots(m.accuracy), notes)
    console.print(t)
    console.print(Text("  ¹ seconds to turn 15 s of speech into text on a 4-core laptop processor; "
                       "~ = estimate. The model is only in memory while you dictate.", style="dim"))


def fetch(key):
    """Download a model with a progress bar. Returns True when it's installed."""
    m = catalog.MODELS[key]
    if download.is_installed(MODELS_DIR, key):
        ok(f"{key} is already downloaded")
        return True
    os.makedirs(MODELS_DIR, exist_ok=True)
    columns = [TextColumn("  [bold]{task.description}"), BarColumn(bar_width=36, complete_style=ACCENT,
               finished_style="#30d158"), TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
               DownloadColumn(binary_units=False), TransferSpeedColumn(), TimeRemainingColumn()]
    try:
        with Progress(*columns, console=console, transient=False) as progress:
            task = progress.add_task(f"Downloading {key}", total=m.download_mb * 1_000_000)

            def update(name, done, total):
                label = "Verifying" if name.startswith("verifying") else "Downloading"
                progress.update(task, completed=done, total=total, description=f"{label} {key}")

            download.download(m.repo, MODELS_DIR, key, update)
            progress.update(task, description=f"Downloaded {key}")
    except KeyboardInterrupt:
        warn("Paused. Run the same command again to resume where it stopped.")
        return False
    except Exception as ex:
        fail(f"Download failed: {ex}")
        warn("Check your internet connection and run it again: it resumes where it stopped.")
        return False
    ok(f"{key} downloaded and verified")
    return True


# --- mic test --------------------------------------------------------------------------------

def mic_test(key, seconds=4):
    """Record a few seconds with a live level meter, then show what the model heard."""
    import numpy as np
    import sounddevice as sd

    from dictate_core import RATE
    from dictate_core.engine import Model
    from dictate_core.text import clean

    console.print(Text("  Say a sentence, for example: ", style="dim") +
                  Text("“Testing my new dictation app, full stop.”", style="italic"))
    Prompt.ask(Text("  Press Enter, then speak"), default="", show_default=False)
    blocks, level = [], [0.0]

    def cb(indata, frames, t, status):
        blocks.append(indata[:, 0].copy())
        level[0] = float(np.sqrt(np.mean(indata[:, 0] ** 2)))

    model = Model(key, MODELS_DIR, log=lambda m: None)
    model.preload()
    try:
        with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=cb, blocksize=RATE // 20), \
                Live(console=console, refresh_per_second=20, transient=True) as live:
            end = time.monotonic() + seconds
            while (left := end - time.monotonic()) > 0:
                n = int(min(1.0, (level[0] * 4.5) ** 0.8) * 30)
                bar = Text("  ● ", style="bold #ff453a")
                for i in range(30):
                    bar.append("█" if i < n else "·", style=RAINBOW[i * 5 // 30] if i < n else "#3a3a40")
                bar.append(f"  {left:3.1f} s", style="dim")
                live.update(bar)
                time.sleep(0.05)
    except Exception as ex:
        fail(f"Couldn't use the microphone: {ex}")
        return False
    audio = np.concatenate(blocks) if blocks else np.zeros(0, np.float32)
    peak = float(abs(audio).max()) if audio.size else 0.0
    with console.status("  Transcribing on this PC…", spinner="dots"):
        t = time.time()
        text = clean(model.transcribe(audio), peak)
        took = time.time() - t
    if peak < 0.002:
        fail("The microphone only sent silence. Check your input in Settings → System → Sound.")
        return False
    if not text:
        warn("Didn't catch any words. Try speaking a little closer to the mic.")
        return False
    console.print(Panel(Text(text), title="I heard", title_align="left", border_style="#30d158",
                        subtitle=f"{took:.1f} s", subtitle_align="right", padding=(0, 2)))
    return True


# --- shortcut ---------------------------------------------------------------------------------

SHORTCUTS = ["Insert", "F9", "Pause", "Ctrl+Alt+Space"]


def shortcut_free(combo):
    if not IS_WINDOWS:
        return True
    from dictate_win import win32
    from dictate_win.hotkeys import parse

    mods, vk = parse(combo)
    if win32.user32.RegisterHotKey(None, 0x7FFF, mods, vk):
        win32.user32.UnregisterHotKey(None, 0x7FFF)
        return True
    return False


def choose_shortcut(current):
    for i, s in enumerate(SHORTCUTS, 1):
        tag = Text("  (current)", style=ACCENT) if s == current else Text("")
        console.print(Text.assemble((f"  {i}  ", "dim"), (s, "bold"), tag))
    console.print(Text("  Or type your own, e.g. Ctrl+Shift+D", style="dim"))
    while True:
        answer = Prompt.ask("  Shortcut", default=str(SHORTCUTS.index(current) + 1 if current in SHORTCUTS
                                                     else current))
        combo = SHORTCUTS[int(answer) - 1] if answer.isdigit() and 0 < int(answer) <= len(SHORTCUTS) else answer
        try:
            if shortcut_free(combo):
                ok(f"{combo} starts and stops dictation · Enter also finishes")
                return combo
            warn(f"{combo} is already used by another app. Pick another.")
        except ValueError as ex:
            warn(str(ex))


# --- the running app ------------------------------------------------------------------------

def send(cmd):
    """Send a command to the running tray app. Returns False if it isn't running."""
    from dictate_win.app import server_name

    path = rf"\\.\pipe\{server_name()}" if IS_WINDOWS else os.path.join("/tmp", server_name())
    try:
        if IS_WINDOWS:
            with open(path, "wb", buffering=0) as pipe:
                pipe.write(cmd.encode())
        else:
            import socket

            s = socket.socket(socket.AF_UNIX)
            s.connect(path)
            s.sendall(cmd.encode())
            s.close()
        return True
    except OSError:
        return False


def start_app():
    if send("ping"):
        return True
    exe = sys.executable
    if IS_WINDOWS:
        pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        subprocess.Popen([pythonw if os.path.exists(pythonw) else exe, "-m", "dictate_win"],
                         creationflags=0x00000008 | 0x00000200, close_fds=True)  # detached, own group
    else:
        subprocess.Popen([exe, "-m", "dictate_win"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        time.sleep(0.2)
        if send("ping"):
            return True
    return False


# --- commands -------------------------------------------------------------------------------

def cmd_setup(args):
    banner()
    settings = load_settings()
    total = 5
    step(1, total, "Checking this PC")
    with console.status("  Looking at your processor, memory and microphone…", spinner="dots"):
        info = system_info()
    show_system(info)
    rec = catalog.recommend(info["memory_gb"], info["cores"])

    step(2, total, "Choose your speech model")
    console.print(Text("  Everything runs on this PC: your voice never leaves it. "
                       "You can switch models any time with `dictate models`.", style="dim"))
    console.print()
    model_table(rec, settings.get("model"))
    keys = list(catalog.MODELS)
    if args.model:
        key = args.model
    elif args.yes:
        key = rec
    else:
        answer = Prompt.ask("  Pick a model", default=str(keys.index(rec) + 1),
                            choices=[str(i) for i in range(1, len(keys) + 1)] + keys, show_choices=False)
        key = keys[int(answer) - 1] if answer.isdigit() else answer
    m = catalog.MODELS[key]
    if m.download_mb / 1000 > info["free_disk_gb"] - 0.5:
        fail(f"Not enough free disk space for {key} ({m.download_mb} MB).")
        return 1
    if m.memory_mb / 1024 > info["memory_gb"] * 0.6:
        warn(f"{key} needs about {m.memory_mb} MB while dictating; this PC may slow down.")
    console.print()
    if not fetch(key):
        return 1
    settings["model"] = key

    step(3, total, "Test your microphone")
    if args.no_mic_test or not info["mic"]:
        warn("Skipped" + ("" if info["mic"] else ": no microphone found"))
    else:
        while not mic_test(key) and Confirm.ask("  Try again?", default=True):
            pass

    step(4, total, "Pick your shortcut")
    if args.yes:
        ok(f"{settings['hotkey']} starts and stops dictation · Enter also finishes")
    else:
        settings["hotkey"] = choose_shortcut(settings["hotkey"])

    step(5, total, "Finishing up")
    if not args.yes:
        settings["sounds"] = Confirm.ask("  Play a soft chime when listening starts and stops?",
                                         default=settings["sounds"])
        settings["autostart"] = Confirm.ask("  Start Dictate when you sign in?", default=settings["autostart"])
    save_settings(settings)
    ok(f"Settings saved to {config_dir()}")
    if IS_WINDOWS:
        from dictate_win import autostart

        autostart.set_enabled(settings["autostart"])
        if settings["autostart"]:
            ok("Starts with Windows")
    if not os.path.exists(vocabulary_file()):
        with open(vocabulary_file(), "w", encoding="utf-8") as f:
            f.write(VOCABULARY_TEMPLATE)
    started = False if args.no_start else start_app()
    if started:
        ok("Dictate is running: look for the rainbow icon by the clock")
    console.print()
    how = Text.assemble("Click into any text box, press ", (settings["hotkey"], "bold"), ", speak, then press ",
                        (settings["hotkey"], "bold"), " or ", ("Enter", "bold"), ".\n",
                        ("Right-click the tray icon for your last 3 dictations, Retry and settings.", "dim"))
    console.print(Panel(how, title=rainbow("You're all set"), border_style="#30d158", padding=(1, 3)))
    return 0


def cmd_models(args):
    settings = load_settings()
    if args.add or args.use:
        key = args.add or args.use
        if key not in catalog.MODELS:
            fail(f"Unknown model {key}. Choose from: {', '.join(catalog.MODELS)}")
            return 1
        if not fetch(key):
            return 1
        if args.use:
            settings["model"] = key
            save_settings(settings)
            ok(f"Now using {key} (applies to your next dictation)")
            send("reload")
        return 0
    if args.remove:
        if args.remove == settings["model"]:
            fail("That's the model in use. Switch first: dictate models --use <model>")
            return 1
        download.remove(MODELS_DIR, args.remove)
        ok(f"Removed {args.remove}")
        return 0
    import psutil

    model_table(catalog.recommend(psutil.virtual_memory().total / 2**30, psutil.cpu_count(logical=False) or 1),
                settings["model"])
    return 0


def cmd_doctor(args):
    banner()
    settings, problems = load_settings(), 0
    info = system_info()
    show_system(info)
    console.print()
    if info["mic"]:
        ok(f"Microphone: {info['mic']}")
    else:
        fail("No microphone found")
        problems += 1
    if download.is_installed(MODELS_DIR, settings["model"]):
        ok(f"Model {settings['model']} is installed")
    else:
        fail(f"Model {settings['model']} is missing: run `dictate models --use {settings['model']}`")
        problems += 1
    running = send("ping")
    (ok if running else warn)("Dictate is running" if running else "Dictate isn't running: `dictate start`")
    if not running:
        try:
            (ok if shortcut_free(settings["hotkey"]) else fail)(f"Shortcut {settings['hotkey']}")
        except ValueError as ex:
            fail(str(ex))
            problems += 1
    if IS_WINDOWS:
        from dictate_win import autostart

        (ok if autostart.is_enabled() else warn)(
            "Starts with Windows" if autostart.is_enabled() else "Doesn't start with Windows")
    console.print()
    console.print(Text("  Everything looks good." if not problems else f"  {problems} problem(s) found.",
                       style="bold #30d158" if not problems else "bold #ff453a"))
    return 1 if problems else 0


def cmd_uninstall(args):
    console.print(Panel(Text("This removes Dictate's settings, downloaded models, history and autostart."),
                        border_style="#ff9f0a"))
    if not args.yes and not Confirm.ask("  Continue?", default=False):
        return 0
    send("quit")
    if IS_WINDOWS:
        from dictate_win import autostart

        autostart.set_enabled(False)
    for d in (data_dir(), config_dir()):
        shutil.rmtree(d, ignore_errors=True)
    ok("Removed settings, models and history")
    console.print(Text("  To remove the program itself:  ", style="dim") + Text("uv tool uninstall dictate", "bold"))
    return 0


def cmd_vocab(args):
    path = vocabulary_file()
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(VOCABULARY_TEMPLATE)
    if IS_WINDOWS:
        os.startfile(path)
    console.print(Text("  Vocabulary: ", style="dim") + Text(path))
    return 0


def use_utf8():
    """Windows consoles (and redirected output) often default to a legacy code page that can't show ✔,
    ★ or the rainbow bars: switch to UTF-8, and never crash on a character that still can't be shown."""
    if IS_WINDOWS:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        os.system("")  # turn on colours in older consoles
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    p = argparse.ArgumentParser(prog="dictate", description="Private, offline dictation.")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("setup", help="guided setup")
    s.add_argument("--model", choices=list(catalog.MODELS))
    s.add_argument("--yes", action="store_true", help="accept the recommended choices")
    s.add_argument("--no-mic-test", action="store_true")
    s.add_argument("--no-start", action="store_true")
    mo = sub.add_parser("models", help="list, download, switch or remove models")
    g = mo.add_mutually_exclusive_group()
    g.add_argument("--use", metavar="KEY")
    g.add_argument("--add", metavar="KEY")
    g.add_argument("--remove", metavar="KEY")
    sub.add_parser("doctor", help="check that everything works")
    for name in ("start", "stop", "toggle", "retry", "vocab"):
        sub.add_parser(name)
    u = sub.add_parser("uninstall", help="remove settings, models and autostart")
    u.add_argument("--yes", action="store_true")
    args = p.parse_args(argv)
    use_utf8()
    try:
        if args.cmd == "setup":
            return cmd_setup(args)
        if args.cmd == "models":
            return cmd_models(args)
        if args.cmd == "doctor":
            return cmd_doctor(args)
        if args.cmd == "uninstall":
            return cmd_uninstall(args)
        if args.cmd == "vocab":
            return cmd_vocab(args)
        if args.cmd == "start":
            (ok if start_app() else fail)("Dictate is running" if send("ping") else "Couldn't start Dictate")
            return 0
        if args.cmd in ("stop", "toggle", "retry"):
            if not send("quit" if args.cmd == "stop" else args.cmd):
                warn("Dictate isn't running: `dictate start`")
                return 1
            return 0
        p.print_help()
        return 0
    except KeyboardInterrupt:
        console.print()
        warn("Cancelled")
        return 130


if __name__ == "__main__":
    sys.exit(main())
