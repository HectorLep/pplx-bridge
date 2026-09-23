"""Proveedores API REST (httpx) del puente.

- :class:`BaseApiProvider`: fontaneria HTTP sync/async, credenciales desde
  entorno o ``.env`` y adjuntos como bloques de contexto.
- :class:`GeminiProvider`: Google AI Studio (Gemini API) con alias
  ``gemini-flash`` / ``gemini-pro`` y auto-registro condicionado a
  ``GEMINI_API_KEY``.
"""

from .base import (
    DEFAULT_API_KEY_ENV,
    DEFAULT_TIMEOUT_S,
    BaseApiProvider,
    default_dotenv_paths,
    read_env_value,
)
from .gemini import (
    GEMINI_ALIASES,
    GEMINI_API_BASE,
    GEMINI_FLASH,
    GEMINI_PRO,
    GeminiProvider,
    build_gemini_providers,
    gemini_models_for,
    normalize_model,
    register_gemini_providers,
)

__all__ = [
    "DEFAULT_API_KEY_ENV",
    "DEFAULT_TIMEOUT_S",
    "GEMINI_ALIASES",
    "GEMINI_API_BASE",
    "GEMINI_FLASH",
    "GEMINI_PRO",
    "BaseApiProvider",
    "GeminiProvider",
    "build_gemini_providers",
    "default_dotenv_paths",
    "gemini_models_for",
    "normalize_model",
    "read_env_value",
    "register_gemini_providers",
]
