"""Proveedores web (Playwright)."""

from .base import BaseWebProvider
from .perplexity import PERPLEXITY_MODEL, PerplexityProvider

__all__ = ["PERPLEXITY_MODEL", "BaseWebProvider", "PerplexityProvider"]
