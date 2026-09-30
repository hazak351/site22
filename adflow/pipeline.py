"""Orquestrador: PEDIDO → análises → direção → cenas → prompts → Flow → Quality Gate → edição → MP4.

Cada etapa é persistida em project.json. Ao retomar, etapas concluídas são puladas, cenas
aprovadas não são regeradas e downloads já feitos são reaproveitados.
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import analysis, director, editor, media, planner, prompts, quality
from .config import Settings
from .credits import BudgetError, can_spend, check_estimate, estimate, load_credit_table
from .flow import FlowError, GenerationRequest, create_engine
from .logs import ProjectLog
from .models import Attempt, Project, ProjectStatus, Scene, SceneStatus, StageStatus, now_iso
from .storage import ProjectStore


class Cancelled(Exception):
    pass


class QualityError(RuntimeError):
    pass


@dataclass
class RunContext:
    project: Project
    log: ProjectLog
    cancel: threading.Event
    t0: float = field(default_factory=time.monotonic)
    base_elapsed: float = 0.0
    engine: object | None = None


def estimate_for(settings: Settings, duration: float, quality_mode: str, engine: str,
                 max_credits: int | None = None):
    durations = planner.plan_durations(duration, settings.generation_durations, settings.min_scene_duration)
    return estimate(durations, quality_mode, load_credit_table(settings), settings.outputs_per_prompt,
                    max_credits=max_credits, simulated=engine != "flow")


class Pipeline:
    def __init__(self, settings: Settings, store: ProjectStore, engine_factory=create_engine,
                 engine_kwargs: dict | None = None, judge=None):
        self.settings = settings
        self.store = store
        self.engine_factory = engine_factory
        self.engine_kwargs = engine_kwargs or {}
        self.judge = judge

    # ------------------------------------------------------------ infraestrutura

    def _dir(self, project: Project) -> Path:
        return self.store.project_dir(project.id)

    def _rel(self, project: Project, path: Path) -> str:
        return str(Path(path).resolve().relative_to(self._dir(project).resolve())).replace("\\", "/")

    def _save(self, ctx: RunContext) -> None:
        ctx.project.elapsed_s = round(ctx.base_elapsed + time.monotonic() - ctx.t0, 1)
        self.store.save(ctx.project)

    def _check_cancel(self, ctx: RunContext) -> None:
        if ctx.cancel.is_set():
            raise Cancelled()

    def _engine(self, ctx: RunContext):
        if ctx.engine is None:
            kwargs = dict(self.engine_kwargs)
            if ctx.project.engine == "simulator" and "delay" not in kwargs:
                kwargs["delay"] = self.settings.simulator_delay_s
            ctx.engine = self.engine_factory(ctx.project.engine, self.settings, log=ctx.log, **kwargs)
        return ctx.engine

    # ------------------------------------------------------------ execução

    def run(self, project_id: str, cancel: threading.Event | None = None) -> Project:
        project = self.store.load(project_id)
        ctx = RunContext(project=project, log=ProjectLog(self.settings.logs_dir, project.id),
                         cancel=cancel or threading.Event(), base_elapsed=project.elapsed_s)
        resumed = project.status in (ProjectStatus.interrupted, ProjectStatus.failed, ProjectStatus.cancelled)
        project.status = ProjectStatus.running
        project.error = None
        project.started_at = project.started_at or now_iso()
        ctx.log.info("pipeline", "retomando projeto" if resumed else "iniciando projeto",
                     engine=project.engine, duration=project.duration, aspect=project.aspect, quality=project.quality)
        self._recover(ctx)
        self._save(ctx)

        steps = [
            ("product_analysis", self._product_analysis),
            ("reference_analysis", self._reference_analysis),
            ("direction", self._direction),
            ("planning", self._planning),
            ("prompts", self._prompts),
            ("flow_check", self._flow_check),
            ("generation", self._generation),
            ("editing", self._editing),
            ("export", self._export),
        ]
        current = None
        try:
            for key, func in steps:
                self._check_cancel(ctx)
                stage = project.stage(key)
                if stage.status == StageStatus.done and not (key == "flow_check" and project.stage("generation").status != StageStatus.done):
                    continue
                current = stage
                project.current_stage = key
                stage.status = StageStatus.running
                stage.started_at = now_iso()
                stage.detail = ""
                self._save(ctx)
                ctx.log.info("stage", f"{stage.label}: iniciando")
                detail = func(ctx) or ""
                stage.status = StageStatus.done
                stage.finished_at = now_iso()
                stage.detail = detail
                ctx.log.info("stage", f"{stage.label}: concluído", detail=detail)
                self._save(ctx)
            project.status = ProjectStatus.done
            project.current_stage = None
            project.finished_at = now_iso()
            ctx.log.info("pipeline", "vídeo final pronto", file=project.final_video, elapsed_s=project.elapsed_s)
        except Cancelled:
            project.status = ProjectStatus.cancelled
            if current is not None:
                current.status = StageStatus.pending
            ctx.log.warn("pipeline", "cancelado pelo usuário")
        except Exception as exc:
            project.status = ProjectStatus.failed
            project.error = str(exc)
            if current is not None:
                current.status = StageStatus.failed
                current.detail = str(exc)[:500]
            data = exc.to_dict() if isinstance(exc, FlowError) else {}
            if data:
                project.final_report["last_flow_error"] = data
            ctx.log.error("pipeline", f"falhou: {exc}", **{k: v for k, v in data.items() if k not in ("html", "message")},
                          traceback=traceback.format_exc(limit=6))
        finally:
            if ctx.engine is not None:
                try:
                    ctx.engine.close()
                except Exception:
                    pass
            self._save(ctx)
        return project

    # ------------------------------------------------------------ recuperação

    def _recover(self, ctx: RunContext) -> None:
        """Reconcilia o estado salvo com os arquivos em disco (após queda, reinício ou falha)."""
        project = ctx.project
        base = self._dir(project)
        for stage in project.stages:
            if stage.status == StageStatus.running:
                stage.status = StageStatus.pending
        for scene in project.scenes:
            for attempt in scene.attempts:
                if attempt.status != "running":
                    continue
                path = base / attempt.video if attempt.video else None
                if path and path.is_file() and self._valid_video(path):
                    attempt.status = "ok"
                    ctx.log.info("recovery", "download encontrado após interrupção; será reaproveitado",
                                 scene=scene.index, attempt=attempt.number)
                else:
                    attempt.status = "interrompida"
                    attempt.error = "processo interrompido durante a geração (créditos contados por segurança)"
                    ctx.log.warn("recovery", "tentativa interrompida", scene=scene.index, attempt=attempt.number)
                attempt.finished_at = attempt.finished_at or now_iso()
            if scene.status in (SceneStatus.approved, SceneStatus.warning):
                video = scene.approved_video()
                if not video or not (base / video).is_file():
                    ctx.log.warn("recovery", "arquivo da cena aprovada sumiu; cena volta para a fila", scene=scene.index)
                    scene.status = SceneStatus.pending
                    scene.approved_attempt = None
                    project.stage("generation").status = StageStatus.pending
            elif scene.status in (SceneStatus.generating, SceneStatus.checking):
                scene.status = SceneStatus.pending
        if project.final_video and not (base / project.final_video).is_file():
            project.final_video = None
            project.stage("editing").status = StageStatus.pending
            project.stage("export").status = StageStatus.pending

    def _valid_video(self, path: Path) -> bool:
        try:
            return media.video_info(self.settings, path)["duration"] > 0.5
        except Exception:
            return False

    # ------------------------------------------------------------ etapas

    def _product_analysis(self, ctx: RunContext) -> str:
        p = ctx.project
        profile = analysis.analyze_product(self.settings, self._dir(p), p.product_name, p.brief, p.product_assets)
        p.product_profile = profile
        ctx.log.info("product", "Product Lock gerado", category=profile.category, colors=profile.color_names,
                     lock=profile.lock_text)
        views = ", ".join(sorted({a.role for a in profile.images if a.kind == "image"})) or "nenhuma foto"
        return f"{profile.category}; cores: {', '.join(profile.color_names) or '—'}; vistas: {views}"

    def _reference_analysis(self, ctx: RunContext) -> str:
        p = ctx.project
        ref = analysis.analyze_reference(self.settings, self._dir(p), p.reference, p.brief)
        p.reference_analysis = ref
        ctx.log.info("reference", "referência analisada", present=ref.present, pace=ref.pace, key=ref.key,
                     moves=ref.camera_moves, cuts=len(ref.cuts))
        if not ref.present:
            return "sem referência (linguagem visual pelo briefing)"
        return f"ritmo {ref.pace}, luz {ref.key}, movimentos: {', '.join(ref.camera_moves) or '—'}"

    def _direction(self, ctx: RunContext) -> str:
        p = ctx.project
        p.direction = director.build_direction(p.brief, p.product_profile, p.reference_analysis, p.cta)
        p.environment = director.build_environment(p.brief, p.product_profile, p.reference_analysis, p.direction)
        ctx.log.info("direction", "direção criativa definida", style=p.direction.style, pace=p.direction.pace,
                     transition=p.direction.transition, environment=p.environment.lock_text())
        return f"estilo {p.direction.style}, ritmo {p.direction.pace}, transição {p.direction.transition}"

    def _planning(self, ctx: RunContext) -> str:
        p = ctx.project
        plan = estimate_for(self.settings, p.duration, p.quality, p.engine, p.max_credits)
        if p.credits is not None:
            plan.used = p.credits.used
        check_estimate(plan)
        p.credits = plan
        if not p.scenes:
            p.scenes = director.plan_scenes(plan.durations, self.settings.generation_durations, p.brief, p.product_profile)
        ctx.log.info("planning", "cenas planejadas", durations=plan.durations, estimated=plan.estimated,
                     max_credits=plan.max_credits, beats=[s.beat for s in p.scenes])
        return " + ".join(f"{d:g}s" for d in plan.durations) + f" → estimativa {plan.estimated} créditos (teto {plan.max_credits})"

    def _prompts(self, ctx: RunContext) -> str:
        p = ctx.project
        gen_aspect = prompts.generation_aspect(p.aspect, self.settings.square_generation_aspect)
        for scene in p.scenes:
            scene.prompt = prompts.build_prompt(scene, len(p.scenes), p.aspect, p.direction, p.product_profile,
                                                p.environment)
            ctx.log.info("prompt", f"prompt da cena {scene.index}", scene=scene.index, prompt=scene.prompt)
        return f"{len(p.scenes)} prompts com Product/Environment/Continuity Lock (geração em {gen_aspect})"

    def _flow_check(self, ctx: RunContext) -> str:
        p = ctx.project
        if p.engine == "flow" and not p.credits_confirmed:
            raise BudgetError("Confirme a estimativa de créditos antes de gerar no Flow real.")
        engine = self._engine(ctx)
        report = engine.doctor()
        p.final_report["doctor"] = report.model_dump()
        ctx.log.info("doctor", report.message, ok=report.ok, real=report.real, failed_step=report.failed_step)
        if not report.ok:
            raise FlowError(f"Flow Doctor falhou — nenhum crédito foi usado. {report.message}",
                            step=report.failed_step or "doctor", url=report.url, screenshot=report.screenshot)
        return report.message

    def _generation(self, ctx: RunContext) -> str:
        p = ctx.project
        engine = self._engine(ctx)
        for scene in p.scenes:
            self._check_cancel(ctx)
            if scene.status in (SceneStatus.approved, SceneStatus.warning):
                continue
            previous = p.scenes[scene.index - 2] if scene.index > 1 else None
            if previous and self.settings.use_last_frame_as_start and previous.last_frame:
                scene.start_frame = previous.last_frame
            self._run_scene(ctx, engine, scene, previous)
        approved = sum(1 for s in p.scenes if s.status == SceneStatus.approved)
        warned = sum(1 for s in p.scenes if s.status == SceneStatus.warning)
        label = "créditos simulados" if p.credits.simulated else "créditos"
        return f"{approved} aprovadas, {warned} com alertas; {p.credits.used} {label} usados"

    def _run_scene(self, ctx: RunContext, engine, scene: Scene, previous: Scene | None) -> None:
        p = ctx.project
        plan = p.credits
        base = self._dir(p)
        table = load_credit_table(self.settings)
        model_match = table.get("modes", {}).get(p.quality, {}).get("model_match", "")
        gen_aspect = prompts.generation_aspect(p.aspect, self.settings.square_generation_aspect)
        fixes: list[str] = []

        # tentativa já baixada antes de uma queda: avalia sem gastar créditos de novo
        for attempt in scene.attempts:
            if attempt.status == "ok" and attempt.quality is None and attempt.video and (base / attempt.video).is_file():
                if self._check_attempt(ctx, scene, attempt, previous):
                    return
                fixes = attempt.quality.fixes if attempt.quality else []

        while True:
            self._check_cancel(ctx)
            counted = [a for a in scene.attempts if a.status in ("ok", "falha")]
            if len(counted) >= plan.max_attempts_per_scene:
                self._best_effort(ctx, scene, previous, "limite de tentativas atingido")
                return
            if not can_spend(plan):
                ctx.log.warn("credits", "teto de créditos atingido; nenhuma nova geração", used=plan.used,
                             max_credits=plan.max_credits, scene=scene.index)
                self._best_effort(ctx, scene, previous, "teto de créditos atingido")
                return
            number = len(scene.attempts) + 1
            prompt = prompts.build_prompt(scene, len(p.scenes), p.aspect, p.direction, p.product_profile,
                                          p.environment, fixes=fixes)
            out = base / "scenes" / f"scene_{scene.index:02d}" / f"attempt_{number}.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            # caminho gravado ANTES de gerar: após uma queda, um download completo é reaproveitado
            attempt = Attempt(number=number, engine=engine.name, prompt=prompt, credits=plan.credits_per_generation,
                              video=self._rel(p, out))
            scene.attempts.append(attempt)
            scene.status = SceneStatus.generating
            plan.used += attempt.credits  # contabiliza ANTES de enviar: clicar em gerar já consome
            self._save(ctx)
            ctx.log.info("generate", f"cena {scene.index}: tentativa {number}", scene=scene.index, attempt=number,
                         credits=attempt.credits, used=plan.used, prompt=prompt)
            request = GenerationRequest(
                project_id=p.id, scene_index=scene.index, attempt=number, prompt=prompt, aspect=gen_aspect,
                duration=scene.generation_duration, output_path=out,
                reference_images=[base / r for r in scene.reference_images],
                start_frame=(base / scene.start_frame) if scene.start_frame else None,
                model_match=model_match,
                environment_hint={"dark": "white" not in (p.environment.background if p.environment else "")},
            )
            try:
                engine.generate(request)
            except FlowError as exc:
                attempt.status = "falha"
                attempt.error = str(exc)
                attempt.finished_at = now_iso()
                if not exc.credits_spent:
                    plan.used -= attempt.credits
                    attempt.credits = 0
                self._save(ctx)
                ctx.log.error("generate", f"cena {scene.index}: falha na geração: {exc}", scene=scene.index,
                              attempt=number, step=exc.step, screenshot=exc.screenshot, retryable=exc.retryable)
                if exc.retryable:
                    continue
                raise
            attempt.status = "ok"
            self._save(ctx)
            ctx.log.info("download", f"cena {scene.index}: vídeo salvo", scene=scene.index, attempt=number,
                         file=attempt.video)
            if self._check_attempt(ctx, scene, attempt, previous):
                return
            fixes = attempt.quality.fixes if attempt.quality else []
            ctx.log.warn("quality", f"cena {scene.index} reprovada; regenerando SOMENTE esta cena", scene=scene.index,
                         attempt=number, failed=[c.name for c in attempt.quality.checks if c.status == "falha"])

    def _frames_dir(self, ctx: RunContext, scene: Scene, attempt: Attempt) -> Path:
        return self._dir(ctx.project) / "scenes" / f"scene_{scene.index:02d}" / f"attempt_{attempt.number}_frames"

    def _check_attempt(self, ctx: RunContext, scene: Scene, attempt: Attempt, previous: Scene | None) -> bool:
        p = ctx.project
        base = self._dir(p)
        scene.status = SceneStatus.checking
        self._save(ctx)
        video = base / attempt.video
        frames = quality.extract_scene_frames(self.settings, video, scene, self._frames_dir(ctx, scene, attempt))
        env_ref = base / p.environment.reference_image if p.environment and p.environment.reference_image else None
        prev_last = base / previous.last_frame if previous and previous.last_frame else None
        product_images = [base / a.path for a in p.product_assets if a.kind == "image"]
        report = quality.evaluate_scene(self.settings, video, scene, frames, product_images, env_ref, prev_last,
                                        brief=p.brief, judge=self.judge)
        attempt.quality = report
        attempt.finished_at = now_iso()
        ctx.log.info("quality", f"cena {scene.index} tentativa {attempt.number}: {'APROVADA' if report.passed else 'REPROVADA'}",
                     scene=scene.index, attempt=attempt.number, score=report.score,
                     checks={c.name: c.status for c in report.checks})
        if report.passed:
            self._approve(ctx, scene, attempt, frames, SceneStatus.approved)
            return True
        self._save(ctx)
        return False

    def _approve(self, ctx: RunContext, scene: Scene, attempt: Attempt, frames: dict[str, Path],
                 status: SceneStatus) -> None:
        p = ctx.project
        scene.approved_attempt = attempt.number
        scene.status = status
        scene.first_frame = self._rel(p, frames["first"])
        scene.last_frame = self._rel(p, frames["last"])
        if p.environment and not p.environment.reference_image:
            p.environment.reference_image = self._rel(p, frames["mid"])
            ctx.log.info("environment", "referência visual do Environment Master definida", image=p.environment.reference_image)
        self._save(ctx)

    def _best_effort(self, ctx: RunContext, scene: Scene, previous: Scene | None, reason: str) -> None:
        p = ctx.project
        candidates = [a for a in scene.attempts if a.status == "ok" and a.quality is not None]
        if not candidates or self.settings.on_quality_fail == "stop":
            raise QualityError(f"Cena {scene.index} sem vídeo aprovado ({reason}).")
        best = max(candidates, key=lambda a: a.quality.score)
        failed = [c.name for c in best.quality.checks if c.status == "falha"]
        message = f"Cena {scene.index}: usada a melhor tentativa ({best.number}) com alertas: {', '.join(failed)} — {reason}"
        scene.warnings.append(message)
        p.warnings.append(message)
        ctx.log.warn("quality", message, scene=scene.index)
        frames = {name: self._frames_dir(ctx, scene, best) / f"{name}.png" for name in ("first", "mid", "last")}
        self._approve(ctx, scene, best, frames, SceneStatus.warning)

    def _editing(self, ctx: RunContext) -> str:
        p = ctx.project
        base = self._dir(p)
        clips = [editor.ClipSpec(source=base / s.approved_video(), start=s.trim_start, target=s.target_duration)
                 for s in p.scenes]
        overlays = editor.Overlays(
            logo=base / p.logo.path if p.logo else None,
            cta=p.cta or (p.direction.cta if p.direction else ""),
            music=base / p.music.path if p.music else None,
        )
        output = base / "output" / f"adflow_{p.id}_{p.aspect.replace(':', 'x')}.mp4"
        result = editor.assemble(self.settings, clips, p.aspect, p.duration, p.direction.transition, overlays,
                                 base / "edit", output, log=ctx.log)
        p.final_report["edit"] = result
        p.warnings += result.get("notes", [])
        p.final_video = self._rel(p, output)
        cuts = sum(1 for j in result["joins"] if j == 0)
        return f"{len(clips)} cenas, {len(result['joins']) - cuts} transições, {cuts} cortes secos"

    def _export(self, ctx: RunContext) -> str:
        p = ctx.project
        path = self._dir(p) / p.final_video
        check = editor.verify_output(self.settings, path, p.aspect, p.duration)
        p.final_report["verify"] = check
        ctx.log.info("export", "verificação do MP4 final", ok=check["ok"], problems=check["problems"],
                     info=check["info"])
        if not check["ok"]:
            raise media.MediaError("MP4 final fora da especificação: " + "; ".join(check["problems"]))
        info = check["info"]
        return f"{info['width']}x{info['height']}, {info['duration']:.2f}s, {info['fps']:.0f} fps, H.264 + AAC"


class Runner:
    """Executa um projeto por vez em segundo plano (o navegador do Flow é um recurso único)."""

    def __init__(self, pipeline: Pipeline):
        self.pipeline = pipeline
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self.current: str | None = None

    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, project_id: str) -> None:
        with self._lock:
            if self.busy():
                raise RuntimeError(f"já existe um projeto em execução ({self.current})")
            self._cancel = threading.Event()
            self.current = project_id
            self._thread = threading.Thread(target=self._run, args=(project_id, self._cancel), daemon=True,
                                            name=f"adflow-{project_id}")
            self._thread.start()

    def _run(self, project_id: str, cancel: threading.Event) -> None:
        try:
            self.pipeline.run(project_id, cancel)
        finally:
            self.current = None

    def cancel(self, project_id: str) -> bool:
        if self.current == project_id and self.busy():
            self._cancel.set()
            return True
        return False

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)


def mark_interrupted(store: ProjectStore) -> list[str]:
    """Na inicialização: projetos que estavam 'running' ficaram órfãos (queda/reinício)."""
    marked = []
    for project in store.list():
        if project.status == ProjectStatus.running:
            project.status = ProjectStatus.interrupted
            store.save(project)
            marked.append(project.id)
    return marked
