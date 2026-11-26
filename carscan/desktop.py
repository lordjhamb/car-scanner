"""Desktop launcher for personal installs (the downloadable Mac / Windows / Linux app).

- keeps data in the user's own app-data folder, not next to the program
- listens on 127.0.0.1 only, signs the owner in automatically
- reuses an already-running copy instead of starting a second one
- opens the browser, and stops cleanly from the app's "Quit" button or Ctrl+C
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import socket
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "Car Scanner"
DEFAULT_PORT = 8077


def user_data_dir(system: str | None = None, home: Path | None = None, env: dict | None = None) -> Path:
    system = system or platform.system()
    home = home or Path.home()
    env = os.environ if env is None else env
    if system == "Darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if system == "Windows":
        return Path(env.get("APPDATA") or home / "AppData" / "Roaming") / APP_NAME
    return Path(env.get("XDG_DATA_HOME") or home / ".local" / "share") / "car-scanner"


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def running_copy(port: int) -> bool:
    """True if Car Scanner is already answering on this port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/system/info", timeout=1.5) as r:  # noqa: S310
            return json.loads(r.read().decode()).get("app") == "carscan"
    except Exception:
        return False


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def choose_port(preferred: int) -> tuple[int, bool]:
    """(port, already_running). Keeps the preferred port so bookmarks keep working."""
    if port_is_free(preferred):
        return preferred, False
    if running_copy(preferred):
        return preferred, True
    return free_port(), False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="car-scanner", description=f"{APP_NAME}: universal car diagnostics")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--no-browser", action="store_true", help="don't open the browser")
    p.add_argument("--data-dir", help="where to keep the database and photos")
    p.add_argument("--version", action="store_true")
    args = p.parse_args(argv)

    from . import __version__
    if args.version:
        print(__version__)
        return 0

    data_dir = Path(args.data_dir) if args.data_dir else user_data_dir()
    port, already = choose_port(args.port)
    url = f"http://127.0.0.1:{port}"
    if already:
        print(f"{APP_NAME} is already running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(data_dir / "car-scanner.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])

    import uvicorn

    from .app import create_app
    from .config import load_settings

    settings = load_settings(env="desktop", data_dir=data_dir, local_mode=True, update_check=True,
                             host="127.0.0.1", port=port)
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    app.state.shutdown = lambda: setattr(server, "should_exit", True)

    print(f"\n  {APP_NAME} {__version__} is running at {url}\n"
          f"  Your data is stored in: {data_dir}\n"
          "  To stop it, use Quit in the app, or close this window / press Ctrl+C.\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    server.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
