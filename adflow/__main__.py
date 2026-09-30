"""Linha de comando.

  python -m adflow            inicia o servidor e abre a interface
  python -m adflow check      verifica Python, dependências, FFmpeg, fonte e navegador
  python -m adflow doctor     roda o Flow Doctor no Flow REAL (não gasta créditos)
  python -m adflow login      abre o navegador de automação no Flow para você fazer login
"""

from __future__ import annotations

import argparse
import importlib
import sys
import threading
import webbrowser

from .config import load_settings


def cmd_check() -> int:
    settings = load_settings()
    ok = True
    print(f"Python {sys.version.split()[0]}")
    for module in ("fastapi", "uvicorn", "pydantic", "multipart", "playwright", "PIL"):
        try:
            mod = importlib.import_module(module)
            print(f"  [ok] {module} {getattr(mod, '__version__', '')}")
        except Exception as exc:
            ok = False
            print(f"  [ERRO] {module}: {exc}")
    for label, value in (("ffmpeg", settings.ffmpeg), ("ffprobe", settings.ffprobe)):
        print(f"  [{'ok' if value else 'ERRO'}] {label}: {value or 'não encontrado (winget install Gyan.FFmpeg)'}")
        ok = ok and bool(value)
    print(f"  [{'ok' if settings.font_path else 'aviso'}] fonte para textos: {settings.font_path or 'não encontrada'}")
    print(f"  [{'ok' if settings.browser_path else 'aviso'}] navegador: {settings.browser_path or 'Edge/Chrome não encontrado'}")
    return 0 if ok else 1


def cmd_doctor() -> int:
    from .flow import create_engine
    from .logs import ProjectLog

    settings = load_settings()
    settings.ensure_dirs()
    engine = create_engine("flow", settings, log=ProjectLog(settings.logs_dir, "_doctor"))
    try:
        report = engine.doctor()
    finally:
        engine.close()
    print("FLOW DOCTOR (Flow REAL) —", "OK" if report.ok else "FALHOU")
    for check in report.checks:
        mark = "ok" if check.ok else ("ERRO" if check.critical else "aviso")
        print(f"  [{mark}] {check.label} {('- ' + check.detail) if check.detail else ''}"
              f"{(' [' + check.selector + ']') if check.selector and not check.ok else ''}")
    print(report.message)
    if report.url:
        print("URL:", report.url)
    if report.screenshot:
        print("Screenshot:", report.screenshot)
    return 0 if report.ok else 1


def cmd_login() -> int:
    from .flow.browser import launch_browser

    settings = load_settings()
    settings.ensure_dirs()
    launch_browser(settings, start_url=settings.flow_url)
    print("Navegador de automação aberto. Faça login no Google (e 2FA) nessa janela e depois rode o Flow Doctor.")
    print("O AdFlow não vê nem salva sua senha; a sessão fica no perfil local data/browser-profile.")
    return 0


def cmd_serve() -> int:
    import uvicorn

    from .api import create_app

    settings = load_settings()
    app = create_app(settings)
    url = f"http://{settings.host}:{settings.port}"
    print(f"AdFlow Studio Auto rodando em {url}  (Ctrl+C para parar)")
    if settings.open_browser_on_start:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="adflow", description="AdFlow Studio Auto")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "check", "doctor", "login"])
    args = parser.parse_args(argv)
    return {"serve": cmd_serve, "check": cmd_check, "doctor": cmd_doctor, "login": cmd_login}[args.command]()


if __name__ == "__main__":
    sys.exit(main())
