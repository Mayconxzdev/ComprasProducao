from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from app.core.update_service import UpdateService


class UpdateWorkerSignals(QObject):
    state = Signal(str, object)  # state_name, payload


class UpdateWorker(QRunnable):
    def __init__(
        self,
        *,
        service: UpdateService,
        mode: str = "check_and_download",
        signals: UpdateWorkerSignals | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.mode = str(mode or "check_and_download")
        self.signals = signals or UpdateWorkerSignals()

    def _emit(self, state: str, payload: dict[str, Any] | None = None) -> None:
        try:
            self.signals.state.emit(str(state), dict(payload or {}))
        except RuntimeError:
            # QObject de sinais pode ser destruido durante fechamento da UI.
            return

    def run(self) -> None:
        if self.mode not in {"check_and_download", "retry_download"}:
            self._emit("idle", {"message": "Modo de update desconhecido."})
            return

        self._emit("checking", {})
        check = self.service.check_for_update()
        if not check.available or check.manifest is None:
            self._emit(
                "idle",
                {
                    "current_version": check.current_version,
                    "latest_version": check.latest_version,
                    "message": check.message,
                },
            )
            return

        latest = check.latest_version
        current = check.current_version
        self._emit(
            "available_downloading",
            {
                "current_version": current,
                "latest_version": latest,
                "progress": 0,
            },
        )

        def _progress(percent: int) -> None:
            self._emit(
                "available_downloading",
                {
                    "current_version": current,
                    "latest_version": latest,
                    "progress": int(percent),
                },
            )

        download = self.service.download_update(check.manifest, progress_cb=_progress)
        if download.ok:
            self._emit(
                "ready_to_install",
                {
                    "current_version": current,
                    "latest_version": latest,
                    "installer_path": download.installer_path,
                    "message": download.message,
                },
            )
            return

        self._emit(
            "error_download",
            {
                "current_version": current,
                "latest_version": latest,
                "message": download.message,
            },
        )
