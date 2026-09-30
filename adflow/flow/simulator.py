"""SIMULADOR do Google Flow: gera clipes sintéticos com FFmpeg (nenhum crédito, nenhuma conexão).

Serve para testar o pipeline inteiro offline. Comportamentos por cena/tentativa permitem
simular falhas: "ok", "error", "bad_env", "wrong_color", "black".
Ex.: ADFLOW_SIM_BEHAVIORS='{"2": ["bad_env", "ok"]}'
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from .. import media
from ..config import Settings
from .base import DoctorCheck, DoctorReport, FlowError, GenerationRequest, GenerationResult

DOCTOR_ITEMS = [
    ("browser", "Navegador conectado"), ("session", "Sessão Google válida"), ("flow", "Flow abriu"),
    ("prompt", "Campo de prompt encontrado"), ("upload", "Upload encontrado"),
    ("controls", "Controles principais encontrados"), ("generate", "Botão de geração encontrado"),
    ("results", "Área de resultados identificável"), ("download", "Mecanismo de download identificado"),
]


class SimulatorEngine:
    name = "simulator"
    real = False

    def __init__(self, settings: Settings, log=None, behaviors: dict | None = None, delay: float | None = None):
        self.settings = settings
        self.log = log
        if behaviors is None and os.environ.get("ADFLOW_SIM_BEHAVIORS"):
            behaviors = json.loads(os.environ["ADFLOW_SIM_BEHAVIORS"])
        self.behaviors = {int(k): v for k, v in (behaviors or {}).items()}
        self.delay = float(os.environ.get("ADFLOW_SIM_DELAY", 0)) if delay is None else delay

    def doctor(self) -> DoctorReport:
        checks = [DoctorCheck(key=k, label=l, ok=True, detail="SIMULADO") for k, l in DOCTOR_ITEMS]
        return DoctorReport(engine=self.name, real=False, ok=True, checks=checks,
                            message="SIMULADOR: nenhuma conexão com o Google Flow foi feita e nenhum crédito foi usado.")

    def _behavior(self, request: GenerationRequest) -> str:
        plan = self.behaviors.get(request.scene_index, [])
        return plan[request.attempt - 1] if len(plan) >= request.attempt else "ok"

    def generate(self, request: GenerationRequest) -> GenerationResult:
        behavior = self._behavior(request)
        if self.log:
            self.log.info("simulator", "gerando clipe sintético", scene=request.scene_index,
                          attempt=request.attempt, behavior=behavior)
        if self.delay:
            time.sleep(self.delay)
        if behavior == "error":
            raise FlowError("SIMULADOR: geração falhou (falha simulada)", step="generate", retryable=True,
                            credits_spent=True)
        s = self.settings
        w, h = (1280, 720) if request.aspect == "16:9" else (720, 1280)
        duration = request.duration
        dark = request.environment_hint.get("dark", True)
        bg = "0x1d1d22" if dark else "0xe9e9e9"
        if behavior == "bad_env":
            bg = "0x8a5a2b"  # "mesa de madeira": ambiente diferente
        if behavior == "black":
            bg = "black"
        product = next((p for p in request.reference_images if Path(p).is_file()), None)
        work = request.output_path.parent
        work.mkdir(parents=True, exist_ok=True)

        cmd = [s.ffmpeg, "-y", "-v", "error",
               "-f", "lavfi", "-i", f"color=c={bg}:s={w}x{h}:r=24:d={duration:.3f}"]
        filters = []
        if behavior == "black":
            filters.append("[0:v]format=yuv420p[v]")
        else:
            filters.append("[0:v]noise=alls=10:allf=t+u,format=yuv420p[bg]")
            current = "[bg]"
            if product is not None:
                cmd += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(product)]
                shift = ",hue=h=150:s=1.4" if behavior == "wrong_color" else ""
                filters.append(f"[1:v]scale=-2:{int(h * 0.45)},format=rgba,colorkey=0xFFFFFF:0.18:0.08{shift}[prod]")
                filters.append(f"{current}[prod]overlay=x='(W-w)/2+W*0.05*sin(2*PI*t/{duration:.3f})':"
                               f"y='(H-h)/2+H*0.02*cos(2*PI*t/{duration:.3f})':shortest=1[comp]")
                current = "[comp]"
            if s.font_path:
                shutil.copyfile(s.font_path, work / "sim_font.ttf")
                label = f"SIMULADOR  cena {request.scene_index}  tentativa {request.attempt}"
                (work / "sim_label.txt").write_text(label, encoding="utf-8")
                filters.append(f"{current}drawtext=fontfile=sim_font.ttf:textfile=sim_label.txt:fontsize={int(h * 0.025)}:"
                               f"fontcolor=white@0.8:x=w*0.04:y=h*0.03[v]")
            else:
                filters.append(f"{current}null[v]")
        audio_index = 2 if (product is not None and behavior != "black") else 1
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={180 + 40 * request.scene_index}:sample_rate=48000:duration={duration:.3f}"]
        filters.append(f"[{audio_index}:a]volume=0.15[a]")
        cmd += ["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", str(request.output_path.resolve())]
        media.run(cmd, cwd=work)
        return GenerationResult(video_path=request.output_path, meta={"engine": "simulator", "behavior": behavior})

    def close(self) -> None:
        pass
