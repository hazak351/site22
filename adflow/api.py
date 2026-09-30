"""API HTTP (FastAPI) + interface web estática."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, media, planner
from .config import Settings, load_settings
from .credits import BudgetError, check_estimate, load_credit_table
from .flow import create_engine
from .flow.browser import cdp_version
from .logs import ProjectLog
from .models import Asset, Project, ProjectStatus
from .pipeline import Pipeline, Runner, estimate_for, mark_interrupted
from .storage import ProjectStore

MAX_UPLOAD = 300 * 1024 * 1024
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
ASPECTS = ("9:16", "16:9", "1:1")
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class EstimateIn(BaseModel):
    duration: float
    quality: str = "equilibrado"
    engine: str = "simulator"
    max_credits: int | None = None


class StartIn(BaseModel):
    confirm_credits: bool = False


class DoctorIn(BaseModel):
    engine: str = "flow"


def _summary(project: Project) -> dict:
    return {
        "id": project.id, "created_at": project.created_at, "status": project.status,
        "engine": project.engine, "brief": project.brief[:140], "duration": project.duration,
        "aspect": project.aspect, "quality": project.quality, "final_video": project.final_video,
        "current_stage": project.current_stage,
        "credits": project.credits.model_dump() if project.credits else None,
    }


def create_app(settings: Settings | None = None, engine_kwargs: dict | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.ensure_dirs()
    store = ProjectStore(settings)
    pipeline = Pipeline(settings, store, engine_kwargs=engine_kwargs)
    runner = Runner(pipeline)
    interrupted = mark_interrupted(store)

    app = FastAPI(title="AdFlow Studio Auto", version=__version__)
    app.state.settings = settings
    app.state.store = store
    app.state.runner = runner
    app.state.interrupted_on_start = interrupted

    def load(project_id: str) -> Project:
        try:
            if not store.exists(project_id):
                raise HTTPException(404, "projeto não encontrado")
            return store.load(project_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def save_upload(project_id: str, upload: UploadFile, subdir: str, allowed: set[str]) -> Asset:
        name = upload.filename or "arquivo"
        ext = Path(name).suffix.lower()
        if ext not in allowed:
            raise HTTPException(400, f"tipo de arquivo não suportado: {name}")
        data = upload.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, f"arquivo muito grande: {name}")
        if not data:
            raise HTTPException(400, f"arquivo vazio: {name}")
        rel = store.save_upload(project_id, subdir, name, data)
        kind = media.kind_of(name) if ext not in AUDIO_EXTS else "audio"
        return Asset(path=rel, original_name=name, kind=kind)

    # ------------------------------------------------------------ status

    @app.get("/api/health")
    def health() -> dict:
        return {
            "version": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "ffmpeg": settings.ffmpeg,
            "ffprobe": settings.ffprobe,
            "font": settings.font_path,
            "browser": settings.browser_path,
            "cdp_port": settings.cdp_port,
            "cdp_alive": cdp_version(settings.cdp_port) is not None,
            "busy": runner.current,
            "interrupted_on_start": interrupted,
        }

    @app.get("/api/config")
    def config() -> dict:
        table = load_credit_table(settings)
        return {
            "durations": planner.PRESET_DURATIONS,
            "aspects": list(ASPECTS),
            "modes": {k: {"label": v.get("label", k), "credits_per_generation": v["credits_per_generation"],
                          "max_attempts_per_scene": v.get("max_attempts_per_scene", 1),
                          "model_hint": v.get("model_hint", "")} for k, v in table["modes"].items()},
            "generation_durations": settings.generation_durations,
        }

    @app.post("/api/estimate")
    def estimate_endpoint(body: EstimateIn) -> dict:
        try:
            plan = estimate_for(settings, body.duration, body.quality, body.engine, body.max_credits)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        result = plan.model_dump()
        result["within_budget"] = plan.estimated <= plan.max_credits
        return result

    # ------------------------------------------------------------ projetos

    @app.get("/api/projects")
    def list_projects() -> list[dict]:
        return [_summary(p) for p in store.list()]

    @app.post("/api/projects")
    def create_project(
        brief: str = Form(...),
        duration: float = Form(...),
        aspect: str = Form("9:16"),
        quality: str = Form("equilibrado"),
        engine: str = Form("simulator"),
        product_name: str = Form(""),
        cta: str = Form(""),
        max_credits: str = Form(""),
        product_files: list[UploadFile] = File(...),
        reference: UploadFile | None = File(None),
        logo: UploadFile | None = File(None),
        music: UploadFile | None = File(None),
    ) -> dict:
        if not brief.strip():
            raise HTTPException(400, "descreva o anúncio")
        if aspect not in ASPECTS:
            raise HTTPException(400, f"formato inválido: {aspect}")
        if engine not in ("simulator", "flow"):
            raise HTTPException(400, f"motor inválido: {engine}")
        cap = int(max_credits) if max_credits.strip().isdigit() else None
        try:
            plan = estimate_for(settings, duration, quality, engine, cap)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        project = Project(id=store.new_id(), brief=brief.strip(), duration=round(duration, 3), aspect=aspect,
                          quality=quality, engine=engine, product_name=product_name.strip(), cta=cta.strip(),
                          max_credits=cap, credits=plan)
        store.save(project)
        try:
            images = [f for f in product_files if f is not None and f.filename]
            if not images:
                raise HTTPException(400, "adicione pelo menos uma foto do produto")
            project.product_assets = [save_upload(project.id, f, "product", media.IMAGE_EXTS) for f in images]
            if reference is not None and reference.filename:
                project.reference = save_upload(project.id, reference, "reference", media.IMAGE_EXTS | media.VIDEO_EXTS)
            if logo is not None and logo.filename:
                project.logo = save_upload(project.id, logo, "brand", media.IMAGE_EXTS)
            if music is not None and music.filename:
                project.music = save_upload(project.id, music, "brand", AUDIO_EXTS)
        except HTTPException:
            store.delete(project.id)
            raise
        store.save(project)
        ProjectLog(settings.logs_dir, project.id).info("project", "projeto criado", engine=engine, duration=duration,
                                                        aspect=aspect, quality=quality, estimated=plan.estimated,
                                                        max_credits=plan.max_credits)
        return project.model_dump()

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict:
        project = load(project_id)
        data = project.model_dump()
        data["running"] = runner.current == project_id
        return data

    def _start(project_id: str, confirm: bool) -> dict:
        project = load(project_id)
        if project.status == ProjectStatus.running and runner.current == project_id:
            raise HTTPException(409, "projeto já está em execução")
        if project.status == ProjectStatus.done:
            raise HTTPException(409, "projeto já concluído")
        if project.engine == "flow" and not (confirm or project.credits_confirmed):
            raise HTTPException(412, {"message": "Confirme a estimativa de créditos antes de gerar no Flow real.",
                                      "credits": project.credits.model_dump() if project.credits else None})
        try:
            if project.credits:
                check_estimate(project.credits)
        except BudgetError as exc:
            raise HTTPException(400, str(exc)) from exc
        if confirm:
            project.credits_confirmed = True
            store.save(project)
            ProjectLog(settings.logs_dir, project.id).info("credits", "usuário confirmou a estimativa",
                                                            estimated=project.credits.estimated,
                                                            max_credits=project.credits.max_credits)
        try:
            runner.start(project_id)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"started": True, "id": project_id}

    @app.post("/api/projects/{project_id}/start")
    def start(project_id: str, body: StartIn) -> dict:
        return _start(project_id, body.confirm_credits)

    @app.post("/api/projects/{project_id}/resume")
    def resume(project_id: str) -> dict:
        project = load(project_id)
        if project.status not in (ProjectStatus.interrupted, ProjectStatus.failed, ProjectStatus.cancelled):
            raise HTTPException(409, f"nada para retomar (status: {project.status.value})")
        return _start(project_id, False)

    @app.post("/api/projects/{project_id}/cancel")
    def cancel(project_id: str) -> dict:
        return {"cancelled": runner.cancel(project_id)}

    @app.get("/api/projects/{project_id}/log")
    def project_log(project_id: str, lines: int = 200) -> dict:
        load(project_id)
        return {"lines": ProjectLog(settings.logs_dir, project_id).tail(min(lines, 2000))}

    @app.get("/api/projects/{project_id}/video")
    def final_video(project_id: str):
        project = load(project_id)
        if not project.final_video:
            raise HTTPException(404, "vídeo final ainda não existe")
        path = store.project_dir(project_id) / project.final_video
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    @app.get("/api/projects/{project_id}/file/{rel_path:path}")
    def project_file(project_id: str, rel_path: str):
        base = store.project_dir(project_id).resolve()
        target = (base / rel_path).resolve()
        if base not in target.parents or not target.is_file():
            raise HTTPException(404, "arquivo não encontrado")
        return FileResponse(target)

    # ------------------------------------------------------------ Flow Doctor

    @app.post("/api/doctor")
    def doctor(body: DoctorIn) -> dict:
        if runner.busy():
            raise HTTPException(409, "há uma geração em andamento; rode o Doctor depois")
        log = ProjectLog(settings.logs_dir, "_doctor")
        engine = create_engine(body.engine, settings, log=log)
        try:
            report = engine.doctor()
        finally:
            engine.close()
        data = report.model_dump()
        if report.screenshot:
            data["screenshot_url"] = f"/api/diagnostics/{Path(report.screenshot).name}"
        return data

    @app.get("/api/diagnostics/{name}")
    def diagnostics(name: str):
        target = (settings.diagnostics_dir / Path(name).name).resolve()
        if not target.is_file():
            raise HTTPException(404, "diagnóstico não encontrado")
        return FileResponse(target)

    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    return app
