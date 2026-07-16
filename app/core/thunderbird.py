from __future__ import annotations
import os
import subprocess
import tempfile
import webbrowser
from pathlib import Path
from typing import Optional, Tuple
from email.message import EmailMessage
from email.policy import SMTP

def detect_thunderbird_path(config_path: str = "") -> Optional[str]:
    # If provided and exists
    if config_path:
        p = Path(config_path)
        if p.exists():
            return str(p)

    candidates = [
        r"C:\Program Files\Mozilla Thunderbird\thunderbird.exe",
        r"C:\Program Files (x86)\Mozilla Thunderbird\thunderbird.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None

def _escape_compose_value(s: str) -> str:
    # Thunderbird -compose supports quoting with single quotes.
    # Escape backslashes and single quotes.
    s = s.replace("\\", "\\\\")
    s = s.replace("'", "\\'")
    return s

def open_compose(thunderbird_exe: str, to: str, subject: str, body: str) -> Tuple[bool, str]:
    """Try open thunderbird compose. Returns (ok, message)."""
    try:
        to_v = _escape_compose_value(to)
        subject_v = _escape_compose_value(subject)
        body_v = _escape_compose_value(body)
        compose = f"to='{to_v}',subject='{subject_v}',body='{body_v}'"
        subprocess.Popen([thunderbird_exe, "-compose", compose], close_fds=True)
        return True, "Compose aberto no Thunderbird."
    except Exception as e:
        return False, f"Falha ao abrir compose no Thunderbird: {e}"

def open_mailto(to: str, subject: str, body: str) -> Tuple[bool, str]:
    try:
        # Encode using urllib.parse.quote
        import urllib.parse
        url = f"mailto:{urllib.parse.quote(to)}?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body)}"
        ok = webbrowser.open(url)
        return bool(ok), "Abrindo mailto no cliente padrão."
    except Exception as e:
        return False, f"Falha ao abrir mailto: {e}"

def open_eml_fallback(to: str, subject: str, body: str) -> Tuple[bool, str]:
    """Generate a temporary .eml and open it. Best fallback for long bodies."""
    try:
        msg = EmailMessage(policy=SMTP)
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        tmp_dir = Path(tempfile.gettempdir()) / "ComprasApp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        eml_path = tmp_dir / f"cotacao_{os.getpid()}_{abs(hash((to, subject))) % 10_000_000}.eml"
        eml_path.write_bytes(msg.as_bytes())

        # On Windows, os.startfile will open with default app (Thunderbird when installed)
        if hasattr(os, "startfile"):
            os.startfile(str(eml_path))  # type: ignore[attr-defined]
            return True, f"Arquivo EML aberto: {eml_path}"
        else:
            # fallback for non-windows: open via webbrowser or xdg-open
            subprocess.Popen(["xdg-open", str(eml_path)])
            return True, f"Arquivo EML aberto: {eml_path}"
    except Exception as e:
        return False, f"Falha ao gerar/abrir EML: {e}"

def open_email_best_effort(thunderbird_exe: Optional[str], to: str, subject: str, body: str) -> Tuple[bool, str]:
    """Attempt: compose (tb) -> mailto -> eml."""
    if thunderbird_exe:
        ok, msg = open_compose(thunderbird_exe, to, subject, body)
        if ok:
            return ok, msg

    ok, msg = open_mailto(to, subject, body)
    if ok:
        return ok, msg

    return open_eml_fallback(to, subject, body)
