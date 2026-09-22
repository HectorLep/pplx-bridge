"""Permite ``python -m core_bridge`` como alias de ``python -m core_bridge.cli``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
