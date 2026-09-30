"""Navegador de automação: Edge/Chrome com perfil persistente próprio, controlado via CDP.

- Usa um perfil DEDICADO em data/browser-profile (nunca o perfil principal do usuário).
- O login Google é feito pelo próprio usuário nessa janela; nenhuma senha é salva pelo AdFlow.
- O navegador roda desacoplado do servidor: se o AdFlow reiniciar, a sessão continua aberta.
- O AdFlow sempre abre a SUA PRÓPRIA aba; nunca reutiliza a primeira aba existente.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from ..config import Settings
from .base import FlowError


def cdp_version(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def launch_browser(settings: Settings, log=None, start_url: str = "about:blank") -> dict:
    """Garante um navegador com CDP ativo na porta configurada. Devolve /json/version."""
    info = cdp_version(settings.cdp_port)
    if info:
        return info
    path = settings.browser_path
    if not path or not Path(path).is_file():
        raise FlowError(
            "Navegador não encontrado. Instale o Microsoft Edge ou Google Chrome, ou defina 'browser_path' "
            "em config/settings.json.", step="browser")
    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        path,
        f"--remote-debugging-port={settings.cdp_port}",
        f"--user-data-dir={settings.profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    if getattr(settings, "browser_headless", False):
        args.append("--headless=new")
    if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")
    args.append(start_url)
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    if log:
        log.info("browser", "abrindo navegador de automação", browser=Path(path).name, port=settings.cdp_port)
    subprocess.Popen(args, **kwargs)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        info = cdp_version(settings.cdp_port)
        if info:
            return info
        time.sleep(0.3)
    raise FlowError(
        f"O navegador abriu mas a porta CDP {settings.cdp_port} não respondeu. Feche janelas antigas do "
        "navegador de automação e tente de novo.", step="browser")


def close_browser(settings: Settings) -> bool:
    """Fecha o navegador de automação (se estiver aberto na porta CDP configurada)."""
    if not cdp_version(settings.cdp_port):
        return False
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{settings.cdp_port}")
        try:
            browser.new_browser_cdp_session().send("Browser.close")
        except Exception:
            pass
    return True


class BrowserSession:
    """Conexão Playwright via CDP com uma aba própria do AdFlow."""

    def __init__(self, settings: Settings, log=None):
        self.settings = settings
        self.log = log
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.version: dict = {}

    def open(self):
        from playwright.sync_api import sync_playwright

        self.version = launch_browser(self.settings, self.log)
        self._pw = sync_playwright().start()
        try:
            self.browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.settings.cdp_port}")
        except Exception as exc:
            self._pw.stop()
            self._pw = None
            raise FlowError(f"Falha ao conectar via CDP: {exc}", step="browser") from exc
        self.context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        self.page = self.context.new_page()  # aba própria do AdFlow
        self.page.set_default_timeout(self.settings.ui_timeout_ms)
        if self.log:
            self.log.info("browser", "conectado via CDP", browser=self.version.get("Browser", "?"))
        return self.page

    def close(self, keep_page: bool = False) -> None:
        try:
            if self.page is not None and not keep_page and not self.page.is_closed():
                self.page.close()
        except Exception:
            pass
        try:
            if self.browser is not None:
                self.browser.close()  # em conexões CDP apenas desconecta; o navegador continua aberto
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self.browser = self.context = self.page = None
