from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .cache_manager import get_cache_file_path, get_xlsx_path
from .config import AppConfig, demo_mode_enabled
from .path_utils import normalize_master_path


def _clean(value: str) -> str:
    return str(value or "").strip()


def _dedupe_paths(values: List[str]) -> List[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        path = normalize_master_path(_clean(raw))
        if not path:
            continue
        key = path.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


@dataclass
class RuntimeBootstrapResult:
    ok: bool
    message: str
    nas_path: str = ""
    cache_path: str = ""
    cache_exists: bool = False
    warnings: list[str] = field(default_factory=list)


def ensure_runtime_bootstrap(config: AppConfig, *, force_refresh: bool = False, materialize_cache: bool | None = None) -> RuntimeBootstrapResult:
    """
    Make startup deterministic for clean PCs/user profiles.

    Rules:
    - Always keep runtime xlsx sources with NAS path + local cache path.
    - Startup path must stay cheap: by default this only prepares runtime paths.
    - Cache materialization is reserved for explicit refresh/repair flows.
    """
    warnings: list[str] = []

    # A demonstração pública usa uma planilha local versionada. Não acrescentar
    # um cache inexistente como uma segunda fonte evita alertas de arquivo
    # ausente e garante que o primeiro uso seja reproduzível em qualquer PC.
    if demo_mode_enabled():
        config.xlsx_sources = _dedupe_paths(list(config.xlsx_sources or []))
        return RuntimeBootstrapResult(
            ok=bool(config.xlsx_sources),
            message="Bootstrap de demonstração pronto: base local carregada.",
            cache_path="",
            cache_exists=False,
        )

    nas_path = normalize_master_path(_clean(config.nas_master_path))
    cache_path_obj = get_cache_file_path()
    cache_path = str(cache_path_obj)
    cache_exists = cache_path_obj.exists()

    if materialize_cache is None:
        materialize_cache = bool(force_refresh)

    if nas_path.lower().endswith((".xlsx", ".xlsm")):
        # Evita cópia/estatística pesada no startup. Fornecedores e Configurações
        # chamam com force_refresh=True quando o usuário realmente precisa atualizar.
        if materialize_cache and (force_refresh or not cache_exists):
            resolved, msg = get_xlsx_path(nas_path, force_refresh=force_refresh or not cache_exists)
            if resolved:
                cache_exists = Path(resolved).exists()
            else:
                warnings.append(msg or "Falha ao sincronizar cache local do NAS.")
    elif nas_path:
        warnings.append("Caminho NAS configurado nao parece arquivo XLSX.")
    else:
        warnings.append("Caminho NAS nao configurado.")

    runtime_sources = _dedupe_paths(
        [nas_path] + list(config.xlsx_sources or []) + [cache_path]
    )
    if runtime_sources != list(config.xlsx_sources or []):
        config.xlsx_sources = runtime_sources
        try:
            config.save()
        except Exception as exc:
            warnings.append(f"Falha ao salvar configuracao local: {exc}")

    ok = cache_exists or bool(nas_path)
    if cache_exists:
        message = "Bootstrap pronto: cache local de fornecedores disponivel."
    elif nas_path:
        message = "Bootstrap parcial: NAS configurado, cache local ainda indisponivel."
    else:
        message = "Bootstrap incompleto: sem NAS e sem cache local."

    return RuntimeBootstrapResult(
        ok=ok,
        message=message,
        nas_path=nas_path,
        cache_path=cache_path,
        cache_exists=cache_exists,
        warnings=warnings,
    )
