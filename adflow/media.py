"""Utilitários FFmpeg/ffprobe e análise de imagem com Pillow (sem numpy, sem compilação)."""

from __future__ import annotations

import colorsys
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageStat

from .config import Settings

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}


class MediaError(RuntimeError):
    pass


def pixels(img: Image.Image) -> list:
    """Lista de pixels compatível com Pillow 11–14 (getdata foi descontinuado)."""
    getter = getattr(img, "get_flattened_data", None)
    return list(getter() if getter else img.getdata())


def kind_of(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return "other"


def run(cmd: list[str], timeout: int = 900, cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    kwargs: dict = {"cwd": str(cwd) if cwd else None}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **kwargs
        )
    except FileNotFoundError as exc:
        raise MediaError(f"executável não encontrado: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"tempo esgotado executando {Path(cmd[0]).name}") from exc
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise MediaError(f"{Path(cmd[0]).name} falhou (código {proc.returncode}):\n{tail}")
    return proc


def require_tools(settings: Settings) -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if not getattr(settings, name)]
    if missing:
        raise MediaError(
            "FFmpeg não encontrado (" + ", ".join(missing) + "). Instale com 'winget install Gyan.FFmpeg' "
            "ou coloque ffmpeg.exe e ffprobe.exe em tools\\ffmpeg\\bin."
        )


def probe(settings: Settings, path: str | Path) -> dict:
    require_tools(settings)
    proc = run([settings.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
               timeout=120)
    return json.loads(proc.stdout or "{}")


def _fps(rate: str) -> float:
    try:
        num, den = rate.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def video_info(settings: Settings, path: str | Path) -> dict:
    data = probe(settings, path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaError(f"sem trilha de vídeo: {Path(path).name}")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0.0)
    return {
        "duration": duration,
        "video_duration": float(video.get("duration") or duration),
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "fps": _fps(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"),
        "nb_frames": int(video.get("nb_frames", 0) or 0),
        "vcodec": video.get("codec_name"),
        "pix_fmt": video.get("pix_fmt"),
        "has_audio": audio is not None,
        "acodec": audio.get("codec_name") if audio else None,
        "sample_rate": int(audio.get("sample_rate", 0)) if audio else 0,
    }


def extract_frame(settings: Settings, video: str | Path, t: float, out_png: str | Path) -> Path:
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    run([settings.ffmpeg, "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
         "-frames:v", "1", "-update", "1", str(out)], timeout=120)
    if not out.exists():
        raise MediaError(f"não foi possível extrair frame em {t:.2f}s de {Path(video).name}")
    return out


def detect_cuts(settings: Settings, video: str | Path, threshold: float = 0.3) -> list[float]:
    proc = run([settings.ffmpeg, "-hide_banner", "-i", str(video), "-an", "-filter:v",
                f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"], timeout=600)
    return [round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]


def black_ratio(settings: Settings, video: str | Path, duration: float) -> float:
    proc = run([settings.ffmpeg, "-hide_banner", "-i", str(video), "-an", "-vf",
                "blackdetect=d=0.1:pix_th=0.08", "-f", "null", "-"], timeout=600)
    total = sum(float(x) for x in re.findall(r"black_duration:([0-9.]+)", proc.stderr))
    return min(1.0, total / duration) if duration else 0.0


def freeze_ratio(settings: Settings, video: str | Path, duration: float) -> float:
    proc = run([settings.ffmpeg, "-hide_banner", "-i", str(video), "-an", "-vf",
                "freezedetect=n=0.001:d=0.5", "-f", "null", "-"], timeout=600)
    starts = [float(x) for x in re.findall(r"freeze_start: ([0-9.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"freeze_end: ([0-9.]+)", proc.stderr)]
    total = 0.0
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else duration
        total += max(0.0, end - start)
    return min(1.0, total / duration) if duration else 0.0


def motion_intensity(frames: list[Path]) -> float:
    """Diferença média entre frames consecutivos (0 = parado, 1 = muita mudança)."""
    if len(frames) < 2:
        return 0.0
    diffs = []
    previous = None
    for path in frames:
        img = Image.open(path).convert("L").resize((64, 64))
        if previous is not None:
            pairs = zip(pixels(img), pixels(previous))
            diffs.append(sum(abs(a - b) for a, b in pairs) / (64 * 64 * 255))
        previous = img
    return round(min(1.0, 4 * sum(diffs) / len(diffs)), 3)


# ---------------------------------------------------------------- cor / histogramas

def _load_small(path: str | Path, size: int = 96) -> Image.Image:
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(background, img)
    img = img.convert("RGB")
    img.thumbnail((size, size))
    return img


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb[:3])


def from_hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def border_color(img: Image.Image) -> tuple[int, int, int]:
    w, h = img.size
    pixels = [img.getpixel((x, 0)) for x in range(w)] + [img.getpixel((x, h - 1)) for x in range(w)]
    pixels += [img.getpixel((0, y)) for y in range(h)] + [img.getpixel((w - 1, y)) for y in range(h)]
    buckets: dict[tuple[int, int, int], int] = {}
    for p in pixels:
        key = (p[0] // 32, p[1] // 32, p[2] // 32)
        buckets[key] = buckets.get(key, 0) + 1
    top = max(buckets, key=buckets.get)
    members = [p for p in pixels if (p[0] // 32, p[1] // 32, p[2] // 32) == top]
    return tuple(sum(c[i] for c in members) // len(members) for i in range(3))  # type: ignore[return-value]


def dominant_colors(path: str | Path, count: int = 4, ignore_background: bool = True) -> list[tuple[str, float]]:
    """Cores dominantes do objeto (remove a cor de fundo estimada pela borda)."""
    img = _load_small(path)
    bg = border_color(img) if ignore_background else None
    pixels_list = pixels(img)
    if bg is not None:
        filtered = [p for p in pixels_list if _distance(p, bg) > 40]
        if len(filtered) > len(pixels_list) * 0.05:
            pixels_list = filtered
    sample = Image.new("RGB", (len(pixels_list), 1))
    sample.putdata(pixels_list)
    quant = sample.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    palette = quant.getpalette() or []
    counts = sorted(quant.getcolors() or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    result: list[tuple[str, float]] = []
    for amount, index in counts:
        rgb = tuple(palette[index * 3:index * 3 + 3])
        if any(_distance(rgb, from_hex(h)) < 30 for h, _ in result):
            continue
        result.append((to_hex(rgb), round(amount / total, 3)))
        if len(result) >= count:
            break
    return result


def color_name(value: str) -> str:
    r, g, b = (c / 255 for c in from_hex(value))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    hue = h * 360
    if v < 0.18:
        return "black"
    if s < 0.15:
        if v > 0.85:
            return "white"
        return "light gray" if v > 0.6 else "dark gray" if v < 0.35 else "gray"
    if 30 <= hue < 55 and 0.25 <= s <= 0.85 and 0.45 <= v <= 0.95:
        return "gold"
    if 15 <= hue < 45 and v < 0.6:
        return "brown"
    names = [(15, "red"), (40, "orange"), (65, "yellow"), (160, "green"), (195, "cyan"),
             (255, "blue"), (290, "purple"), (335, "pink"), (361, "red")]
    for limit, name in names:
        if hue < limit:
            return ("dark " if v < 0.4 else "") + name
    return "colored"


def hsv_histogram(img: Image.Image, h_bins: int = 18, s_bins: int = 6, v_bins: int = 6) -> list[float]:
    hsv = img.convert("HSV")
    hist = [0.0] * (h_bins + s_bins + v_bins)
    data = pixels(hsv)
    for h, s, v in data:
        if s > 40 and v > 40:
            hist[min(h_bins - 1, h * h_bins // 256)] += 1
        hist[h_bins + min(s_bins - 1, s * s_bins // 256)] += 1
        hist[h_bins + s_bins + min(v_bins - 1, v * v_bins // 256)] += 1
    n = len(data) or 1
    return [x / n for x in hist]


def histogram_similarity(a: list[float], b: list[float]) -> float:
    """Interseção de histogramas normalizada (1 = idênticos)."""
    inter = sum(min(x, y) for x, y in zip(a, b))
    norm = min(sum(a), sum(b)) or 1.0
    return round(inter / norm, 3)


def region(img: Image.Image, which: str) -> Image.Image:
    w, h = img.size
    if which == "center":
        return img.crop((int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8)))
    if which == "border":
        # anel externo (fundo/ambiente): colagem das 4 faixas
        bw, bh = max(1, int(w * 0.15)), max(1, int(h * 0.15))
        strips = [img.crop((0, 0, w, bh)), img.crop((0, h - bh, w, h)),
                  img.crop((0, bh, bw, h - bh)).resize((w, bh)), img.crop((w - bw, bh, w, h - bh)).resize((w, bh))]
        canvas = Image.new("RGB", (w, bh * 4))
        for i, strip in enumerate(strips):
            canvas.paste(strip, (0, i * bh))
        return canvas
    return img


def image_similarity(a: str | Path, b: str | Path, which: str = "full") -> float:
    ia = region(_load_small(a, 160), which)
    ib = region(_load_small(b, 160), which)
    return histogram_similarity(hsv_histogram(ia), hsv_histogram(ib))


def brightness(path: str | Path) -> float:
    return round(ImageStat.Stat(_load_small(path).convert("L")).mean[0] / 255, 3)


def hue_profile(path: str | Path, ignore_background: bool = True, which: str = "full") -> tuple[list[float], float]:
    """Histograma de matiz (só pixels saturados) + fração de pixels saturados."""
    img = region(_load_small(path, 128), which)
    bg = border_color(img) if ignore_background else None
    hist = [0.0] * 18
    saturated = 0
    total = 0
    for (r, g, b), (h, s, v) in zip(pixels(img), pixels(img.convert("HSV"))):
        if bg is not None and _distance((r, g, b), bg) <= 40:
            continue
        total += 1
        if s > 60 and v > 50:
            saturated += 1
            hist[min(17, h * 18 // 256)] += 1
    if saturated:
        hist = [x / saturated for x in hist]
    return hist, (saturated / total if total else 0.0)
