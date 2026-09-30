"""Quality Gate: técnico, cor do produto, ambiente, continuidade e honestidade sobre o que não é avaliado."""

from adflow import media, quality
from adflow.models import Scene
from tests.conftest import draw_earbud, needs_ffmpeg

pytestmark = needs_ffmpeg


def _scene(target=3.0):
    return Scene(index=2, beat="orbit", target_duration=target, generation_duration=4.0)


def _evaluate(settings, video, tmp_path, product_images=(), env_ref=None, prev_last=None, name="q"):
    scene = _scene()
    frames = quality.extract_scene_frames(settings, video, scene, tmp_path / name)
    return quality.evaluate_scene(settings, video, scene, frames, list(product_images), env_ref, prev_last)


def _status(report, name):
    return next(c.status for c in report.checks if c.name == name)


def test_good_clip_passes_and_semantics_are_not_faked(settings, clip_factory, tmp_path, product_image):
    ref_clip = clip_factory(4.0)
    env_ref = media.extract_frame(settings, ref_clip, 2.0, tmp_path / "env.png")
    report = _evaluate(settings, clip_factory(4.0), tmp_path, [product_image], env_ref, env_ref)
    assert report.passed, [(c.name, c.status, c.detail) for c in report.checks]
    for name, _ in quality.SEMANTIC_CHECKS:
        assert _status(report, name) == "nao_avaliado"


def test_black_clip_fails(settings, clip_factory, tmp_path):
    report = _evaluate(settings, clip_factory(4.0, color="black", freeze=True, product_box=False), tmp_path)
    assert not report.passed
    assert _status(report, "tela_preta") == "falha"
    assert quality.FIXES["tela_preta"] in report.fixes


def test_frozen_clip_fails(settings, clip_factory, tmp_path):
    report = _evaluate(settings, clip_factory(4.0, freeze=True), tmp_path)
    assert _status(report, "congelado") == "falha"


def test_environment_change_fails(settings, clip_factory, tmp_path):
    env_ref = media.extract_frame(settings, clip_factory(4.0), 2.0, tmp_path / "env.png")
    wood = clip_factory(4.0, color="0x8a5a2b")  # "mesa de madeira"
    report = _evaluate(settings, wood, tmp_path, env_ref=env_ref)
    assert _status(report, "ambiente") == "falha"
    assert quality.FIXES["ambiente"] in report.fixes


def test_product_color_drift_fails(settings, clip_factory, tmp_path):
    blue_product = draw_earbud(tmp_path / "azul.png", body=(20, 40, 200), accent=(20, 40, 200))
    report = _evaluate(settings, clip_factory(4.0), tmp_path, [blue_product])  # clipe tem detalhe dourado
    assert _status(report, "cor_produto") == "falha"


def test_achromatic_product_color_not_faked(settings, clip_factory, tmp_path):
    black_only = draw_earbud(tmp_path / "preto.png", accent=(15, 15, 15))
    report = _evaluate(settings, clip_factory(4.0), tmp_path, [black_only])
    assert _status(report, "cor_produto") == "nao_avaliado"


def test_too_short_clip_fails_duration(settings, clip_factory, tmp_path):
    report = _evaluate(settings, clip_factory(1.5), tmp_path)
    assert _status(report, "duracao") == "falha"
