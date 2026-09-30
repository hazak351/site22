"""Persistência de projetos (JSON com escrita atômica) para recuperação após falhas."""

from __future__ import annotations

import os
import secrets
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import Settings
from .models import Project, now_iso


class ProjectStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.projects_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def new_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)

    def project_dir(self, project_id: str) -> Path:
        if not project_id or any(c in project_id for c in "/\\.:"):
            raise ValueError("id de projeto inválido")
        return self.root / project_id

    def path(self, project: Project | str, relative: str) -> Path:
        project_id = project if isinstance(project, str) else project.id
        return self.project_dir(project_id) / relative

    def save(self, project: Project) -> None:
        project.updated_at = now_iso()
        folder = self.project_dir(project.id)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "project.json"
        tmp = folder / f"project.json.{os.getpid()}.{threading.get_ident()}.tmp"
        data = project.model_dump_json(indent=2)
        with self._lock:
            tmp.write_text(data, encoding="utf-8")
            for attempt in range(20):
                try:
                    os.replace(tmp, target)
                    break
                except PermissionError:  # Windows: arquivo aberto por outro leitor
                    time.sleep(0.05 * (attempt + 1))
            else:
                raise PermissionError(f"não foi possível gravar {target}")

    def load(self, project_id: str) -> Project:
        target = self.project_dir(project_id) / "project.json"
        for attempt in range(20):
            try:
                return Project.model_validate_json(target.read_text(encoding="utf-8"))
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        raise PermissionError(f"não foi possível ler {target}")

    def exists(self, project_id: str) -> bool:
        try:
            return (self.project_dir(project_id) / "project.json").is_file()
        except ValueError:
            return False

    def list(self) -> list[Project]:
        projects = []
        for folder in sorted(self.root.iterdir(), reverse=True):
            if (folder / "project.json").is_file():
                try:
                    projects.append(self.load(folder.name))
                except Exception:
                    continue
        return projects

    def save_upload(self, project_id: str, subdir: str, filename: str, data: bytes) -> str:
        """Salva um arquivo enviado e devolve o caminho relativo à pasta do projeto."""
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in Path(filename).name) or "arquivo"
        folder = self.project_dir(project_id) / subdir
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / safe
        counter = 1
        while target.exists():
            target = folder / f"{Path(safe).stem}_{counter}{Path(safe).suffix}"
            counter += 1
        target.write_bytes(data)
        return str(target.relative_to(self.project_dir(project_id))).replace("\\", "/")

    def delete(self, project_id: str) -> None:
        shutil.rmtree(self.project_dir(project_id), ignore_errors=True)
