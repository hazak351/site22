"""Pipeline completo com SIMULADOR: geração, Quality Gate, regeneração, créditos e recuperação."""

import pytest

from adflow import editor
from adflow.flow import DoctorReport, FlowError
from adflow.flow.simulator import SimulatorEngine
from adflow.models import Asset, Project, ProjectStatus, SceneStatus
from adflow.pipeline import Pipeline, mark_interrupted
from adflow.storage import ProjectStore
from tests.conftest import needs_ffmpeg

pytestmark = needs_ffmpeg

BRIEF = ("Anúncio desse fone preto com detalhes dourados, estilo premium, fundo escuro, "
         "exploded view e final com produto flutuando e logo.")


def make_project(settings, product_image, logo_image=None, **kwargs) -> tuple[ProjectStore, Project]:
    store = ProjectStore(settings)
    fields = dict(brief=BRIEF, duration=10.0, aspect="16:9", quality="equilibrado", product_name="Fone X", cta="Compre agora")
    fields.update(kwargs)
    project = Project(id=store.new_id(), **fields)
    store.save(project)
    rel = store.save_upload(project.id, "product", product_image.name, product_image.read_bytes())
    project.product_assets = [Asset(path=rel, original_name=product_image.name)]
    if logo_image is not None:
        project.logo = Asset(path=store.save_upload(project.id, "brand", "logo.png", logo_image.read_bytes()),
                             original_name="logo.png")
    store.save(project)
    return store, project


def factory_with(engine):
    return lambda name, settings, log=None, **kwargs: engine


def test_end_to_end_simulator(settings, product_image, logo_image):
    store, project = make_project(settings, product_image, logo_image)
    result = Pipeline(settings, store).run(project.id)
    assert result.status == ProjectStatus.done, result.error
    assert all(stage.status == "done" for stage in result.stages)
    assert [s.target_duration for s in result.scenes] == [4.0, 3.0, 3.0]
    assert all(s.status == SceneStatus.approved for s in result.scenes)
    assert result.credits.simulated and result.credits.used == result.credits.estimated
    assert result.environment.reference_image  # Environment Master ganhou imagem de referência
    assert result.scenes[1].start_frame == result.scenes[0].last_frame  # Continuity: último frame → próxima cena
    final = store.project_dir(project.id) / result.final_video
    assert editor.verify_output(settings, final, "16:9", 10.0)["ok"]
    log = (settings.logs_dir / project.id / "project.log").read_text(encoding="utf-8")
    for op in ("product", "direction", "planning", "prompt", "generate", "download", "quality", "ffmpeg", "export"):
        assert f"| {op}" in log, op


def test_regenerates_only_the_failing_scene(settings, product_image):
    store, project = make_project(settings, product_image, duration=8.0)
    engine = SimulatorEngine(settings, behaviors={2: ["bad_env", "ok"]})
    result = Pipeline(settings, store, engine_factory=factory_with(engine)).run(project.id)
    assert result.status == ProjectStatus.done, result.error
    assert [len(s.attempts) for s in result.scenes] == [1, 2]
    first_try = result.scenes[1].attempts[0]
    assert not first_try.quality.passed
    assert any(c.name == "ambiente" and c.status == "falha" for c in first_try.quality.checks)
    assert "CORRECTIONS FOR THIS TAKE" in result.scenes[1].attempts[1].prompt
    assert result.credits.used == 3 * result.credits.credits_per_generation


def test_best_effort_after_attempt_limit(settings, product_image):
    store, project = make_project(settings, product_image, duration=4.0, quality="economico")  # 1 tentativa
    engine = SimulatorEngine(settings, behaviors={1: ["wrong_color"]})
    result = Pipeline(settings, store, engine_factory=factory_with(engine)).run(project.id)
    assert result.status == ProjectStatus.done
    assert result.scenes[0].status == SceneStatus.warning
    assert result.warnings and "alertas" in result.warnings[0]


def test_budget_ceiling_stops_regeneration(settings, product_image):
    per = 20
    store, project = make_project(settings, product_image, duration=8.0, max_credits=2 * per)
    engine = SimulatorEngine(settings, behaviors={2: ["bad_env", "bad_env"]})
    # cena 1 define a referência de ambiente; cena 2 muda o cenário e não pode regenerar: teto = 40
    result = Pipeline(settings, store, engine_factory=factory_with(engine)).run(project.id)
    assert result.credits.used <= result.credits.max_credits == 40
    assert len(result.scenes[1].attempts) == 1
    assert result.status == ProjectStatus.done
    assert result.scenes[1].status == SceneStatus.warning
    assert any("teto de créditos" in w for w in result.warnings)


def test_estimate_above_ceiling_never_starts(settings, product_image):
    store, project = make_project(settings, product_image, duration=8.0, max_credits=10)
    result = Pipeline(settings, store).run(project.id)
    assert result.status == ProjectStatus.failed
    assert "teto" in result.error
    assert result.credits is None or result.credits.used == 0
    assert all(not s.attempts for s in result.scenes)


def test_generation_error_is_retried(settings, product_image):
    store, project = make_project(settings, product_image, duration=4.0)
    engine = SimulatorEngine(settings, behaviors={1: ["error", "ok"]})
    result = Pipeline(settings, store, engine_factory=factory_with(engine)).run(project.id)
    assert result.status == ProjectStatus.done
    assert [a.status for a in result.scenes[0].attempts] == ["falha", "ok"]


def test_real_flow_requires_credit_confirmation(settings, product_image):
    created = []

    def factory(name, settings, log=None, **kwargs):
        created.append(name)
        raise AssertionError("o motor não deveria ser criado")

    store, project = make_project(settings, product_image, duration=4.0, engine="flow")
    result = Pipeline(settings, store, engine_factory=factory).run(project.id)
    assert result.status == ProjectStatus.failed
    assert "Confirme" in result.error
    assert created == [] and result.credits.used == 0


class _BrokenDoctorEngine(SimulatorEngine):
    name = "flow"
    real = True
    generated = 0

    def doctor(self):
        return DoctorReport(engine="flow", real=True, ok=False, failed_step="prompt",
                            message="Campo de prompt não encontrado")

    def generate(self, request):
        self.generated += 1
        raise AssertionError("não pode gerar com o Doctor reprovado")


def test_doctor_failure_spends_no_credits(settings, product_image):
    store, project = make_project(settings, product_image, duration=4.0, engine="flow", credits_confirmed=True)
    engine = _BrokenDoctorEngine(settings)
    result = Pipeline(settings, store, engine_factory=factory_with(engine)).run(project.id)
    assert result.status == ProjectStatus.failed
    assert "nenhum crédito" in result.error
    assert engine.generated == 0 and result.credits.used == 0
    assert result.stage("flow_check").status == "failed"


class Crash(BaseException):
    """Simula o processo morrendo (queda de energia, PC reiniciando)."""


class _CrashingEngine(SimulatorEngine):
    def __init__(self, settings, crash_scene, after_download=False):
        super().__init__(settings)
        self.crash_scene = crash_scene
        self.after_download = after_download
        self.calls = []

    def generate(self, request):
        self.calls.append((request.scene_index, request.attempt))
        if request.scene_index == self.crash_scene:
            if self.after_download:
                super().generate(request)
            raise Crash()
        return super().generate(request)


@pytest.mark.parametrize("after_download", [False, True])
def test_recovery_after_crash(settings, product_image, after_download):
    store, project = make_project(settings, product_image, duration=10.0)
    crashing = _CrashingEngine(settings, crash_scene=2, after_download=after_download)
    with pytest.raises(Crash):
        Pipeline(settings, store, engine_factory=factory_with(crashing)).run(project.id)

    # servidor reinicia: projeto órfão vira "interrompido"
    assert mark_interrupted(store) == [project.id]
    saved = store.load(project.id)
    assert saved.status == ProjectStatus.interrupted
    assert saved.scenes[0].status == SceneStatus.approved
    used_before = saved.credits.used

    fresh = SimulatorEngine(settings)
    calls = []
    original = fresh.generate
    fresh.generate = lambda req: (calls.append((req.scene_index, req.attempt)), original(req))[1]
    result = Pipeline(settings, store, engine_factory=factory_with(fresh)).run(project.id)
    assert result.status == ProjectStatus.done, result.error
    assert all(index != 1 for index, _ in calls)  # cena 1 nunca é regerada
    if after_download:
        # o download completo antes da queda foi reaproveitado: nenhuma nova geração da cena 2
        assert (2, 1) not in calls and all(index != 2 for index, _ in calls)
        assert result.scenes[1].attempts[0].status == "ok"
    else:
        assert result.scenes[1].attempts[0].status == "interrompida"
        assert (2, 2) in calls
    assert result.credits.used >= used_before
    assert editor.verify_output(settings, store.project_dir(project.id) / result.final_video, "16:9", 10.0)["ok"]


def test_missing_scene_file_is_regenerated_on_resume(settings, product_image):
    store, project = make_project(settings, product_image, duration=4.0)
    first = Pipeline(settings, store).run(project.id)
    assert first.status == ProjectStatus.done
    video = store.project_dir(project.id) / first.scenes[0].approved_video()
    video.unlink()
    (store.project_dir(project.id) / first.final_video).unlink()
    first.status = ProjectStatus.interrupted
    store.save(first)
    second = Pipeline(settings, store).run(project.id)
    assert second.status == ProjectStatus.done
    assert len(second.scenes[0].attempts) == 2


def test_cancel(settings, product_image):
    import threading

    store, project = make_project(settings, product_image, duration=4.0)
    cancel = threading.Event()
    cancel.set()
    result = Pipeline(settings, store).run(project.id, cancel)
    assert result.status == ProjectStatus.cancelled
    assert result.credits is None or result.credits.used == 0


def test_flow_error_details_are_saved(settings, product_image):
    class Failing(SimulatorEngine):
        def generate(self, request):
            raise FlowError("upload quebrou", step="upload", selector="css=input[type='file']",
                            url="https://labs.google/fx/tools/flow", retryable=False)

    store, project = make_project(settings, product_image, duration=4.0)
    result = Pipeline(settings, store, engine_factory=factory_with(Failing(settings))).run(project.id)
    assert result.status == ProjectStatus.failed
    err = result.final_report["last_flow_error"]
    assert err["step"] == "upload" and "input" in err["selector"]
    assert result.credits.used == 0  # erro antes de enviar: crédito devolvido na contabilidade
