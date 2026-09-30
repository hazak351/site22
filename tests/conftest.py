from __future__ import annotations

import functools
import http.server
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from adflow.config import detect_browser, load_settings

ROOT = Path(__file__).resolve().parent.parent
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg não instalado")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def settings(tmp_path):
    s = load_settings(
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        config_dir=ROOT / "config",
        generation_durations=[4.0],
        simulator_delay_s=0.0,
        open_browser_on_start=False,
        browser_headless=True,
        cdp_port=free_port(),
        generation_timeout_s=30,
        ui_timeout_ms=5000,
        final_preset="ultrafast",
        normalize_preset="ultrafast",
    )
    s.ensure_dirs()
    return s


def draw_earbud(path: Path, body=(15, 15, 15), accent=(201, 162, 39), background="white") -> Path:
    """Foto sintética de produto: fone preto com detalhe dourado sobre fundo branco."""
    img = Image.new("RGB", (600, 600), background)
    d = ImageDraw.Draw(img)
    d.ellipse((180, 150, 420, 390), fill=body)
    d.rectangle((270, 360, 330, 520), fill=body)
    d.ellipse((250, 210, 350, 310), fill=accent)
    img.save(path)
    return path


@pytest.fixture
def product_image(tmp_path) -> Path:
    return draw_earbud(tmp_path / "fone_frente.png")


@pytest.fixture
def logo_image(tmp_path) -> Path:
    logo = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
    ImageDraw.Draw(logo).rectangle((0, 0, 399, 119), outline=(230, 190, 60, 255), width=10)
    path = tmp_path / "logo.png"
    logo.save(path)
    return path


def make_clip(path: Path, duration: float, size=(1280, 720), color="0x1d1d22", audio=True, fps=24,
              moving=True, freeze=False, product_box=True) -> Path:
    """Clipe de teste: fundo com ruído (movimento), "produto" dourado opcional e tom de áudio."""
    w, h = size
    moving = moving and not freeze
    filters = ["noise=alls=12:allf=t+u" if moving else "null"]
    if product_box:
        x = "iw/2-80+60*sin(t*2)" if moving else "iw/2-80"
        filters.append(f"drawbox=x={x}:y=ih/2-80:w=160:h=160:color=0xc9a227:t=fill")
    filters.append("format=yuv420p")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r={fps}:d={duration}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=44100:duration={duration}"]
    cmd += ["-vf", ",".join(filters)]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-t", str(duration)]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture
def clip_factory(tmp_path):
    counter = {"n": 0}

    def factory(duration=4.0, **kwargs) -> Path:
        counter["n"] += 1
        return make_clip(tmp_path / f"clip_{counter['n']}.mp4", duration, **kwargs)

    return factory


# ------------------------------------------------------------------ fake Flow (driver real)

BROWSER = detect_browser()
needs_browser = pytest.mark.skipif(not BROWSER, reason="Chromium/Edge/Chrome não encontrado")


@pytest.fixture(scope="session")
def fake_flow(tmp_path_factory):
    """Servidor HTTP local com a página falsa do Flow e um clipe MP4 para download."""
    if not HAS_FFMPEG:
        pytest.skip("FFmpeg não instalado")
    root = tmp_path_factory.mktemp("fake_flow")
    shutil.copy(ROOT / "tests" / "fake_flow" / "index.html", root / "index.html")
    make_clip(root / "clip.mp4", 4.0, size=(1280, 720))
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    server.shutdown()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # silencia o log do servidor de teste
        pass


@pytest.fixture
def flow_settings(settings, fake_flow):
    from adflow.flow.browser import close_browser

    settings.flow_url = fake_flow
    settings.browser_path = BROWSER
    yield settings
    close_browser(settings)
