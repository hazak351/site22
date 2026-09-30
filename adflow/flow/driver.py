"""Google Flow REAL via navegador (Playwright + CDP).

ATENÇÃO: os seletores em config/flow_selectors.json são candidatos e NÃO foram validados no
Flow real. O Flow Doctor verifica cada elemento antes de qualquer gasto de créditos; se algo
não for encontrado, a geração não começa e o diagnóstico mostra o que ajustar.
"""

from __future__ import annotations

import base64
import re
import time
from datetime import datetime
from pathlib import Path

from .. import media
from ..config import Settings
from .base import DoctorCheck, DoctorReport, FlowError, GenerationRequest, GenerationResult
from .browser import BrowserSession
from .locator import any_text, find, load_selectors, rx

CRITICAL = {"browser", "session", "flow", "prompt", "upload", "generate"}


class FlowBrowserEngine:
    name = "flow"
    real = True

    def __init__(self, settings: Settings, log=None):
        self.settings = settings
        self.log = log
        self.sel = load_selectors(settings.config_dir)
        self.session = BrowserSession(settings, log)
        self.page = None
        self.flow_project_url: str | None = None

    # ------------------------------------------------------------------ utilidades

    def _log(self, level: str, op: str, message: str, **data) -> None:
        if self.log:
            getattr(self.log, level)(op, message, **data)

    def _ensure_page(self):
        if self.page is None or self.page.is_closed():
            self.page = self.session.open()
        return self.page

    def _diagnose(self, step: str) -> tuple[str | None, str | None]:
        """Screenshot + HTML da página (salvos localmente em data/diagnostics)."""
        page = self.page
        if page is None or page.is_closed():
            return None, None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        folder = self.settings.diagnostics_dir
        folder.mkdir(parents=True, exist_ok=True)
        shot = folder / f"{stamp}_{step}.png"
        html_path = folder / f"{stamp}_{step}.html"
        try:
            page.screenshot(path=str(shot), full_page=False)
        except Exception:
            shot = None
        snippet = None
        try:
            html = page.content()
            html_path.write_text(html, encoding="utf-8")
            body = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S | re.I)
            snippet = re.sub(r"\s+", " ", body)[:3000]
        except Exception:
            pass
        return (str(shot) if shot else None), snippet

    def _fail(self, message: str, step: str, selector: str = "", retryable: bool = False,
              credits_spent: bool = False) -> FlowError:
        shot, snippet = self._diagnose(step)
        url = self.page.url if self.page is not None and not self.page.is_closed() else ""
        self._log("error", "flow", message, step=step, selector=selector, url=url, screenshot=shot)
        return FlowError(message, step=step, selector=selector, url=url, screenshot=shot, html=snippet,
                         retryable=retryable, credits_spent=credits_spent)

    def _login_required(self, page) -> bool:
        if any(marker in page.url for marker in self.sel.get("login_url_markers", [])):
            return True
        locator, _ = find(page, self.sel["login"], timeout_ms=1500)
        return locator is not None

    def _open_flow(self, page) -> None:
        target = self.flow_project_url or self.settings.flow_url
        if not page.url.startswith(target):
            page.goto(target, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

    def _ensure_editor(self, page, allow_create: bool = True):
        prompt, desc = find(page, self.sel["prompt"], timeout_ms=5000)
        if prompt is not None or not allow_create:
            return prompt, desc
        button, _ = find(page, self.sel["new_project"], timeout_ms=5000)
        if button is None:
            return None, desc
        button.click()
        prompt, desc = find(page, self.sel["prompt"], timeout_ms=self.settings.ui_timeout_ms)
        if prompt is not None:
            self.flow_project_url = page.url
            self._log("info", "flow", "projeto do Flow aberto", url=page.url)
        return prompt, desc

    # ------------------------------------------------------------------ Flow Doctor

    def doctor(self, allow_create_project: bool = True) -> DoctorReport:
        report = DoctorReport(engine=self.name, real=True)
        checks = report.checks

        def add(key: str, label: str, ok: bool, detail: str = "", selector: str = "") -> bool:
            checks.append(DoctorCheck(key=key, label=label, ok=ok, critical=key in CRITICAL,
                                      detail=detail, selector=selector))
            self._log("info" if ok else "warn", "doctor", f"{label}: {'OK' if ok else 'FALHOU'}",
                      detail=detail, selector=selector)
            return ok

        try:
            page = self._ensure_page()
            add("browser", "Navegador conectado", True, self.session.version.get("Browser", ""))
            self._open_flow(page)
            report.url = page.url
            if self._login_required(page):
                add("session", "Sessão Google válida", False,
                    "Login necessário: faça login (e 2FA) na janela do navegador AdFlow e rode o Doctor de novo.")
                return self._finish(report, keep_page=True)
            add("session", "Sessão Google válida", True)
            add("flow", "Flow abriu", "labs.google" in page.url or page.url.startswith(self.settings.flow_url), page.url)

            prompt, desc = self._ensure_editor(page, allow_create=allow_create_project)
            add("prompt", "Campo do Agent/prompt encontrado", prompt is not None, "", desc)
            report.url = page.url

            file_input, desc_in = find(page, self.sel["file_input"], timeout_ms=1500)
            trigger, desc_tr = find(page, self.sel["upload_trigger"], timeout_ms=1500)
            add("upload", "Upload encontrado", file_input is not None or trigger is not None, "",
                desc_in if file_input is not None else desc_tr)

            found = []
            for key in ("mode_menu", "settings_button", "aspect_control", "model_control"):
                loc, _ = find(page, self.sel[key], timeout_ms=800)
                if loc is not None:
                    found.append(key)
            add("controls", "Controles principais encontrados", bool(found),
                "encontrados: " + (", ".join(found) or "nenhum"))

            generate, desc_gen = find(page, self.sel["generate"], timeout_ms=3000)
            add("generate", "Botão/menu de geração encontrado", generate is not None, "", desc_gen)

            results, desc_res = find(page, self.sel["results"], timeout_ms=1500)
            add("results", "Área de resultados identificável", results is not None,
                "" if results is not None else "nenhum vídeo no projeto ainda (verificado na primeira geração)", desc_res)

            download_ok, detail = False, "sem resultado existente para testar"
            if results is not None:
                try:
                    results.hover()
                    dl, desc_dl = find(page, self.sel["download"], timeout_ms=2500)
                    download_ok = dl is not None
                    detail = desc_dl
                except Exception as exc:
                    detail = f"erro ao testar: {exc}"
            add("download", "Mecanismo de download identificado", download_ok, detail)
        except FlowError as exc:
            add(exc.step or "browser", "Navegador conectado" if exc.step == "browser" else exc.step, False, str(exc))
        except Exception as exc:  # erro inesperado de automação
            add("browser", "Automação do navegador", False, f"{type(exc).__name__}: {exc}")
        return self._finish(report)

    def _finish(self, report: DoctorReport, keep_page: bool = False) -> DoctorReport:
        strict = getattr(self.settings, "doctor_strict", False)
        failed = [c for c in report.checks if not c.ok and (strict or c.critical)]
        report.ok = not failed
        if failed:
            first = failed[0]
            report.failed_step = first.key
            report.message = f"Falhou em '{first.label}'. {first.detail}".strip()
            report.screenshot, report.html_snippet = self._diagnose(f"doctor_{first.key}")
        else:
            warnings = [c.label for c in report.checks if not c.ok]
            report.message = "Flow pronto para gerar." + (f" Avisos: {', '.join(warnings)}." if warnings else "")
        if self.page is not None and not self.page.is_closed():
            report.url = self.page.url
        if keep_page:
            self.session.close(keep_page=True)
            self.page = None
        return report

    # ------------------------------------------------------------------ geração

    def _select_option(self, page, control_key: str, option_regex: str, label: str) -> bool:
        control, _ = find(page, self.sel.get(control_key, []), timeout_ms=1500)
        if control is None:
            settings_btn, _ = find(page, self.sel["settings_button"], timeout_ms=1000)
            if settings_btn is not None:
                settings_btn.click()
                control, _ = find(page, self.sel.get(control_key, []), timeout_ms=2000)
        if control is None:
            self._log("warn", "flow", f"controle '{label}' não encontrado; mantendo padrão do Flow")
            return False
        try:
            if option_regex and rx(option_regex).search(control.inner_text(timeout=1000) or ""):
                return True
            control.click()
            for role in ("option", "menuitem", "menuitemradio", "radio", "button"):
                option = page.get_by_role(role, name=rx(option_regex))
                if option.count() and option.first.is_visible():
                    option.first.click()
                    self._log("info", "flow", f"{label} selecionado", option=option_regex)
                    return True
            page.keyboard.press("Escape")
        except Exception as exc:
            self._log("warn", "flow", f"falha ao ajustar {label}: {exc}")
        return False

    def _upload(self, page, files: list[Path], slot_key: str | None = None) -> bool:
        files = [Path(f) for f in files if Path(f).is_file()]
        if not files:
            return True
        paths = [str(f) for f in files]
        triggers = (self.sel.get(slot_key, []) if slot_key else []) + self.sel["upload_trigger"]
        trigger, _ = find(page, triggers, timeout_ms=2000)
        if trigger is not None:
            try:
                with page.expect_file_chooser(timeout=4000) as chooser:
                    trigger.click()
                chooser.value.set_files(paths)
                self._after_upload(page)
                return True
            except Exception:
                pass
        file_input, _ = find(page, self.sel["file_input"], timeout_ms=3000)
        if file_input is None:
            return False
        file_input.set_input_files(paths)
        self._after_upload(page)
        return True

    def _after_upload(self, page) -> None:
        confirm, _ = find(page, self.sel["upload_confirm"], timeout_ms=2500)
        if confirm is not None:
            try:
                confirm.click()
            except Exception:
                pass
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:  # espera barras de progresso do upload sumirem
            busy, _ = find(page, self.sel["progress"][:1], timeout_ms=300)
            if busy is None:
                return
            time.sleep(0.5)

    _SRC_JS = "e => e.currentSrc || e.src || (e.querySelector('source') || {}).src || ''"

    def _video_sources(self, page) -> list[str]:
        try:
            return page.eval_on_selector_all("video", f"els => els.map({self._SRC_JS})")
        except Exception:
            return []

    def _video_locator(self, page, src: str):
        try:
            index = page.eval_on_selector_all("video", f"(els, s) => els.map({self._SRC_JS}).indexOf(s)", src)
        except Exception:
            index = -1
        videos = page.locator("video")
        return videos.nth(index) if index >= 0 else videos.first

    def generate(self, request: GenerationRequest) -> GenerationResult:
        page = self._ensure_page()
        self._open_flow(page)
        if self._login_required(page):
            raise self._fail("Sessão do Google expirou: faça login na janela do navegador AdFlow.", "session")
        prompt, desc = self._ensure_editor(page)
        if prompt is None:
            raise self._fail("Campo de prompt não encontrado (a interface do Flow pode ter mudado).", "prompt", desc)

        # modo: primeiro frame (continuidade) ou imagens do produto (ingredientes)
        use_frames = request.start_frame is not None and Path(request.start_frame).is_file()
        modes = self.sel.get("mode_options", {})
        if use_frames:
            use_frames = self._select_option(page, "mode_menu", modes.get("frames", ""), "modo Frames")
        if not use_frames and request.reference_images:
            self._select_option(page, "mode_menu", modes.get("ingredients", ""), "modo Ingredientes")
        self._select_option(page, "aspect_control", self.sel["aspect_options"].get(request.aspect, request.aspect), "proporção")
        if request.model_match:
            self._select_option(page, "model_control", f"(?i){request.model_match}", "modelo")
        self._select_option(page, "outputs_control", f"^{self.settings.outputs_per_prompt}$", "saídas por prompt")

        if use_frames:
            uploaded = self._upload(page, [request.start_frame], "start_frame_slot")
        else:
            uploaded = self._upload(page, request.reference_images[:3])
        if not uploaded:
            raise self._fail("Não foi possível enviar as imagens de referência.", "upload")

        prompt, desc = find(page, self.sel["prompt"], timeout_ms=5000)
        if prompt is None:
            raise self._fail("Campo de prompt sumiu após o upload.", "prompt", desc)
        prompt.click()
        prompt.fill(request.prompt)
        typed = prompt.input_value() if prompt.evaluate("e => 'value' in e") else prompt.inner_text()
        if request.prompt[:40] not in (typed or ""):
            raise self._fail("O prompt não foi preenchido corretamente.", "prompt", desc)

        before = set(self._video_sources(page))
        generate, desc_gen = find(page, self.sel["generate"], timeout_ms=5000)
        if generate is None:
            raise self._fail("Botão de geração não encontrado.", "generate", desc_gen)
        if not generate.is_enabled():
            raise self._fail("Botão de geração desabilitado (prompt ou upload pendente?).", "generate", desc_gen)
        self._log("info", "flow", "enviando geração (consome créditos)", scene=request.scene_index,
                  attempt=request.attempt, aspect=request.aspect)
        generate.click()

        new_src = self._wait_result(page, before)
        self._download(page, new_src, request.output_path)
        return GenerationResult(video_path=request.output_path,
                                meta={"engine": "flow", "flow_project_url": self.flow_project_url, "src": new_src[:200]})

    def _wait_result(self, page, before: set[str]) -> str:
        deadline = time.monotonic() + self.settings.generation_timeout_s
        while time.monotonic() < deadline:
            error = any_text(page, self.sel.get("error_texts", []))
            if error:
                no_credit = any_text(page, self.sel.get("no_credit_texts", []))
                raise self._fail(f"O Flow informou: {error}", "generation", retryable=not no_credit,
                                 credits_spent=True)
            fresh = [s for s in self._video_sources(page) if s and s not in before]
            if fresh:
                busy, _ = find(page, self.sel["progress"], timeout_ms=200)
                if busy is None:
                    return fresh[0]
            time.sleep(2)
        raise self._fail("Tempo esgotado esperando o resultado do Flow.", "generation_timeout",
                         retryable=True, credits_spent=True)

    def _download(self, page, src: str, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        # 1) botão de download da interface — procurado primeiro DENTRO do card do vídeo novo
        try:
            target = self._video_locator(page, src)
            target.hover()
            button = None
            container = target
            for _ in range(4):
                container = container.locator("xpath=..")
                button, _ = find(container, self.sel["download"], timeout_ms=300)
                if button is not None:
                    break
            if button is None:
                button, _ = find(page, self.sel["download"], timeout_ms=3000)
            if button is not None:
                with page.expect_download(timeout=120000) as download_info:
                    button.click()
                    option, _ = find(page, self.sel["download_option"], timeout_ms=2500)
                    if option is not None:
                        option.click()
                download_info.value.save_as(str(output))
                self._verify_download(output)
                self._log("info", "download", "download pela interface", file=output.name)
                return
        except FlowError:
            raise
        except Exception as exc:
            self._log("warn", "download", f"download pela interface falhou, tentando pela URL do vídeo: {exc}")
        # 2) URL do vídeo (http ou blob) com a própria sessão do navegador
        try:
            if src.startswith("http"):
                response = page.context.request.get(src, timeout=120000)
                if not response.ok:
                    raise RuntimeError(f"HTTP {response.status}")
                output.write_bytes(response.body())
            else:
                data = page.evaluate(
                    """async (src) => { const r = await fetch(src); const b = await r.blob();
                       return await new Promise(res => { const fr = new FileReader();
                       fr.onload = () => res(fr.result.split(',')[1]); fr.readAsDataURL(b); }); }""", src)
                output.write_bytes(base64.b64decode(data))
            self._verify_download(output)
            self._log("info", "download", "download pela URL do vídeo", file=output.name)
        except FlowError:
            raise
        except Exception as exc:
            raise self._fail(f"Download falhou: {exc}", "download", retryable=False, credits_spent=True) from exc

    def _verify_download(self, output: Path) -> None:
        if not output.is_file() or output.stat().st_size < 1000:
            raise self._fail("Arquivo baixado vazio ou incompleto.", "download", credits_spent=True)
        try:
            media.video_info(self.settings, output)
        except Exception as exc:
            raise self._fail(f"Arquivo baixado não é um vídeo válido: {exc}", "download", credits_spent=True) from exc

    def close(self) -> None:
        self.session.close()
        self.page = None
