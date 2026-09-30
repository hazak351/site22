"""Editor final: formatos, FPS, H.264/AAC, áudio, duração exata, logo, CTA, música e transições."""

import pytest

from adflow import editor, media
from tests.conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


@pytest.mark.parametrize("aspect,size", [("9:16", (1080, 1920)), ("16:9", (1920, 1080)), ("1:1", (1080, 1080))])
def test_assemble_each_format_exact(settings, clip_factory, tmp_path, aspect, size):
    source_size = (720, 1280) if aspect == "9:16" else (1280, 720)
    clips = [editor.ClipSpec(clip_factory(4.0, size=source_size), 0.0, 3.0),
             editor.ClipSpec(clip_factory(4.0, size=source_size, fps=25), 0.0, 3.5)]
    out = tmp_path / f"final_{aspect.replace(':', 'x')}.mp4"
    editor.assemble(settings, clips, aspect, 6.5, "cut", editor.Overlays(), tmp_path / "edit", out)
    check = editor.verify_output(settings, out, aspect, 6.5)
    assert check["ok"], check["problems"]
    info = check["info"]
    assert (info["width"], info["height"]) == size
    assert info["nb_frames"] == round(6.5 * settings.fps)
    assert info["vcodec"] == "h264" and info["acodec"] == "aac" and info["sample_rate"] == 48000


def test_transitions_use_spare_footage_and_keep_duration(settings, clip_factory, tmp_path):
    clips = [editor.ClipSpec(clip_factory(4.0), 0.0, 3.0) for _ in range(3)]
    out = tmp_path / "fade.mp4"
    result = editor.assemble(settings, clips, "16:9", 9.0, "fade", editor.Overlays(), tmp_path / "edit", out)
    assert result["joins"] == [settings.transition_duration] * 2  # havia 1s de sobra em cada clipe
    assert editor.verify_output(settings, out, "16:9", 9.0)["ok"]


def test_no_spare_footage_means_hard_cut(settings, clip_factory, tmp_path):
    clips = [editor.ClipSpec(clip_factory(4.0), 0.0, 4.0) for _ in range(2)]
    out = tmp_path / "cut.mp4"
    result = editor.assemble(settings, clips, "16:9", 8.0, "fade", editor.Overlays(), tmp_path / "edit", out)
    assert result["joins"] == [0.0]
    assert editor.verify_output(settings, out, "16:9", 8.0)["ok"]


def test_mixed_cut_and_fade_joins(settings, clip_factory, tmp_path):
    # 4s+3s+3s com clipes de 4s: 1ª junção sem sobra (corte), 2ª com sobra (fade)
    clips = [editor.ClipSpec(clip_factory(4.0), 0.0, 4.0), editor.ClipSpec(clip_factory(4.0), 0.0, 3.0),
             editor.ClipSpec(clip_factory(4.0), 0.0, 3.0)]
    out = tmp_path / "mixed.mp4"
    result = editor.assemble(settings, clips, "16:9", 10.0, "fade", editor.Overlays(), tmp_path / "edit", out)
    assert result["joins"] == [0.0, settings.transition_duration]
    assert editor.verify_output(settings, out, "16:9", 10.0)["ok"]


def test_clip_without_audio_gets_silence(settings, clip_factory, tmp_path):
    clips = [editor.ClipSpec(clip_factory(3.0, audio=False), 0.0, 3.0),
             editor.ClipSpec(clip_factory(3.0, audio=True), 0.0, 3.0)]
    out = tmp_path / "mixed_audio.mp4"
    editor.assemble(settings, clips, "16:9", 6.0, "cut", editor.Overlays(), tmp_path / "edit", out)
    info = media.video_info(settings, out)
    assert info["has_audio"] and abs(info["duration"] - 6.0) < 0.05


def test_short_source_is_padded_to_exact_duration(settings, clip_factory, tmp_path):
    clips = [editor.ClipSpec(clip_factory(2.5), 0.0, 3.0)]  # Flow devolveu menos que o pedido
    out = tmp_path / "padded.mp4"
    editor.assemble(settings, clips, "9:16", 3.0, "cut", editor.Overlays(), tmp_path / "edit", out)
    assert editor.verify_output(settings, out, "9:16", 3.0)["ok"]


def test_logo_cta_and_music_do_not_change_duration(settings, clip_factory, logo_image, tmp_path):
    music = tmp_path / "music.m4a"
    media.run([settings.ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
               "-c:a", "aac", str(music)])  # música MAIS LONGA que o vídeo
    clips = [editor.ClipSpec(clip_factory(4.0), 0.0, 4.0), editor.ClipSpec(clip_factory(4.0), 0.0, 4.0)]
    out = tmp_path / "branded.mp4"
    overlays = editor.Overlays(logo=logo_image, cta="Compre agora", music=music)
    result = editor.assemble(settings, clips, "9:16", 8.0, "cut", overlays, tmp_path / "edit", out)
    check = editor.verify_output(settings, out, "9:16", 8.0)
    assert check["ok"], check["problems"]
    assert result["notes"] == []

    # logo e CTA aparecem só no final: compara um frame do início com um do fim na região do logo
    early = media.extract_frame(settings, out, 1.0, tmp_path / "early.png")
    late = media.extract_frame(settings, out, 7.8, tmp_path / "late.png")
    from PIL import Image
    box = (300, 200, 780, 420)  # região superior central onde o logo entra
    early_px = Image.open(early).convert("RGB").crop(box).resize((40, 20))
    late_px = Image.open(late).convert("RGB").crop(box).resize((40, 20))
    gold = lambda img: sum(1 for r, g, b in media.pixels(img) if r > 180 and g > 140 and b < 120)
    assert gold(late_px) > gold(early_px)


def test_cta_without_font_is_reported(settings, clip_factory, tmp_path):
    settings.font_path = None
    out = tmp_path / "nofont.mp4"
    result = editor.assemble(settings, [editor.ClipSpec(clip_factory(2.0), 0.0, 2.0)], "1:1", 2.0, "cut",
                             editor.Overlays(cta="Compre"), tmp_path / "edit", out)
    assert result["notes"] and "fonte" in result["notes"][0]
    assert editor.verify_output(settings, out, "1:1", 2.0)["ok"]
