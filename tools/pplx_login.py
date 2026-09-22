"""Shim de compatibilidad: delega en ``python -m core_bridge.cli login``.

Mantiene la invocacion historica ``python tools/pplx_login.py
[--user-data-dir DIR] [--browser BROWSER] [--start-url URL] [--force]``
que usan ``setup.ps1`` / ``setup.sh``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core_bridge.browser import LOGIN_MARKER_NAME  # noqa: E402
from core_bridge.cli import main as _cli_main  # noqa: E402

MARKER_NAME = LOGIN_MARKER_NAME


def main(argv: list[str] | None = None) -> int:
    """Ejecuta el subcomando ``login`` del CLI unificado."""
    args = list(sys.argv[1:] if argv is None else argv)
    return _cli_main(["login", *args])


if __name__ == "__main__":
    raise SystemExit(main())
