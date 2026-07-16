from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PATTERNS = set()
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "dist_installer",
    "visual-build",
    "node_modules",
}
RUNTIME_SUFFIXES = {".log", ".lock"}
ALLOWED_LOCKFILES = {"uv.lock"}
ALLOW_UNUSED = {
    ("app/qt/app.py", "_qt_resources"),  # Qt resource import has side effect
}
EXPECTED_VERSION_PREFIX = "4.8.0"


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _iter_python_files() -> list[Path]:
    return [p for p in ROOT.rglob("*.py") if not _is_excluded(p)]


def _is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_DIRS or part.startswith(".demo-runtime") for part in path.parts)


def _unused_imports(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(text)
    imported: list[tuple[str, str, int]] = []
    used: set[str] = set()

    class UsedNames(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:  # noqa: N802 - AST API
            used.add(node.id)

        def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802 - AST API
            self.generic_visit(node)

    UsedNames().visit(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append((alias.asname or alias.name.split(".")[0], alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported.append((alias.asname or alias.name, f"{node.module or ''}.{alias.name}", node.lineno))

    rel = _rel(path)
    if path.name == "__init__.py":
        return []
    problems: list[str] = []
    for local_name, full_name, line in imported:
        if (rel, local_name) in ALLOW_UNUSED:
            continue
        if local_name not in used:
            problems.append(f"{rel}:{line}: unused import {full_name} as {local_name}")
    return problems


def _runtime_artifacts() -> list[str]:
    problems: list[str] = []
    for path in ROOT.rglob("*"):
        if _is_excluded(path):
            continue
        rel = _rel(path)
        if any(part in RUNTIME_PATTERNS for part in path.parts):
            problems.append(f"runtime folder artifact: {rel}")
            continue
        if path.is_file() and path.suffix.lower() in RUNTIME_SUFFIXES and path.name not in ALLOWED_LOCKFILES:
            problems.append(f"runtime file artifact: {rel}")
    if (ROOT / "ComprasApp").exists():
        problems.append("runtime data folder should not be packaged: ComprasApp")
    return problems


def _version_problems() -> list[str]:
    version_path = ROOT / "version.txt"
    if not version_path.exists():
        return ["version.txt missing"]
    text = version_path.read_text(encoding="utf-8").strip()
    if not text.startswith(EXPECTED_VERSION_PREFIX):
        return [f"version.txt should start with {EXPECTED_VERSION_PREFIX}, got {text!r}"]
    return []


def main() -> int:
    problems: list[str] = []
    for path in _iter_python_files():
        problems.extend(_unused_imports(path))
    problems.extend(_runtime_artifacts())
    problems.extend(_version_problems())
    if problems:
        print("STATIC_CLEAN_AUDIT_FAILED")
        for problem in problems[:200]:
            print(problem)
        if len(problems) > 200:
            print(f"... +{len(problems) - 200} more")
        return 2
    print("STATIC_CLEAN_AUDIT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
