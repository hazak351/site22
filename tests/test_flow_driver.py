"""Driver REAL (Playwright + CDP + navegador de verdade) contra uma página FALSA do Flow.

Valida a mecânica da automação: perfil persistente, aba própria, CDP, Doctor, upload, prompt,
geração, espera, download e diagnóstico. NÃO valida o DOM do Google Flow real.
"""

import json
import urllib.request

import pytest

from adflow import editor, media
from adflow.flow import FlowError, GenerationRequest
from adflow.flow.browser import cdp_version
from adflow.flow.driver import FlowBrowserEngine
from adflow.models import Asset, Project, ProjectStatus
from adflow.pipeline import Pipeline
from adflow.storage import ProjectStore
from tests.conftest import needs_browser, needs_ffmpeg

pytestmark = [needs_browser, needs_ffmpeg]


def _log(engine):
    return engine.page.evaluate("window.__adflow_log")


def test_doctor_ok_on_fake_flow(flow_settings):
    engine = FlowBrowserEngine(flow_settings)
    try:
        report = engine.doctor()
        assert cdp_version(flow_settings.cdp_port)  # navegador com CDP ativo
        assert flow_settings.profile_dir.is_dir()  # perfil persistente dedicado
        checks = {c.key: c for c in report.checks}
        assert report.ok, report.message
        for key in ("browser", "session", "flow", "prompt", "upload", "controls", "generate"):
            assert checks[key].ok, key
        assert not checks["results"].ok and not checks["results"].critical  # projeto novo ainda sem vídeos
        assert "Prompt" in checks["prompt"].selector or "prompt" in checks["prompt"].selector
        assert _log(engine)["generations"] == 0  # Doctor nunca clica em gerar
    finally:
        engine.close()


def test_doctor_uses_its_own_tab(flow_settings):
    from playwright.sync_api import sync_playwright

    from adflow.flow.browser import launch_browser

    launch_browser(flow_settings)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{flow_settings.cdp_port}")
        user_tab = browser.contexts[0].pages[0]
        user_tab.goto("data:text/html,<title>aba do usuario</title>")
        engine = FlowBrowserEngine(flow_settings)
        try:
            engine.doctor()
            assert user_tab.title() == "aba do usuario"  # aba existente intocada
            assert engine.page != user_tab
        finally:
            engine.close()
            browser.close()


def test_doctor_detects_missing_prompt_and_saves_diagnostics(flow_settings, fake_flow):
    flow_settings.flow_url = fake_flow + "?broken=1"
    engine = FlowBrowserEngine(flow_settings)
    try:
        report = engine.doctor()
    finally:
        engine.close()
    assert not report.ok
    assert report.failed_step == "prompt"
    failed = next(c for c in report.checks if c.key == "prompt")
    assert "textarea" in failed.selector  # mostra o que foi procurado
    assert report.screenshot and media.kind_of(report.screenshot) == "image"
    assert report.html_snippet and "Add image" in report.html_snippet
    assert report.url.startswith("http://127.0.0.1")


def test_doctor_detects_expired_login(flow_settings, fake_flow):
    flow_settings.flow_url = fake_flow + "?login=1"
    engine = FlowBrowserEngine(flow_settings)
    try:
        report = engine.doctor()
    finally:
        engine.close()
    assert not report.ok and report.failed_step == "session"
    assert "login" in report.message.lower()


def test_generate_upload_prompt_download(flow_settings, product_image, tmp_path):
    engine = FlowBrowserEngine(flow_settings)
    try:
        out = tmp_path / "scene.mp4"
        request = GenerationRequest(project_id="t", scene_index=1, attempt=1, prompt="PRODUCT LOCK test prompt " * 5,
                                    aspect="9:16", duration=4.0, output_path=out, reference_images=[product_image],
                                    model_match="fast")
        engine.generate(request)
        assert media.video_info(flow_settings, out)["duration"] > 3
        log = _log(engine)
        assert log["prompts"] == [request.prompt]
        assert log["uploads"] == [product_image.name]
        assert log["settings"]["aspect"] == "Portrait (9:16)"
        assert log["settings"]["model"] == "Veo 3 - Fast"
        assert log["settings"]["outputs"] == "1"
        assert log["settings"]["mode"] == "Ingredients to Video"

        # segunda geração: baixa o vídeo NOVO, não o antigo; usa o último frame como início
        out2 = tmp_path / "scene2.mp4"
        frame = media.extract_frame(flow_settings, out, 3.5, tmp_path / "last.png")
        engine.generate(GenerationRequest(project_id="t", scene_index=2, attempt=1, prompt="second shot " * 5,
                                          aspect="9:16", duration=4.0, output_path=out2,
                                          reference_images=[product_image], start_frame=frame))
        assert out2.is_file()
        log = _log(engine)
        assert log["generations"] == 2 and log["settings"]["mode"] == "Frames to Video"
        assert log["uploads"][-1] == "last.png"
    finally:
        engine.close()


def test_generation_error_is_reported(flow_settings, fake_flow, product_image, tmp_path):
    flow_settings.flow_url = fake_flow + "?fail=1&delay=200"
    engine = FlowBrowserEngine(flow_settings)
    try:
        with pytest.raises(FlowError) as info:
            engine.generate(GenerationRequest(project_id="t", scene_index=1, attempt=1, prompt="p " * 30,
                                              aspect="16:9", duration=4.0, output_path=tmp_path / "x.mp4"))
    finally:
        engine.close()
    assert "Something went wrong" in str(info.value)
    assert info.value.retryable and info.value.credits_spent
    assert info.value.screenshot


def test_full_pipeline_with_browser_driver(flow_settings, product_image, logo_image):
    store = ProjectStore(flow_settings)
    project = Project(id=store.new_id(), brief="fone premium fundo escuro com final flutuando", duration=6.0,
                      aspect="9:16", quality="equilibrado", engine="flow", credits_confirmed=True, cta="Compre agora")
    store.save(project)
    project.product_assets = [Asset(path=store.save_upload(project.id, "product", "fone.png", product_image.read_bytes()),
                                    original_name="fone.png")]
    project.logo = Asset(path=store.save_upload(project.id, "brand", "logo.png", logo_image.read_bytes()), original_name="logo.png")
    store.save(project)
    result = Pipeline(flow_settings, store).run(project.id)
    assert result.status == ProjectStatus.done, (result.error, result.final_report.get("last_flow_error"))
    assert result.final_report["doctor"]["real"] is True
    assert not result.credits.simulated and result.credits.used == result.credits.estimated
    final = store.project_dir(project.id) / result.final_video
    assert editor.verify_output(flow_settings, final, "9:16", 6.0)["ok"]
    events = [json.loads(line) for line in (flow_settings.logs_dir / project.id / "events.jsonl").read_text().splitlines()]
    assert any(e["op"] == "flow" and "consome créditos" in e["message"] for e in events)


def test_cdp_only_listens_locally(flow_settings):
    from adflow.flow.browser import launch_browser

    info = launch_browser(flow_settings)
    assert "webSocketDebuggerUrl" in info
    assert "127.0.0.1" in info["webSocketDebuggerUrl"] or "localhost" in info["webSocketDebuggerUrl"]
    with urllib.request.urlopen(f"http://127.0.0.1:{flow_settings.cdp_port}/json/version", timeout=2) as resp:
        assert resp.status == 200
