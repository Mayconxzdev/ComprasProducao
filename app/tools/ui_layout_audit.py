from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UI layout audit entrypoint")
    parser.parse_args(argv)
    legacy_path = Path(__file__).resolve().parents[1] / "legacy_ui_ctk" / "ui_layout_audit.py"
    print(
        "Legacy CTk layout audit foi movido para:",
        legacy_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
