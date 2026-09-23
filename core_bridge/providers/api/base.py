"""Base de proveedores API: HTTP (httpx) sincrono/asincrono y adjuntos.

``BaseApiProvider`` completa :class:`~core_bridge.providers.base.BaseProvider`
con la fontaneria compartida por los backends que hablan con APIs REST
(empezando por Gemini):

- clave de API resuelta de ``os.environ`` o de un archivo ``.env``;
- cliente ``httpx`` perezoso y thread-safe con transporte inyectable
  (``httpx.MockTransport`` en los tests, sin tocar la red);
- helpers ``get_json``/``post_json`` (sync) y ``aget_json``/``apost_json``
  (async) con mapeo uniforme de timeouts y errores HTTP a
  :class:`~core_bridge.providers.base.ProviderError`;
- lectura de adjuntos de texto como bloques de contexto y utilidades para
  normalizar mensajes estilo OpenAI a ``(rol, texto)``.

Las subclases concretas solo declaran ``base_url``, ``api_key_env`` y la
traduccion de payload/respuesta (ver ``gemini.py``).
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from ..base import BaseProvider, ProviderError

DEFAULT_TIMEOUT_S = float(os.environ.get("PPLX_TIMEOUT_S", "180"))
DEFAULT_API_KEY_ENV = "PPLX_API_KEY"


def project_root() -> Path:
    """Raiz del repositorio (tres niveles por encima de este archivo)."""
    return Path(__file__).resolve().parents[3]


def default_dotenv_paths() -> tuple[Path, ...]:
    """Archivos ``.env`` candidatos: raiz del proyecto y CWD actual."""
    candidates = [project_root() / ".env", Path.cwd() / ".env"]
    unique: list[Path] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return tuple(unique)


def parse_dotenv(text: str) -> dict[str, str]:
    """Parser minimo de ``.env`` (``KEY=VALUE``, comillas y comentarios)."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip("'\"")
        if key:
            values.setdefault(key, value)
    return values


def read_env_value(
    name: str,
    dotenv_paths: Iterable[Path] | None = None,
) -> str:
    """Valor de *name*: entorno primero, luego ``.env`` (sin sobreescribir)."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    paths = default_dotenv_paths() if dotenv_paths is None else tuple(dotenv_paths)
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        found = parse_dotenv(text).get(name, "").strip()
        if found:
            return found
    return ""


def message_text(content: Any) -> str:
    """Texto plano de un ``content`` OpenAI (``str`` o lista de partes)."""
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, Mapping):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            elif isinstance(part, str) and part.strip():
                parts.append(part.strip())
        return "\n".join(parts).strip()
    return str(content or "").strip()


def iter_messages(messages: Iterable[Any]) -> list[tuple[str, str]]:
    """Normaliza mensajes (dict o pydantic) a pares ``(rol, texto)``."""
    normalized: list[tuple[str, str]] = []
    for msg in messages or []:
        if isinstance(msg, Mapping):
            role = msg.get("role")
            content = msg.get("content")
        else:
            role = getattr(msg, "role", None)
            content = getattr(msg, "content", None)
        role = str(role or "user").strip().lower() or "user"
        text = message_text(content)
        if text:
            normalized.append((role, text))
    return normalized


class BaseApiProvider(BaseProvider):
    """Proveedor respaldado por una API HTTP (httpx)."""

    name = "api"
    kind = "api"
    api_key_env = DEFAULT_API_KEY_ENV
    base_url = ""
    default_timeout_s = DEFAULT_TIMEOUT_S

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        timeout_s: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__()
        if api_key is not None:
            self.api_key = str(api_key).strip()
        else:
            self.api_key = read_env_value(self.api_key_env) if self.api_key_env else ""
        if model:
            self.default_model = str(model).strip()
        self.timeout_s = float(timeout_s) if timeout_s else self.default_timeout_s
        self._transport = transport
        self._client: httpx.Client | None = None
        self._aclient: httpx.AsyncClient | None = None
        self._client_lock = threading.Lock()

    # -- credenciales / estado ---------------------------------------------
    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key)

    def health(self) -> dict[str, Any]:
        """Estado serializable sin lanzar trabajo ni tocar la red."""
        data = super().health()
        data.update(
            {
                "kind": self.kind,
                "status": "ok" if self.has_credentials else "missing_api_key",
                "api_key_set": self.has_credentials,
                "base_url": self.base_url,
            }
        )
        return data

    # -- clientes httpx (perezosos y compartidos) ---------------------------
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = httpx.Client(
                        timeout=self.timeout_s, transport=self._transport
                    )
        return self._client

    @property
    def aclient(self) -> httpx.AsyncClient:
        if self._aclient is None:
            with self._client_lock:
                if self._aclient is None:
                    self._aclient = httpx.AsyncClient(
                        timeout=self.timeout_s, transport=self._transport
                    )
        return self._aclient

    def close(self) -> None:
        """Cierra el cliente sincrono (el async via :meth:`aclose`)."""
        if self._client is not None:
            self._client.close()
            self._client = None

    async def aclose(self) -> None:
        """Cierra el cliente asincrono si llego a crearse."""
        if self._aclient is not None:
            await self._aclient.aclose()
            self._aclient = None

    # -- peticiones JSON ----------------------------------------------------
    def error_hint(self, status_code: int) -> str:
        """Pista legible para un codigo HTTP (las subclases la especializan)."""
        return ""

    def _error_detail(self, response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return (response.text or "").strip()[:300] or "sin detalle"
        if isinstance(data, Mapping):
            error = data.get("error")
            if isinstance(error, Mapping):
                message = error.get("message") or json.dumps(
                    dict(error), ensure_ascii=False
                )
                return str(message)[:300]
            if isinstance(error, str):
                return error[:300]
        return json.dumps(data, ensure_ascii=False)[:300]

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        message = (
            f"HTTP {response.status_code} en {self.name}: "
            f"{self._error_detail(response)}"
        )
        hint = self.error_hint(response.status_code)
        if hint:
            message = f"{message} ({hint})"
        raise ProviderError(message)

    def _decode(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            snippet = (response.text or "").strip()[:200]
            raise ProviderError(
                f"respuesta no JSON de {self.name}: {snippet or 'vacia'}"
            ) from exc
        if not isinstance(data, Mapping):
            raise ProviderError(
                f"respuesta JSON inesperada de {self.name}: {type(data).__name__}"
            )
        return dict(data)

    def _timeout(self, timeout_s: float | None) -> float:
        return float(timeout_s) if timeout_s else self.timeout_s

    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Peticion sync -> JSON, con errores mapeados a ``ProviderError``."""
        timeout = self._timeout(timeout_s)
        try:
            response = self.client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"timeout de {self.name} tras {timeout:.0f}s: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"fallo de red en {self.name}: {exc}") from exc
        self._raise_for_status(response)
        return self._decode(response)

    async def arequest_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Variante async de :meth:`request_json` (mismo mapeo de errores)."""
        timeout = self._timeout(timeout_s)
        try:
            response = await self.aclient.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"timeout de {self.name} tras {timeout:.0f}s: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"fallo de red en {self.name}: {exc}") from exc
        self._raise_for_status(response)
        return self._decode(response)

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        return self.request_json(
            "GET", url, params=params, headers=headers, timeout_s=timeout_s
        )

    def post_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        return self.request_json(
            "POST",
            url,
            params=params,
            json_body=json_body,
            headers=headers,
            timeout_s=timeout_s,
        )

    async def aget_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        return await self.arequest_json(
            "GET", url, params=params, headers=headers, timeout_s=timeout_s
        )

    async def apost_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        return await self.arequest_json(
            "POST",
            url,
            params=params,
            json_body=json_body,
            headers=headers,
            timeout_s=timeout_s,
        )

    # -- adjuntos -----------------------------------------------------------
    def read_attachments(self, attachments: Sequence[str] | None) -> str:
        """Bloques de contexto con el contenido de texto de cada adjunto.

        Lanza :class:`ProviderError` si alguna ruta no existe o no se puede
        leer; un adjunto vacio se representa igualmente con su cabecera.
        """
        blocks: list[str] = []
        for raw in attachments or []:
            path = Path(str(raw)).expanduser()
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise ProviderError(f"adjunto no legible ({raw}): {exc}") from exc
            blocks.append(
                f"===== ADJUNTO: {raw} =====\n"
                f"{content.rstrip()}\n"
                f"===== FIN ADJUNTO ====="
            )
        return "\n\n".join(blocks)


__all__ = [
    "BaseApiProvider",
    "DEFAULT_API_KEY_ENV",
    "DEFAULT_TIMEOUT_S",
    "default_dotenv_paths",
    "iter_messages",
    "message_text",
    "parse_dotenv",
    "project_root",
    "read_env_value",
]
