from __future__ import annotations

import ctypes


def enable_windows_dpi_awareness() -> str:
    """
    Best-effort DPI awareness for Windows before creating QApplication widgets.
    """
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        # PER_MONITOR_AWARE_V2 = -4
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "PerMonitorV2"
    except Exception:
        pass
    try:
        shcore = ctypes.windll.shcore  # type: ignore[attr-defined]
        # PROCESS_SYSTEM_DPI_AWARE = 1
        shcore.SetProcessDpiAwareness(1)
        return "SystemAware"
    except Exception:
        return "Legacy"
