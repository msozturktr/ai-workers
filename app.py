#!/usr/bin/env python3
"""ai-workers desktop app: the dashboard in its own native window (GTK 3 + WebKitGTK).

  python3 app.py [--inspect]

Single instance: launching again raises the existing window. Uses the dashboard service
when it is running, otherwise serves the UI from a thread inside this process.

Requires the system packages python-gobject and webkit2gtk-4.1 (Arch: `pacman -S
python-gobject webkit2gtk-4.1`, Debian/Ubuntu: `apt install python3-gi gir1.2-webkit2-4.1`).
The rest of ai-workers stays standard-library only and does not need them.
"""
import json, os, socket, subprocess, sys, threading, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

APP_ID = "io.github.msozturktr.AiWorkers"
TITLE = "ai-workers"
STATE_FILE = os.path.expanduser("~/.config/ai-workers/app.json")
DEFAULT_PORT = 8765

_import_error = None
try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gdk, Gio, GLib, Gtk, WebKit2
except (ImportError, ValueError) as e:  # missing PyGObject or typelib
    _import_error = e


def available():
    return _import_error is None


def why_unavailable():
    return f"{type(_import_error).__name__}: {_import_error}" if _import_error else ""


# ---------------------------------------------------------------- backend

def _port():
    try:
        with open(os.path.expanduser("~/.config/ai-workers/config.json")) as f:
            p = json.load(f).get("port")
    except (OSError, ValueError):
        p = None
    return int(p or os.environ.get("AI_WORKERS_PORT") or DEFAULT_PORT)


def _healthy(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return json.load(r).get("app") == "ai-workers"
    except Exception:
        return False


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ensure_backend():
    """URL of a running dashboard: the service, or an in-process server as a fallback."""
    port = _port()
    if _healthy(port):
        return f"http://127.0.0.1:{port}"
    try:  # installed service but stopped: start it
        if subprocess.run(["systemctl", "--user", "start", "ai-workers.service"],
                          capture_output=True, timeout=5).returncode == 0:
            for _ in range(15):
                if _healthy(port):
                    return f"http://127.0.0.1:{port}"
                time.sleep(0.2)
    except (OSError, subprocess.SubprocessError):
        pass
    import dashboard
    try:
        srv = dashboard.make_server(port)
    except OSError:  # port taken by something else
        port = _free_port()
        srv = dashboard.make_server(port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}"


# ---------------------------------------------------------------- window state

def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


# ---------------------------------------------------------------- app

if available():
    class App(Gtk.Application):
        def __init__(self, inspect=False):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
            self.inspect = inspect
            self.window = None
            self.url = None
            GLib.set_application_name(TITLE)
            GLib.set_prgname(APP_ID)  # Wayland app_id -> matches the .desktop file and icon

        def do_startup(self):
            Gtk.Application.do_startup(self)
            for name, accels, cb in (
                ("quit", ["<Primary>q"], lambda *_: self.quit()),
                ("reload", ["<Primary>r", "F5"], lambda *_: self.window and self.window.view.reload()),
                ("fullscreen", ["F11"], lambda *_: self.window and self.window.toggle_fullscreen()),
                ("zoom-in", ["<Primary>plus", "<Primary>equal"], lambda *_: self.window and self.window.zoom(0.1)),
                ("zoom-out", ["<Primary>minus"], lambda *_: self.window and self.window.zoom(-0.1)),
                ("zoom-reset", ["<Primary>0"], lambda *_: self.window and self.window.zoom(None)),
            ):
                act = Gio.SimpleAction.new(name, None)
                act.connect("activate", cb)
                self.add_action(act)
                self.set_accels_for_action(f"app.{name}", accels)

        def do_activate(self):
            if self.window is None:
                if self.url is None:
                    self.url = ensure_backend()
                self.window = Window(self, self.url, self.inspect)
            self.window.present()

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app, url, inspect):
            super().__init__(application=app, title=TITLE)
            self.origin = url
            self.state = _load_state()
            self.set_default_size(self.state.get("width", 1280), self.state.get("height", 820))
            if self.state.get("maximized"):
                self.maximize()
            icon = os.path.join(HERE, "icon.svg")
            theme = Gtk.IconTheme.get_default()
            if theme.has_icon(APP_ID):
                self.set_icon_name(APP_ID)
            elif os.path.exists(icon):
                self.set_icon_from_file(icon)
            self.fullscreen_on = False

            ctx = WebKit2.WebContext.get_default()
            ctx.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)
            self.view = WebKit2.WebView.new_with_context(ctx)
            s = self.view.get_settings()
            s.set_enable_developer_extras(inspect)
            s.set_enable_write_console_messages_to_stdout(inspect)
            s.set_javascript_can_access_clipboard(True)
            s.set_enable_back_forward_navigation_gestures(False)
            self.view.set_zoom_level(self.state.get("zoom", 1.0))
            self.view.connect("decide-policy", self._on_policy)
            self.view.connect("context-menu", self._on_context_menu)
            self.view.connect("load-failed", self._on_load_failed)
            bg = Gdk.RGBA()
            bg.parse("rgba(0,0,0,0)")
            self.view.set_background_color(bg)
            self.add(self.view)
            self.view.load_uri(url + "/?app=1")
            self.view.show()

            self.connect("delete-event", self._on_close)
            self.connect("window-state-event", self._on_state)

        # keep navigation inside the app; external links go to the system browser
        def _on_policy(self, view, decision, kind):
            if kind in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION,
                        WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
                uri = decision.get_navigation_action().get_request().get_uri()
                if not uri.startswith(self.origin):
                    decision.ignore()
                    try:
                        Gtk.show_uri_on_window(self, uri, Gdk.CURRENT_TIME)
                    except GLib.Error:
                        pass
                    return True
            return False

        def _on_context_menu(self, view, menu, event, hit):
            if self.view.get_settings().get_enable_developer_extras():
                return False
            # only text actions (copy, select all); no back/forward/reload
            keep = {WebKit2.ContextMenuAction.COPY, WebKit2.ContextMenuAction.CUT,
                    WebKit2.ContextMenuAction.PASTE, WebKit2.ContextMenuAction.SELECT_ALL,
                    WebKit2.ContextMenuAction.COPY_LINK_TO_CLIPBOARD}
            for item in list(menu.get_items()):
                if item.get_stock_action() not in keep:
                    menu.remove(item)
            return menu.get_n_items() == 0

        def _on_load_failed(self, view, event, uri, error):
            html = ("<body style='font:15px system-ui;padding:40px;color:#888'>"
                    "<h3>ai-workers dashboard is not reachable</h3>"
                    f"<p>{GLib.markup_escape_text(error.message)}</p>"
                    "<p>Press Ctrl+R to retry, or run <code>ai-workers doctor</code>.</p></body>")
            view.load_alternate_html(html, uri, None)
            return True

        def _on_state(self, win, event):
            self.state["maximized"] = bool(event.new_window_state & Gdk.WindowState.MAXIMIZED)
            self.fullscreen_on = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)
            return False

        def _on_close(self, *_):
            if not self.state.get("maximized") and not self.fullscreen_on:
                w, h = self.get_size()
                self.state.update(width=w, height=h)
            self.state["zoom"] = round(self.view.get_zoom_level(), 2)
            _save_state(self.state)
            return False

        def toggle_fullscreen(self):
            (self.unfullscreen if self.fullscreen_on else self.fullscreen)()

        def zoom(self, delta):
            z = 1.0 if delta is None else max(0.5, min(2.5, self.view.get_zoom_level() + delta))
            self.view.set_zoom_level(z)


def main():
    if not available():
        print(f"ai-workers app: GTK/WebKit not available ({why_unavailable()}).\n"
              "Install python-gobject + webkit2gtk-4.1, or use `ai-workers open` for the browser.",
              file=sys.stderr)
        return 1
    inspect = "--inspect" in sys.argv
    return App(inspect=inspect).run([a for a in sys.argv if a != "--inspect"])


if __name__ == "__main__":
    sys.exit(main())
