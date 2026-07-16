from __future__ import annotations

import json
import subprocess
import sys
import time
from app.catalog.product_catalog import generate_catalog_quality_report
from app.core.config import ensure_app_data_dir


def _run_step(name: str, cmd: list[str]) -> dict:
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        exit_code = int(proc.returncode)
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"\nTIMEOUT after {exc.timeout}s"
    dt = (time.perf_counter() - t0) * 1000.0
    return {
        "name": name,
        "cmd": cmd,
        "exit_code": exit_code,
        "duration_ms": round(dt, 2),
        "stdout": str(stdout)[-20000:],
        "stderr": str(stderr)[-20000:],
    }


def _run_catalog_quality_step() -> dict:
    t0 = time.perf_counter()
    report_path = ensure_app_data_dir() / "reports" / "catalog_quality_report.json"
    try:
        report = generate_catalog_quality_report(strict=True, report_path=report_path)
        ok = bool(report.get("valid", False))
        exit_code = 0 if ok else 2
        stdout = json.dumps(report, ensure_ascii=False)
        stderr = ""
    except Exception as exc:
        report = {"valid": False, "error": str(exc)}
        exit_code = 2
        stdout = json.dumps(report, ensure_ascii=False)
        stderr = str(exc)

    dt = (time.perf_counter() - t0) * 1000.0
    return {
        "name": "catalog_quality",
        "cmd": ["internal", "catalog_quality"],
        "exit_code": int(exit_code),
        "duration_ms": round(dt, 2),
        "stdout": stdout[-20000:],
        "stderr": stderr[-20000:],
        "report_path": str(report_path),
    }


def main() -> int:
    py = sys.executable
    results = []
    catalog_step = _run_catalog_quality_step()
    results.append(catalog_step)
    print(f"{catalog_step['name']}: exit={catalog_step['exit_code']} dur_ms={catalog_step['duration_ms']}")
    if catalog_step["exit_code"] != 0:
        ok = False
        report = {
            "ok": ok,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "results": results,
        }
        reports_dir = ensure_app_data_dir() / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / "quality_gate.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"quality_gate_report={path}")
        return 2

    steps = [
        ("compileall", [py, "-m", "compileall", "app"]),
        ("pytest", [py, "-m", "pytest", "-q"]),
        ("ui_layout_audit", [py, "-m", "app.tools.ui_layout_audit"]),
        ("clean_runtime_artifacts", [py, "-m", "app.tools.clean_runtime_artifacts"]),
        ("static_clean_audit", [py, "-m", "app.tools.static_clean_audit"]),
    ]

    for name, cmd in steps:
        res = _run_step(name, cmd)
        results.append(res)
        print(f"{name}: exit={res['exit_code']} dur_ms={res['duration_ms']}")
        if res["exit_code"] != 0:
            break

    ok = all(r["exit_code"] == 0 for r in results) and len(results) == (len(steps) + 1)
    report = {
        "ok": ok,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": results,
    }
    reports_dir = ensure_app_data_dir() / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "quality_gate.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"quality_gate_report={path}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
