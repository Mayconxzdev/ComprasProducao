from __future__ import annotations

import statistics
import time

from app.core.catalog_db import CatalogDB
from app.core.config import AppConfig


def main() -> int:
    cfg = AppConfig.load()
    db = CatalogDB(cfg)

    t0 = time.perf_counter()
    ok, msg = db.rebuild_if_needed(force=False)
    t_open = (time.perf_counter() - t0) * 1000

    samples = []
    for _ in range(20):
        s = time.perf_counter()
        rows = db.query_suppliers("chapa inox", limit=100)
        samples.append((time.perf_counter() - s) * 1000)

    sim_render_start = time.perf_counter()
    _ = [f"{r.name}|{r.email}|{r.base_score}" for r in rows]
    render_ms = (time.perf_counter() - sim_render_start) * 1000

    print(f"reindex_or_check_ok={ok} msg={msg}")
    print(f"db_open_or_check_ms={t_open:.2f}")
    print(f"query_top100_avg_ms={statistics.mean(samples):.2f}")
    print(f"query_top100_p95_ms={statistics.quantiles(samples, n=20)[-1]:.2f}")
    print(f"render_sim_ms={render_ms:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
