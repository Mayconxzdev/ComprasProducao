# Performance Diagnostic Tool for ComprasApp
# Usage: python -m app.tools.perf_diag

import time
import json
import os
import sys
from pathlib import Path
from typing import Dict, List
import logging

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PerfTimer:
    """Context manager for timing operations"""
    def __init__(self, name: str):
        self.name = name
        self.start = 0
        self.duration_ms = 0

    def __enter__(self):
        self.start = time.perf_counter()
        logger.info(f"PERF:STAGE_START {self.name}")
        return self

    def __exit__(self, *args):
        self.duration_ms = (time.perf_counter() - self.start) * 1000
        logger.info(f"PERF:STAGE_END {self.name} dur_ms={self.duration_ms:.2f}")


def measure_nas_access(filepath: str, iterations: int = 5) -> Dict:
    """Measure NAS file access time"""
    results = {
        "filepath": filepath,
        "exists": os.path.exists(filepath),
        "timings_ms": [],
        "file_size_kb": 0
    }

    if not results["exists"]:
        return results

    # Measure file size
    results["file_size_kb"] = os.path.getsize(filepath) / 1024

    # Measure open/read times
    for i in range(iterations):
        with PerfTimer(f"NAS_ACCESS_iteration_{i+1}") as timer:
            try:
                with open(filepath, 'rb') as f:
                    _ = f.read(1024)  # Read first KB only
                results["timings_ms"].append(timer.duration_ms)
            except Exception as e:
                logger.error(f"Failed to access NAS: {e}")
                results["timings_ms"].append(-1)

    # Calculate stats
    valid_timings = [t for t in results["timings_ms"] if t > 0]
    if valid_timings:
        results["min_ms"] = min(valid_timings)
        results["max_ms"] = max(valid_timings)
        results["avg_ms"] = sum(valid_timings) / len(valid_timings)
        results["p95_ms"] = sorted(valid_timings)[int(len(valid_timings) * 0.95)]

    return results


def measure_xlsx_loading(filepath: str) -> Dict:
    """Measure openpyxl loading time"""
    results = {
        "filepath": filepath,
        "load_time_ms": 0,
        "read_only": True,
        "success": False
    }

    try:
        import openpyxl

        with PerfTimer("XLSX_LOAD_openpyxl") as timer:
            wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
            sheet_names = wb.sheetnames
            wb.close()

        results["load_time_ms"] = timer.duration_ms
        results["sheet_count"] = len(sheet_names)
        results["sheet_names"] = sheet_names
        results["success"] = True

    except Exception as e:
        logger.error(f"Failed to load XLSX: {e}")
        results["error"] = str(e)

    return results


def measure_index_building(xlsx_path: str) -> Dict:
    """Measure index building performance"""
    results = {
        "parse_time_ms": 0,
        "dedupe_time_ms": 0,
        "total_time_ms": 0,
        "supplier_count": 0
    }

    try:
        from app.core.data_manager import build_index

        with PerfTimer("INDEX_BUILD_total") as total_timer:
            index, load_result = build_index([xlsx_path])
            results["supplier_count"] = load_result.suppliers_count

        results["total_time_ms"] = total_timer.duration_ms

    except Exception as e:
        logger.error(f"Failed to build index: {e}")
        results["error"] = str(e)

    return results


def measure_search_performance(index, queries: List[str]) -> Dict:
    """Measure search performance for various queries"""
    results = {"queries": []}

    for query in queries:
        query_result = {
            "query": query,
            "compute_time_ms": 0,
            "result_count": 0
        }

        try:
            with PerfTimer(f"SEARCH_compute_query={query}") as timer:
                search_results = index.search(query)
                query_result["result_count"] = len(search_results)

            query_result["compute_time_ms"] = timer.duration_ms

        except Exception as e:
            query_result["error"] = str(e)

        results["queries"].append(query_result)

    return results


def main():
    """Run full performance diagnostics"""
    print("=" * 60)
    print("ComprasApp Performance Diagnostic Tool")
    print("=" * 60)

    # Configuration
    nas_path = os.environ.get("COMPRAS_VESPER_CATALOG_PATH", "")
    local_test_path = ""

    # Use local path for testing if NAS not available
    test_path = local_test_path if local_test_path and os.path.exists(local_test_path) else nas_path
    if not test_path:
        raise SystemExit("Defina COMPRAS_VESPER_CATALOG_PATH para executar este diagnóstico.")

    perf_results = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "test_file": test_path,
        "diagnostics": {}
    }

    # 1. NAS Access Test
    print("\n[1/5] Testing NAS file access...")
    perf_results["diagnostics"]["nas_access"] = measure_nas_access(test_path, iterations=5)
    print(f"  → Avg: {perf_results['diagnostics']['nas_access'].get('avg_ms', 0):.2f}ms")

    # 2. XLSX Loading Test
    print("\n[2/5] Testing XLSX loading (openpyxl)...")
    perf_results["diagnostics"]["xlsx_load"] = measure_xlsx_loading(test_path)
    print(f"  → Duration: {perf_results['diagnostics']['xlsx_load']['load_time_ms']:.2f}ms")

    # 3. Index Building Test
    print("\n[3/5] Testing index building...")
    perf_results["diagnostics"]["index_build"] = measure_index_building(test_path)
    print(f"  → Duration: {perf_results['diagnostics']['index_build']['total_time_ms']:.2f}ms")
    print(f"  → Suppliers: {perf_results['diagnostics']['index_build']['supplier_count']}")

    # 4. Search Performance Test
    print("\n[4/5] Testing search performance...")
    from app.core.data_manager import build_index
    index, _ = build_index([test_path])

    test_queries = ["chapa", "aço", "parafuso", "tubo", ""]
    perf_results["diagnostics"]["search"] = measure_search_performance(index, test_queries)
    for q in perf_results["diagnostics"]["search"]["queries"]:
        print(f"  → '{q['query']}': {q['compute_time_ms']:.2f}ms ({q['result_count']} results)")

    # 5. Save Results
    print("\n[5/5] Saving results...")
    output_file = "perf_results.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(perf_results, f, indent=2, ensure_ascii=False)
    print(f"  → Results saved to: {output_file}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"NAS Access (avg):     {perf_results['diagnostics']['nas_access'].get('avg_ms', 0):.2f}ms")
    print(f"XLSX Load:            {perf_results['diagnostics']['xlsx_load']['load_time_ms']:.2f}ms")
    print(f"Index Build:          {perf_results['diagnostics']['index_build']['total_time_ms']:.2f}ms")
    print(f"Search 'chapa':       {perf_results['diagnostics']['search']['queries'][0]['compute_time_ms']:.2f}ms")
    print("=" * 60)


if __name__ == "__main__":
    main()
