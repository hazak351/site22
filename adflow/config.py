"""Configuração central.

Valores padrão ficam aqui; o usuário sobrescreve em ``config/settings.json``.
Variáveis de ambiente (usadas principalmente nos testes) têm prioridade:
ADFLOW_DATA_DIR, ADFLOW_LOGS_DIR, ADFLOW_CONFIG_DIR, ADFLOW_BROWSER_PATH.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

WINDOWS_BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
MAC_BROWSERS = [
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]
LINUX_BROWSERS = ["microsoft-edge", "microsoft-edge-stable", "google-chrome", "chromium", "chromium-browser"]

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def detect_browser() -> str | None:
    """Encontra Edge ou Chrome instalado (Edge primeiro no Windows)."""
    env = os.environ.get("ADFLOW_BROWSER_PATH")
    if env:
        return env
    if sys.platform.startswith("win"):
        candidates = WINDOWS_BROWSERS
    elif sys.platform == "darwin":
        candidates = MAC_BROWSERS
    else:
        candidates = [shutil.which(name) or "" for name in LINUX_BROWSERS] + ["/opt/pw-browsers/chromium"]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    return None


def detect_tool(name: str) -> str | None:
    """Procura ffmpeg/ffprobe: tools/ffmpeg/bin, PATH e instalação do winget."""
    exe = name + (".exe" if sys.platform.startswith("win") else "")
    local = BASE_DIR / "tools" / "ffmpeg" / "bin" / exe
    if local.is_file():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    if sys.platform.startswith("win"):
        localappdata = os.environ.get("LOCALAPPDATA", "")
        patterns = [
            os.path.join(localappdata, "Microsoft", "WinGet", "Links", exe),
            os.path.join(localappdata, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", exe),
            os.path.join(r"C:\ffmpeg", "bin", exe),
        ]
        for pattern in patterns:
            for match in glob.glob(pattern):
                return match
    return None


def detect_font() -> str | None:
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


@dataclass
class Settings:
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    logs_dir: Path = BASE_DIR / "logs"
    config_dir: Path = BASE_DIR / "config"

    host: str = "127.0.0.1"
    port: int = 8765
    open_browser_on_start: bool = True

    # Google Flow
    flow_url: str = "https://labs.google/fx/tools/flow"
    browser_path: str | None = None
    browser_headless: bool = False
    cdp_port: int = 9333
    generation_timeout_s: int = 900
    ui_timeout_ms: int = 15000
    outputs_per_prompt: int = 1
    doctor_strict: bool = False  # True: resultados/download também bloqueiam a geração
    use_last_frame_as_start: bool = True  # continuidade: último frame da cena N inicia a cena N+1
    square_generation_aspect: str = "16:9"  # o Flow gera 16:9/9:16; 1:1 é recortado na edição
    simulator_delay_s: float = 0.0

    # Ferramentas
    ffmpeg: str | None = None
    ffprobe: str | None = None
    font_path: str | None = None

    # Planejamento: durações que o Flow gera por clipe (o editor corta depois)
    generation_durations: list[float] = field(default_factory=lambda: [8.0])
    min_scene_duration: float = 2.0

    # Edição
    fps: int = 30
    transition_duration: float = 0.4
    final_preset: str = "medium"  # x264: medium = boa qualidade; ultrafast só para testes
    normalize_preset: str = "veryfast"
    fit_mode: str = "crop"  # crop | blur

    # Quality Gate (0..1; quanto maior, mais exigente)
    qg_environment_min: float = 0.55
    qg_continuity_min: float = 0.45
    qg_product_color_min: float = 0.25
    qg_max_black_ratio: float = 0.3
    qg_max_freeze_ratio: float = 0.6

    # Recuperação: se uma cena falha em todas as tentativas, usa a melhor tentativa
    on_quality_fail: str = "best_effort"  # best_effort | stop

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def profile_dir(self) -> Path:
        """Perfil persistente do navegador de automação (NUNCA versionar)."""
        return self.data_dir / "browser-profile"

    @property
    def diagnostics_dir(self) -> Path:
        return self.data_dir / "diagnostics"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.projects_dir, self.logs_dir, self.diagnostics_dir):
            path.mkdir(parents=True, exist_ok=True)

    def resolve_tools(self) -> None:
        self.ffmpeg = self.ffmpeg or detect_tool("ffmpeg")
        self.ffprobe = self.ffprobe or detect_tool("ffprobe")
        self.font_path = self.font_path or detect_font()
        self.browser_path = self.browser_path or detect_browser()


def load_settings(**overrides) -> Settings:
    """Carrega defaults + config/settings.json + variáveis de ambiente + overrides."""
    settings = Settings()
    if os.environ.get("ADFLOW_CONFIG_DIR"):
        settings.config_dir = Path(os.environ["ADFLOW_CONFIG_DIR"])
    user_file = settings.config_dir / "settings.json"
    known = {f.name for f in fields(Settings)}
    if user_file.is_file():
        data = json.loads(user_file.read_text(encoding="utf-8"))
        for key, value in data.items():
            if key in known and not key.startswith("_"):
                setattr(settings, key, value)
    if os.environ.get("ADFLOW_DATA_DIR"):
        settings.data_dir = Path(os.environ["ADFLOW_DATA_DIR"])
    if os.environ.get("ADFLOW_LOGS_DIR"):
        settings.logs_dir = Path(os.environ["ADFLOW_LOGS_DIR"])
    for key, value in overrides.items():
        setattr(settings, key, value)
    for name in ("base_dir", "data_dir", "logs_dir", "config_dir"):
        setattr(settings, name, Path(getattr(settings, name)))
    settings.resolve_tools()
    return settings
