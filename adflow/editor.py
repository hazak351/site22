"""Editor final (FFmpeg): normaliza, junta, aplica transições, logo, CTA, música e fecha a duração exata."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from . import media
from .config import Settings

RESOLUTIONS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
XFADE = {"fade": "fade", "fadeblack": "fadeblack", "cut": None}

ENCODE_V = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high"]
ENCODE_A = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


@dataclass
class ClipSpec:
    source: Path
    start: float
    target: float  # duração que a cena ocupa no vídeo final


@dataclass
class Overlays:
    logo: Path | None = None
    cta: str = ""
    music: Path | None = None
    logo_seconds: float = 2.5
    music_volume: float = 0.5


def _fit_filter(fit: str, w: int, h: int, fps: int) -> str:
    tail = f"setsar=1,fps={fps},format=yuv420p"
    if fit == "blur":
        return (f"split[bgs][fgs];[bgs]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=30:5[bg];"
                f"[fgs]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,{tail}")
    return f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},{tail}"


def normalize_clip(settings: Settings, clip: ClipSpec, length: float, aspect: str, dst: Path) -> Path:
    """Recorta [start, start+length], ajusta resolução/FPS e garante trilha de áudio estéreo 48k."""
    w, h = RESOLUTIONS[aspect]
    info = media.video_info(settings, clip.source)
    available = max(0.0, info["duration"] - clip.start)
    pad = max(0.0, length - available + 0.1)
    vf = f"[0:v]{_fit_filter(settings.fit_mode, w, h, settings.fps)}"
    if pad > 0:
        vf += f",tpad=stop_mode=clone:stop_duration={pad:.3f}"
    vf += f",trim=0:{length:.3f},setpts=PTS-STARTPTS[v]"
    cmd = [settings.ffmpeg, "-y", "-v", "error", "-ss", f"{clip.start:.3f}", "-i", str(clip.source)]
    if info["has_audio"]:
        af = f"[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=0:{length:.3f},asetpts=PTS-STARTPTS[a]"
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        af = f"[1:a]atrim=0:{length:.3f},asetpts=PTS-STARTPTS[a]"
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd += ["-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}",
            *ENCODE_V, "-preset", settings.normalize_preset, "-crf", "17", *ENCODE_A, str(dst)]
    media.run(cmd)
    return dst


def plan_lengths(settings: Settings, clips: list[ClipSpec], transition: str,
                 source_durations: list[float]) -> tuple[list[float], list[float]]:
    """Comprimento de cada clipe normalizado e duração de cada transição.

    Uma transição com sobreposição consome tempo; só é usada quando o clipe de origem tem sobra
    depois do trecho alvo. Sem sobra, a junção vira corte seco (a duração total nunca muda).
    """
    base = settings.transition_duration if XFADE.get(transition) else 0.0
    lengths, joins = [], []
    for i, clip in enumerate(clips):
        if i < len(clips) - 1:
            spare = max(0.0, source_durations[i] - clip.start - clip.target)
            td = round(min(base, spare), 3)
            td = td if td >= 0.1 else 0.0
            joins.append(td)
            lengths.append(round(clip.target + td, 3))
        else:
            lengths.append(round(clip.target, 3))
    return lengths, joins


def assemble(settings: Settings, clips: list[ClipSpec], aspect: str, total: float, transition: str,
             overlays: Overlays, work_dir: Path, output: Path, log=None) -> dict:
    media.require_tools(settings)
    if not clips:
        raise media.MediaError("nenhuma cena aprovada para montar")
    w, h = RESOLUTIONS[aspect]
    fps = settings.fps
    work_dir.mkdir(parents=True, exist_ok=True)
    durations = [media.video_info(settings, clip.source)["duration"] for clip in clips]
    lengths, joins = plan_lengths(settings, clips, transition, durations)

    normalized = []
    for i, (clip, length) in enumerate(zip(clips, lengths), start=1):
        dst = work_dir / f"norm_{i:02d}.mp4"
        normalize_clip(settings, clip, length, aspect, dst)
        normalized.append(dst)
        if log:
            log.info("ffmpeg", f"cena {i} normalizada", length=length, source=str(clip.source.name))

    cmd = [settings.ffmpeg, "-y", "-v", "error"]
    for path in normalized:
        cmd += ["-i", path.name]
    filters: list[str] = []
    # mesmo timebase em todas as entradas: concat e xfade misturados exigem isso
    for k in range(len(normalized)):
        filters.append(f"[{k}:v]settb=AVTB,setpts=PTS-STARTPTS[s{k}]")
    v_cur, a_cur, cur_len = "[s0]", "[0:a]", lengths[0]
    kind = XFADE.get(transition) or "fade"
    for k in range(1, len(normalized)):
        td = joins[k - 1]
        if td > 0:
            filters.append(f"{v_cur}[s{k}]xfade=transition={kind}:duration={td:.3f}:offset={cur_len - td:.3f}[v{k}]")
            filters.append(f"{a_cur}[{k}:a]acrossfade=d={td:.3f}:c1=tri:c2=tri[a{k}]")
            cur_len = cur_len + lengths[k] - td
        else:
            filters.append(f"{v_cur}[s{k}]concat=n=2:v=1:a=0[v{k}]")
            filters.append(f"{a_cur}[{k}:a]concat=n=2:v=0:a=1[a{k}]")
            cur_len = cur_len + lengths[k]
        v_cur, a_cur = f"[v{k}]", f"[a{k}]"

    next_input = len(normalized)
    end_start = max(0.0, total - min(overlays.logo_seconds, total))
    if overlays.logo and overlays.logo.is_file():
        cmd += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(overlays.logo.resolve())]
        logo_w = int(w * (0.42 if aspect == "9:16" else 0.28))
        filters.append(f"[{next_input}:v]scale={logo_w}:-1,format=rgba,fade=t=in:st={end_start:.3f}:d=0.4:alpha=1[logo]")
        filters.append(f"{v_cur}[logo]overlay=x=(W-w)/2:y=H*0.12:enable='gte(t,{end_start:.3f})'[vlogo]")
        v_cur = "[vlogo]"
        next_input += 1

    text_notes = []
    if overlays.cta:
        if settings.font_path:
            shutil.copyfile(settings.font_path, work_dir / "font.ttf")
            (work_dir / "cta.txt").write_text(overlays.cta, encoding="utf-8")
            size = int(h * (0.042 if aspect == "9:16" else 0.06))
            filters.append(
                f"{v_cur}drawtext=fontfile=font.ttf:textfile=cta.txt:fontsize={size}:fontcolor=white:"
                f"box=1:boxcolor=black@0.45:boxborderw={size // 2}:x=(w-text_w)/2:y=h*0.80:"
                f"alpha='if(lt(t,{end_start:.3f}),0,min(1,(t-{end_start:.3f})/0.4))'[vtext]")
            v_cur = "[vtext]"
        else:
            text_notes.append("CTA não aplicado: nenhuma fonte encontrada (defina font_path em config/settings.json)")

    if overlays.music and overlays.music.is_file():
        cmd += ["-stream_loop", "-1", "-i", str(overlays.music.resolve())]
        fade_out = max(0.0, total - 1.2)
        filters.append(f"[{next_input}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                       f"atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={overlays.music_volume},"
                       f"afade=t=in:d=0.5,afade=t=out:st={fade_out:.3f}:d=1.2[mus]")
        filters.append(f"{a_cur}[mus]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[amix]")
        a_cur = "[amix]"
        next_input += 1

    # fecha a duração exata: vídeo com clone do último frame se faltar, áudio com silêncio se faltar
    filters.append(f"{v_cur}tpad=stop_mode=clone:stop_duration=1,trim=0:{total:.3f},setpts=PTS-STARTPTS,fps={fps},format=yuv420p[vout]")
    filters.append(f"{a_cur}apad,atrim=0:{total:.3f},asetpts=PTS-STARTPTS[aout]")

    output.parent.mkdir(parents=True, exist_ok=True)
    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
            "-t", f"{total:.3f}", "-r", str(fps), *ENCODE_V, "-preset", settings.final_preset, "-crf", "18",
            *ENCODE_A, "-movflags", "+faststart", str(output.resolve())]
    media.run(cmd, cwd=work_dir, timeout=1800)
    if log:
        log.info("ffmpeg", "montagem concluída", output=str(output.name), joins=joins, lengths=lengths)
    return {"lengths": lengths, "joins": joins, "notes": text_notes}


def verify_output(settings: Settings, path: Path, aspect: str, total: float) -> dict:
    info = media.video_info(settings, path)
    w, h = RESOLUTIONS[aspect]
    expected_frames = round(total * settings.fps)
    problems = []
    if (info["width"], info["height"]) != (w, h):
        problems.append(f"resolução {info['width']}x{info['height']} ≠ {w}x{h}")
    if abs(info["duration"] - total) > 0.1:
        problems.append(f"duração {info['duration']:.3f}s ≠ {total:.3f}s")
    if info["nb_frames"] and abs(info["nb_frames"] - expected_frames) > 1:
        problems.append(f"{info['nb_frames']} frames ≠ {expected_frames}")
    if info["vcodec"] != "h264":
        problems.append(f"codec de vídeo {info['vcodec']}")
    if not info["has_audio"] or info["acodec"] != "aac":
        problems.append("sem trilha AAC")
    if abs(info["fps"] - settings.fps) > 0.01:
        problems.append(f"fps {info['fps']:.2f} ≠ {settings.fps}")
    return {"ok": not problems, "problems": problems, "info": info, "expected_frames": expected_frames}
