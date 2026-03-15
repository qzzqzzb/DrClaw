"""macOS menu-bar tray app that owns DrClaw daemon lifecycle."""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import threading
import webbrowser
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from drclaw.config.schema import DrClawConfig

console = Console()


def _auto_cwd() -> Path:
    """Find project root if running from source, else fallback to home."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.home()


def build_daemon_launch(config: DrClawConfig) -> tuple[list[str], dict[str, str], Path]:
    """Build daemon command, environment, and working directory from config."""
    cmd = list(config.tray.daemon_program)
    env = os.environ.copy()
    env.update(config.tray.daemon_env)
    cwd = Path(config.tray.daemon_cwd).expanduser() if config.tray.daemon_cwd else _auto_cwd()
    return cmd, env, cwd


def _load_icon_image(config: DrClawConfig):  # noqa: ANN201
    """Load tray icon from config path or fallback to packaged asset."""
    from PIL import Image, ImageDraw

    assets_dir = Path(__file__).resolve().parents[2] / "assets"
    if config.tray.icon_path:
        icon_path = Path(config.tray.icon_path).expanduser()
    else:
        icon_path = assets_dir / "taskbar-tube.svg"

    def _try_load(path: Path):  # noqa: ANN202
        if not path.is_file():
            return None
        try:
            if path.suffix.lower() == ".svg":
                try:
                    import resvg_py
                except ImportError:
                    console.print(
                        "[yellow]Warning:[/yellow] SVG tray icon requires resvg-py; "
                        f"could not load {path}."
                    )
                    return None
                png_bytes = resvg_py.svg_to_bytes(
                    svg_path=str(path),
                    width=64,
                    height=64,
                )
                return Image.open(BytesIO(png_bytes)).convert("RGBA")
            with Image.open(path) as icon:
                return icon.convert("RGBA")
        except Exception as exc:
            console.print(
                f"[yellow]Warning:[/yellow] failed to load tray icon {path}: {exc}"
            )
            return None

    image = _try_load(icon_path)
    if image is not None:
        return image

    # Compatibility fallback for environments that only support raster assets.
    image = _try_load(assets_dir / "taskbar.jpg")
    if image is not None:
        return image

    image = Image.new("RGBA", (64, 64), (29, 41, 56, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill=(9, 132, 227, 255))
    draw.text((22, 20), "C", fill=(255, 255, 255, 255))
    return image


class TrayRuntime:
    """Owns daemon child process and pystray menu handlers."""

    def __init__(self, config: DrClawConfig) -> None:
        self.config = config
        self._daemon: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        atexit.register(self.stop_daemon)

    def start_daemon(self) -> None:
        """Start daemon if not running."""
        with self._lock:
            if self._daemon is not None and self._daemon.poll() is None:
                return
            cmd, env, cwd = build_daemon_launch(self.config)
            self._daemon = subprocess.Popen(cmd, env=env, cwd=str(cwd))

    def stop_daemon(self) -> None:
        """Stop daemon with Ctrl+C-equivalent signal."""
        proc: subprocess.Popen[bytes] | None
        with self._lock:
            proc = self._daemon
            self._daemon = None

        if proc is None or proc.poll() is not None:
            return

        proc.send_signal(signal.SIGINT)
        timeout = self.config.tray.shutdown_timeout_seconds
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)

    def on_control_panel(self, icon, item) -> None:  # noqa: ANN001, ARG002
        webbrowser.open(self.config.tray.control_panel_url, new=2)

    def on_exit(self, icon, item) -> None:  # noqa: ANN001, ARG002
        self.stop_daemon()
        icon.stop()

    def run(self) -> None:
        """Start daemon and block in tray event loop."""
        try:
            import pystray
        except ImportError:
            console.print(
                "[red]Error:[/red] pystray is not installed. "
                "Install dependencies for tray support first."
            )
            raise SystemExit(1) from None

        self.start_daemon()
        image = _load_icon_image(self.config)
        menu = pystray.Menu(
            pystray.MenuItem("Control Panel", self.on_control_panel),
            pystray.MenuItem("Exit", self.on_exit),
        )
        icon = pystray.Icon("drclaw", image, "DrClaw", menu)

        def _sigint_handler(signum, frame) -> None:  # noqa: ANN001, ARG005
            self.stop_daemon()
            icon.stop()

        signal.signal(signal.SIGINT, _sigint_handler)
        icon.run()


def run_tray(config: DrClawConfig) -> None:
    """Entry point for tray mode."""
    TrayRuntime(config).run()
