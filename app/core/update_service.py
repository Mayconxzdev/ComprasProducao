from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .config import AppConfig, ensure_app_data_dir
from .path_utils import nas_fallback_candidates


def _normalize_version(raw: str) -> str:
    value = str(raw or "").strip()
    if value.lower().startswith("v"):
        return value[1:]
    return value


def _version_key(raw: str) -> tuple[int, ...]:
    parts = []
    for token in _normalize_version(raw).split("."):
        token = token.strip()
        if token.isdigit():
            parts.append(int(token))
        else:
            digits = "".join(ch for ch in token if ch.isdigit())
            parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:6])


def _is_version_newer(latest: str, current: str) -> bool:
    return _version_key(latest) > _version_key(current)


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            return raw
    except Exception:
        return None
    return None


def _safe_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass(frozen=True)
class UpdateManifest:
    app: str
    channel: str
    latest_version: str
    installer_name: str
    installer_sha256: str
    source_root: str = ""
    mandatory: bool = False
    notes: str = ""
    published_at: str = ""


@dataclass(frozen=True)
class UpdateCheckResult:
    available: bool
    current_version: str
    latest_version: str = ""
    manifest: UpdateManifest | None = None
    message: str = ""


@dataclass(frozen=True)
class UpdateDownloadResult:
    ok: bool
    latest_version: str = ""
    installer_path: str = ""
    message: str = ""


class UpdateService:
    MANIFEST_FILE = "manifest.json"
    UPDATE_STATE_FILE = "update_state.json"
    DEFAULT_SOURCE_ROOT = ""

    def __init__(self, config: AppConfig):
        self.config = config

    @staticmethod
    def current_version() -> str:
        def _read_version_file(path: Path) -> str:
            try:
                if not path.exists():
                    return ""
                text = _normalize_version(path.read_text(encoding="utf-8-sig").strip())
                return text
            except Exception:
                return ""

        env_version = _normalize_version(os.environ.get("COMPRASVESPER_VERSION", ""))
        if env_version:
            return env_version

        try:
            exe_dir = Path(sys.executable).resolve().parent
            for candidate in (exe_dir / "_internal" / "version.txt", exe_dir / "version.txt"):
                text = _read_version_file(candidate)
                if text:
                    return text
        except Exception:
            pass

        try:
            repo_version = _read_version_file(Path(__file__).resolve().parents[2] / "version.txt")
            if repo_version:
                return repo_version
        except Exception:
            pass
        return "0.0.0"

    @staticmethod
    def update_state_path() -> Path:
        return ensure_app_data_dir() / UpdateService.UPDATE_STATE_FILE

    @staticmethod
    def updates_dir() -> Path:
        path = ensure_app_data_dir() / "updates"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def load_update_state() -> dict[str, Any]:
        state_path = UpdateService.update_state_path()
        raw = _safe_read_json(state_path)
        if isinstance(raw, dict):
            return raw
        return {
            "current_version": UpdateService.current_version(),
            "last_check_at": "",
            "last_seen_version": "",
            "downloaded_installer_path": "",
            "downloaded_sha256": "",
            "pending_apply": False,
        }

    @staticmethod
    def save_update_state(state: dict[str, Any]) -> None:
        _safe_write_json(UpdateService.update_state_path(), state)

    def _candidate_source_roots(self) -> list[Path]:
        roots: list[Path] = []
        configured = str(self.config.update_source_path or "").strip()
        if configured:
            for candidate in nas_fallback_candidates(configured) or [configured]:
                roots.append(Path(candidate))

            lowered = configured.replace("/", "\\").lower()
            marker = "\\releases\\stable"
            if lowered.endswith(marker):
                base = Path(configured)
                parent = base.parent.parent if base.parent and base.parent.parent else None
                if parent is not None:
                    for candidate in nas_fallback_candidates(str(parent)) or [str(parent)]:
                        roots.append(Path(candidate))

        for candidate in nas_fallback_candidates(self.DEFAULT_SOURCE_ROOT) or [self.DEFAULT_SOURCE_ROOT]:
            roots.append(Path(candidate))

        dedup: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            token = str(root).replace("/", "\\").rstrip("\\").lower()
            if not token or token in seen:
                continue
            dedup.append(root)
            seen.add(token)
        return dedup

    def load_manifest(self) -> tuple[UpdateManifest | None, str]:
        selected_root: Path | None = None
        raw: dict[str, Any] | None = None
        attempted: list[str] = []
        for root in self._candidate_source_roots():
            manifest_path = root / self.MANIFEST_FILE
            attempted.append(str(manifest_path))
            payload = _safe_read_json(manifest_path)
            if payload:
                selected_root = root
                raw = payload
                break
        if not raw or selected_root is None:
            attempts = " | ".join(attempted) if attempted else "(nenhum caminho)"
            return None, f"Manifesto nao encontrado. Caminhos tentados: {attempts}"

        latest_version = _normalize_version(str(raw.get("latest_version") or ""))
        installer_name = str(raw.get("installer_name") or "").strip()
        installer_sha256 = str(raw.get("installer_sha256") or "").strip().lower()
        app = str(raw.get("app") or "ComprasVesper").strip() or "ComprasVesper"
        channel = str(raw.get("channel") or "stable").strip() or "stable"

        if not latest_version:
            return None, "Manifesto invalido: latest_version ausente."
        if not installer_name:
            return None, "Manifesto invalido: installer_name ausente."
        if len(installer_sha256) < 64:
            return None, "Manifesto invalido: installer_sha256 ausente ou incompleto."

        manifest = UpdateManifest(
            app=app,
            channel=channel,
            latest_version=latest_version,
            installer_name=installer_name,
            installer_sha256=installer_sha256,
            source_root=str(selected_root),
            mandatory=bool(raw.get("mandatory", False)),
            notes=str(raw.get("notes") or ""),
            published_at=str(raw.get("published_at") or ""),
        )
        return manifest, ""

    def check_for_update(self) -> UpdateCheckResult:
        if not bool(getattr(self.config, "update_enabled", True)):
            return UpdateCheckResult(
                available=False,
                current_version=self.current_version(),
                message="Atualizacao desativada.",
            )

        manifest, error = self.load_manifest()
        current = self.current_version()
        if manifest is None:
            return UpdateCheckResult(
                available=False,
                current_version=current,
                message=error or "Manifesto indisponivel.",
            )

        available = _is_version_newer(manifest.latest_version, current)
        return UpdateCheckResult(
            available=available,
            current_version=current,
            latest_version=manifest.latest_version,
            manifest=manifest,
            message="update_available" if available else "up_to_date",
        )

    @staticmethod
    def compute_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest().lower()

    def download_update(
        self,
        manifest: UpdateManifest,
        *,
        progress_cb: Optional[Callable[[int], None]] = None,
    ) -> UpdateDownloadResult:
        source_root_raw = str(manifest.source_root or self.config.update_source_path or "").strip()
        source_installer: Path | None = None
        source_candidates = nas_fallback_candidates(source_root_raw) or ([source_root_raw] if source_root_raw else [])
        for root in source_candidates:
            candidate = Path(root) / manifest.installer_name
            if candidate.exists():
                source_installer = candidate
                break
        if source_installer is None:
            return UpdateDownloadResult(
                ok=False,
                latest_version=manifest.latest_version,
                message=f"Instalador nao encontrado. Caminhos: {' | '.join(str(Path(r) / manifest.installer_name) for r in source_candidates)}",
            )

        target = self.updates_dir() / manifest.installer_name
        total_size = max(1, int(source_installer.stat().st_size))
        copied = 0
        try:
            with source_installer.open("rb") as src, target.open("wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
                    copied += len(chunk)
                    if progress_cb is not None:
                        progress_cb(min(100, int((copied * 100) / total_size)))
        except Exception as exc:
            return UpdateDownloadResult(
                ok=False,
                latest_version=manifest.latest_version,
                message=f"Falha ao baixar update: {exc}",
            )

        calculated = self.compute_sha256(target)
        expected = manifest.installer_sha256.strip().lower()
        if calculated != expected:
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass
            return UpdateDownloadResult(
                ok=False,
                latest_version=manifest.latest_version,
                message="Hash do instalador nao confere (guard-rail).",
            )

        state = self.load_update_state()
        state.update(
            {
                "current_version": self.current_version(),
                "last_seen_version": manifest.latest_version,
                "downloaded_installer_path": str(target),
                "downloaded_sha256": calculated,
                "pending_apply": True,
            }
        )
        self.save_update_state(state)
        if progress_cb is not None:
            progress_cb(100)
        return UpdateDownloadResult(
            ok=True,
            latest_version=manifest.latest_version,
            installer_path=str(target),
            message="Update baixado e validado.",
        )

    def schedule_install_and_restart(
        self,
        installer_path: str,
        *,
        current_executable: str | None = None,
    ) -> tuple[bool, str]:
        installer = Path(str(installer_path or "").strip())
        if not installer.exists():
            return False, "Instalador nao encontrado para aplicar update."

        current_exe = str(current_executable or sys.executable)
        script_path = self.updates_dir() / "apply_update.cmd"
        script = (
            "@echo off\r\n"
            "setlocal\r\n"
            "timeout /t 1 /nobreak >nul\r\n"
            f"start /wait \"\" \"{installer}\" /VERYSILENT /NORESTART /SP- /CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS\r\n"
            f"if exist \"{current_exe}\" start \"\" \"{current_exe}\"\r\n"
            "endlocal\r\n"
        )
        try:
            script_path.write_text(script, encoding="utf-8")
            creationflags = 0
            creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(
                ["cmd", "/c", str(script_path)],
                creationflags=creationflags,
                close_fds=True,
            )
            state = self.load_update_state()
            state["pending_apply"] = False
            self.save_update_state(state)
            return True, "Instalacao agendada."
        except Exception as exc:
            return False, f"Falha ao agendar instalacao: {exc}"
