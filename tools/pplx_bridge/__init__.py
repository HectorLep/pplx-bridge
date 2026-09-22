"""Shim de compatibilidad: reexporta ``core_bridge`` (puente web generico)."""

try:
    from .browser_client import ask_perplexity, get_browser  # noqa: F401
except ImportError:  # pragma: no cover - ejecucion como script dentro de su carpeta
    from browser_client import ask_perplexity, get_browser  # type: ignore[no-redef]  # noqa: F401

__all__ = ["ask_perplexity", "get_browser"]
