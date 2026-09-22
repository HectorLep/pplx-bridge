"""Cliente Playwright Chromium (multi-navegador) con Circuit Breaker.

Esta capa de navegacion del paquete ``core_bridge``:

- Resuelve el ejecutable Chromium instalado (Brave > Chrome > Edge y, si no
  hay ninguno, el Chromium empaquetado por Playwright) y mantiene un perfil
  persistente para reutilizar la sesion.
- Envia consultas genericas (``prompt``) con adjuntos opcionales
  (``attachments``) y extrae la respuesta final del streaming.
- Aplica la regla anti-bucles: tras ``PPLX_FAILURE_THRESHOLD`` (3 por
  defecto) fallos consecutivos en selectores, timeouts o errores de pagina,
  aborta reintentos ciegos, vuelca ``error_snapshot.png``, ``error_dom.html``
  y ``error_state.log`` a ``PPLX_ERROR_DIR`` (por defecto ``./pplx_errors``)
  y lanza :class:`CircuitBreakerOpenError` indicando si la causa probable es
  sesion caducada o cambio de DOM.

Modos de operacion (variables de entorno):

1. Contexto persistente propio (por defecto): ``PPLX_USER_DATA_DIR`` o, si
   no se indica, un perfil aislado por navegador
   (``.profile_brave`` / ``.profile_chrome`` / ``.profile_edge``) con el
   navegador fijado por ``PPLX_BROWSER`` / ``PPLX_BROWSER_PATH``. Cada
   navegador usa su propio ``user_data_dir`` para no colisionar en el
   ``SingletonLock`` de Chromium (causa de ``TargetClosedError``).
2. Conexion a un Chrome ya abierto: ``PPLX_CDP_URL``
   (p. ej. ``http://127.0.0.1:9222``).

Sincronizacion (reglas estrictas):
    - NUNCA extraer mientras haya spinners o texto de estado tipo
      "Pensando..." / "Thinking..." / "Buscando...".
    - El streaming termina solo cuando TODAS se cumplen: DOM estable
      ``STABLE_S``, sin boton Stop, sin spinners y boton Copiar o Compartir
      visible.
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("core_bridge.browser")

# ---------------------------------------------------------------------------
# Configuracion (via entorno, con valores por defecto razonables)
# ---------------------------------------------------------------------------

CDP_URL = os.environ.get("PPLX_CDP_URL", "").strip()  # p. ej. http://127.0.0.1:9222
START_URL = os.environ.get("PPLX_START_URL", "https://www.perplexity.ai/")
HEADLESS = os.environ.get("PPLX_HEADLESS", "0").strip().lower() in {
    "1", "true", "yes",
}
NAV_TIMEOUT_MS = int(os.environ.get("PPLX_NAV_TIMEOUT_MS", "60000"))
ASK_TIMEOUT_S = float(os.environ.get("PPLX_TIMEOUT_S", "180"))
POLL_S = float(os.environ.get("PPLX_POLL_S", "1.0"))
STABLE_S = float(os.environ.get("PPLX_STABLE_S", "3.0"))  # DOM quieto => fin
SLOW_MO_MS = int(os.environ.get("PPLX_SLOW_MO_MS", "0"))

# Marcador de login guiado confirmado (lo escribe core_bridge.cli login).
LOGIN_MARKER_NAME = ".pplx_login_ok"

# ---------------------------------------------------------------------------
# Multi-navegador (Brave / Chrome / Edge)
# ---------------------------------------------------------------------------
# El ejecutable se fija con PPLX_BROWSER o PPLX_BROWSER_PATH (nombre
# 'brave'/'chrome'/'edge' o ruta directa al .exe). Sin configuracion se
# autodetecta por prioridad: Brave > Google Chrome > Microsoft Edge.

BRAVE_CANDIDATES = (
    os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    os.path.expandvars(r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    os.path.expandvars(r"%PROGRAMFILES(X86)%\BraveSoftware\Brave-Browser\Application\brave.exe"),
)
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)
EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
BROWSER_CANDIDATES = (
    ("brave", BRAVE_CANDIDATES),
    ("chrome", CHROME_CANDIDATES),
    ("edge", EDGE_CANDIDATES),
)
BROWSER_ALIASES = {
    "brave": "brave",
    "brave-browser": "brave",
    "chrome": "chrome",
    "google-chrome": "chrome",
    "google chrome": "chrome",
    "edge": "edge",
    "msedge": "edge",
    "microsoft-edge": "edge",
}
BROWSER_ENV_VARS = ("PPLX_BROWSER", "PPLX_BROWSER_PATH")

# ---------------------------------------------------------------------------
# Flags de lanzamiento de Chromium (anti-singleton / anti-background)
# ---------------------------------------------------------------------------
# En Windows, Edge/Chrome pueden quedar vivos en segundo plano (background
# mode, msEdgeStartupBoost) reteniendo el SingletonLock del perfil; el
# siguiente lanzamiento se redirige a esa instancia y Playwright pierde el
# contexto recien creado (TargetClosedError). Estos flags desactivan ese
# comportamiento y fuerzan una ventana nueva.
CHROMIUM_LAUNCH_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--no-default-browser-check",
    "--disable-background-mode",
    "--disable-features=msEdgeStartupBoost",
    "--new-window",
)


def chromium_launch_args() -> list[str]:
    """Args de Playwright para Chromium (anti-singleton / anti-background).

    Devuelve una copia nueva de :data:`CHROMIUM_LAUNCH_ARGS` para poder
    anadir los flags de sandbox cuando ``PPLX_NO_SANDBOX=1`` (Docker/root).
    """
    args = list(CHROMIUM_LAUNCH_ARGS)
    if os.environ.get("PPLX_NO_SANDBOX", "0").strip() == "1":
        # Necesario al ejecutar como root (p. ej. dentro de Docker).
        args += ["--no-sandbox", "--disable-dev-shm-usage"]
    return args


def browser_kind(value: str | os.PathLike | None) -> str | None:
    """Devuelve 'brave' | 'chrome' | 'edge' para un nombre o ruta, o None."""
    if value is None:
        return None
    text = str(value).strip().strip('"')
    if not text:
        return None
    for candidate in (text.lower(), Path(text).name.lower()):
        if candidate.endswith(".exe"):
            candidate = candidate[:-4]
        kind = BROWSER_ALIASES.get(candidate)
        if kind:
            return kind
    return None


def browser_candidates(kind: str) -> tuple[str, ...]:
    """Rutas tipicas de instalacion para un navegador conocido."""
    for name, paths in BROWSER_CANDIDATES:
        if name == kind:
            return paths
    return ()


def find_browser_executable(kind: str | None = None) -> str | None:
    """Primer ejecutable instalado, por prioridad Brave > Chrome > Edge."""
    kinds = (kind,) if kind else tuple(name for name, _ in BROWSER_CANDIDATES)
    for name in kinds:
        for path in browser_candidates(name):
            if path and os.path.isfile(path):
                return path
    return None


def resolve_browser_executable(
    value: str | os.PathLike | None = None,
    *,
    required: bool = False,
) -> str | None:
    """Resuelve el ejecutable Chromium a usar.

    Prioridad: *value* > ``PPLX_BROWSER`` > ``PPLX_BROWSER_PATH`` >
    autodeteccion (Brave > Chrome > Edge). Acepta nombres conocidos o una
    ruta directa al ejecutable. Con ``required=True`` lanza
    ``FileNotFoundError`` si no hay navegador disponible.
    """
    requested = value
    if requested is None or not str(requested).strip():
        for env_name in BROWSER_ENV_VARS:
            env_value = os.environ.get(env_name, "").strip()
            if env_value:
                requested = env_value
                break
    if requested is not None and str(requested).strip():
        text = os.path.expandvars(os.path.expanduser(str(requested).strip().strip('"')))
        if os.path.isfile(text):
            return text
        kind = browser_kind(text)
        if kind:
            found = find_browser_executable(kind)
            if found:
                return found
        if required:
            raise FileNotFoundError(
                f"navegador no encontrado: {requested!r}. Usa "
                "PPLX_BROWSER=brave|chrome|edge o PPLX_BROWSER_PATH=<ruta al ejecutable>."
            )
        logger.warning("navegador configurado no encontrado: %s", requested)
        return None
    found = find_browser_executable()
    if found:
        return found
    if required:
        raise FileNotFoundError(
            "no se encontro ningun navegador Chromium (Brave/Chrome/Edge). "
            "Configura PPLX_BROWSER o PPLX_BROWSER_PATH."
        )
    return None


def default_user_data_dir(executable: str | os.PathLike | None = None) -> Path:
    """Perfil persistente aislado por navegador (sin PPLX_USER_DATA_DIR).

    Devuelve ``<base>/PplxProfile/.profile_<kind>`` (p. ej.
    ``.profile_brave`` / ``.profile_chrome`` / ``.profile_edge``) para que
    cada ejecutable Chromium tenga su propio ``SingletonLock``: dos
    navegadores distintos nunca comparten el mismo ``user_data_dir`` y no
    colisionan entre si.
    """
    kind = browser_kind(executable) or "chromium"
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".pplx"
    return root / "PplxProfile" / f".profile_{kind}"


# Ejecutable resuelto al importar (env o autodeteccion). Si es None se usa
# el Chromium empaquetado por Playwright.
BROWSER_EXECUTABLE = resolve_browser_executable()

# Perfil persistente: PPLX_USER_DATA_DIR si se indica; en caso contrario un
# perfil aislado por navegador (default_user_data_dir) para que Brave,
# Chrome y Edge no compartan el SingletonLock de Chromium.
_ENV_USER_DATA_DIR = os.environ.get("PPLX_USER_DATA_DIR", "").strip()
if _ENV_USER_DATA_DIR:
    USER_DATA_DIR = Path(_ENV_USER_DATA_DIR)
else:
    USER_DATA_DIR = default_user_data_dir(BROWSER_EXECUTABLE)

# ---------------------------------------------------------------------------
# Circuit Breaker (regla anti-bucles para automatizacion)
# ---------------------------------------------------------------------------

ERROR_SNAPSHOT_FILE = "error_snapshot.png"
ERROR_DOM_FILE = "error_dom.html"
ERROR_STATE_FILE = "error_state.log"

CAUSE_MESSAGES = {
    "sesion": (
        "la sesion de Perplexity caduco o falta el login "
        "(marcador .pplx_login_ok ausente o muro de login visible)"
    ),
    "arranque": "no se pudo iniciar Playwright/Chromium (dependencias, ejecutable o perfil)",
    "dom": "el DOM de Perplexity cambio y los selectores quedaron obsoletos",
}

CAUSE_HINTS = {
    "sesion": "Renueva la sesion con 'python -m core_bridge.cli login' y reintenta.",
    "arranque": "Revisa 'python -m playwright install chromium' y PPLX_USER_DATA_DIR.",
    "dom": "Revisa error_dom.html y actualiza los selectores en core_bridge/browser.py.",
}

LOGIN_WALL_SELECTORS = (
    'input[type="email"]',
    'input[type="password"]',
    'button:has-text("Log in")',
    'button:has-text("Sign in")',
    'button:has-text("Iniciar sesion")',
    'button:has-text("Iniciar sesión")',
    'a[href*="login"]',
    '[data-testid*="login" i]',
)


def error_artifacts_dir() -> Path:
    """Carpeta de volcado de artefactos (PPLX_ERROR_DIR o ./pplx_errors)."""
    configured = os.environ.get("PPLX_ERROR_DIR", "").strip()
    path = Path(configured).expanduser() if configured else Path("pplx_errors")
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def login_marker_path() -> Path:
    """Ruta del marcador de login confirmado en el perfil persistente."""
    return Path(USER_DATA_DIR) / LOGIN_MARKER_NAME


def login_marker_present() -> bool:
    """True si el login guiado ya fue confirmado para el perfil actual."""
    try:
        return login_marker_path().is_file()
    except OSError:
        return False


def _safe(call, default: str = "-") -> str:
    try:
        return str(call())
    except Exception:  # noqa: BLE001 - diagnostico best-effort
        return default


def detect_login_wall(page) -> bool:
    """True si la pagina muestra un muro de autenticacion de Perplexity."""
    if page is None:
        return False
    url = _safe(lambda: page.url, "") or ""
    low = url.lower()
    if any(token in low for token in ("/login", "signin", "sign-in", "/auth", "accounts.")):
        return True
    for selector in LOGIN_WALL_SELECTORS:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0 and loc.is_visible():
                return True
        except Exception:  # noqa: BLE001 - DOM inestable
            continue
    return False


def diagnose_failure(page, error: BaseException | None = None, stage: str = "") -> str:
    """Clasifica el fallo como 'sesion', 'arranque' o 'dom'."""
    if stage in {"arranque", "inicio"}:
        return "arranque"
    if detect_login_wall(page):
        return "sesion"
    low = str(error or "").lower()
    if any(
        token in low
        for token in ("login", "sign in", "iniciar sesion", "iniciar sesión", "sesion", "sesión", "credencial")
    ):
        return "sesion"
    if not CDP_URL and not login_marker_present():
        return "sesion"
    return "dom"


def _session_state_lines(page, info: dict[str, Any]) -> list[str]:
    """Lineas del log de estado de sesion (sin valores de cookies)."""
    lines = [
        f"timestamp={datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"stage={info.get('stage', '-')}",
        f"failure_count={info.get('failure_count', '-')}",
        f"threshold={info.get('threshold', '-')}",
        f"diagnosis={info.get('diagnosis', '-')}",
        f"last_error={info.get('error', '-')}",
        f"url={_safe(lambda: page.url) if page is not None else '-'}",
        f"title={_safe(lambda: page.title()) if page is not None else '-'}",
        f"cookies={_safe(lambda: len(page.context.cookies()), '-') if page is not None else '-'}",
        f"login_marker={login_marker_path()} (present={login_marker_present()})",
        f"user_data_dir={USER_DATA_DIR}",
        f"browser={BROWSER_EXECUTABLE or 'playwright-chromium'}",
        f"cdp_url={CDP_URL or '-'}",
    ]
    return lines


def dump_failure_artifacts(
    page,
    info: dict[str, Any] | None = None,
    error_dir: Path | str | None = None,
) -> dict[str, str]:
    """Vuelca captura, DOM y estado de sesion. Devuelve rutas escritas."""
    target = Path(error_dir) if error_dir else error_artifacts_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("no se pudo crear %s: %s", target, exc)
        return {}

    artifacts: dict[str, str] = {}
    snapshot = target / ERROR_SNAPSHOT_FILE
    if page is not None:
        try:
            page.screenshot(path=str(snapshot), full_page=True)
            artifacts["snapshot"] = str(snapshot)
        except Exception as exc:  # noqa: BLE001 - mejor sin captura que sin abortar
            logger.warning("no se pudo tomar %s: %s", snapshot, exc)
        dom = target / ERROR_DOM_FILE
        try:
            dom.write_text(page.content() or "", encoding="utf-8")
            artifacts["dom"] = str(dom)
        except Exception as exc:  # noqa: BLE001
            logger.warning("no se pudo volcar %s: %s", dom, exc)

    state = target / ERROR_STATE_FILE
    try:
        state.write_text(
            "\n".join(_session_state_lines(page, info or {})) + "\n",
            encoding="utf-8",
        )
        artifacts["state_log"] = str(state)
    except OSError as exc:
        logger.warning("no se pudo volcar %s: %s", state, exc)
    if artifacts:
        logger.error("artefactos de fallo volcados en %s: %s", target, ", ".join(artifacts))
    return artifacts


class CircuitBreakerOpenError(RuntimeError):
    """3 fallos consecutivos: se abortan reintentos ciegos.

    Lleva adjunto el diagnostico (sesion caducada vs. DOM cambiado), el
    contador de fallos y las rutas de los artefactos volcados.
    """

    def __init__(
        self,
        message: str,
        *,
        failure_count: int = 0,
        diagnosis: str = "desconocida",
        artifacts: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_count = failure_count
        self.diagnosis = diagnosis
        self.artifacts = dict(artifacts or {})


class CircuitBreaker:
    """Cuenta fallos consecutivos y corta la automatizacion al llegar a N.

    - ``record_success()`` reinicia el contador.
    - ``record_failure(...)`` incrementa; al alcanzar ``threshold`` vuelca
      artefactos y lanza :class:`CircuitBreakerOpenError`.
    - ``check()`` aborta nuevas consultas mientras el circuito este abierto
      (tras ``cooldown_s`` permite una sonda *half-open*).
    """

    def __init__(
        self,
        threshold: int | None = None,
        cooldown_s: float | None = None,
        error_dir: Path | str | None = None,
    ) -> None:
        if threshold is None:
            threshold = int(os.environ.get("PPLX_FAILURE_THRESHOLD", "3"))
        if cooldown_s is None:
            cooldown_s = float(os.environ.get("PPLX_BREAKER_COOLDOWN_S", "300"))
        self.threshold = max(1, int(threshold))
        self.cooldown_s = max(0.0, float(cooldown_s))
        self._error_dir = Path(error_dir) if error_dir else None
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._last_error: str | None = None
        self._last_diagnosis: str | None = None
        self._artifacts: dict[str, str] = {}

    # -- estado ------------------------------------------------------------
    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failures

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._opened_at is not None

    def _state_locked(self) -> str:
        if self._opened_at is None:
            return "closed"
        if (time.time() - self._opened_at) >= self.cooldown_s:
            return "half_open"
        return "open"

    def state(self) -> str:
        with self._lock:
            return self._state_locked()

    def artifact_dir(self) -> Path:
        return self._error_dir or error_artifacts_dir()

    def snapshot(self) -> dict[str, Any]:
        """Estado serializable para /health y logs."""
        with self._lock:
            return {
                "state": self._state_locked(),
                "failure_count": self._failures,
                "threshold": self.threshold,
                "cooldown_s": self.cooldown_s,
                "last_error": self._last_error,
                "last_diagnosis": self._last_diagnosis,
                "opened_at": (
                    datetime.fromtimestamp(self._opened_at, timezone.utc).isoformat(
                        timespec="seconds"
                    )
                    if self._opened_at is not None
                    else None
                ),
                "artifacts": dict(self._artifacts),
            }

    # -- transiciones ------------------------------------------------------
    def check(self) -> None:
        """Lanza si el circuito sigue abierto (fail-fast sin tocar el DOM)."""
        with self._lock:
            if self._opened_at is None:
                return
            if (time.time() - self._opened_at) >= self.cooldown_s:
                logger.warning("circuit breaker half-open: se permite una sonda")
                return
            message = self._open_message_locked()
            failure_count = self._failures
            diagnosis = self._last_diagnosis or "desconocida"
            artifacts = dict(self._artifacts)
        raise CircuitBreakerOpenError(
            message,
            failure_count=failure_count,
            diagnosis=diagnosis,
            artifacts=artifacts,
        )

    def record_success(self) -> None:
        """Reinicia el contador tras una consulta exitosa."""
        with self._lock:
            had_failures = self._failures
            self._failures = 0
            self._opened_at = None
            self._last_error = None
            self._last_diagnosis = None
            self._artifacts = {}
        if had_failures:
            logger.info("circuit breaker reiniciado tras consulta exitosa")

    def reset(self) -> None:
        """Reinicio manual (equivalente a ``record_success``)."""
        self.record_success()

    def record_failure(self, error: BaseException, *, stage: str = "", page=None) -> None:
        """Registra un fallo; al llegar al umbral vuelca artefactos y lanza."""
        diagnosis = diagnose_failure(page, error, stage)
        with self._lock:
            self._failures += 1
            self._last_error = f"{stage}: {type(error).__name__}: {error}" if stage else (
                f"{type(error).__name__}: {error}"
            )
            self._last_diagnosis = diagnosis
            count = self._failures
            threshold = self.threshold
            reached = count >= threshold

        if not reached:
            logger.warning(
                "fallo %d/%d en '%s' (%s): %s",
                count, threshold, stage or "-", diagnosis, error,
            )
            return

        artifacts = dump_failure_artifacts(
            page,
            {
                "stage": stage,
                "failure_count": count,
                "threshold": threshold,
                "diagnosis": diagnosis,
                "error": self._last_error,
            },
            error_dir=self._error_dir,
        )
        with self._lock:
            self._opened_at = time.time()
            self._artifacts = dict(artifacts)
            message = self._open_message_locked()
        logger.error("circuit breaker abierto: %s", message)
        raise CircuitBreakerOpenError(
            message,
            failure_count=count,
            diagnosis=diagnosis,
            artifacts=artifacts,
        )

    def _open_message_locked(self) -> str:
        diagnosis = self._last_diagnosis or "desconocida"
        cause = CAUSE_MESSAGES.get(diagnosis, "causa desconocida")
        hint = CAUSE_HINTS.get(diagnosis, "Revisa los artefactos volcados.")
        files = ", ".join(sorted(Path(p).name for p in self._artifacts.values()))
        if not files:
            files = f"{ERROR_SNAPSHOT_FILE}, {ERROR_DOM_FILE}, {ERROR_STATE_FILE}"
        return (
            f"Circuit breaker abierto: {self._failures} fallos consecutivos "
            f"(umbral {self.threshold}). Causa probable: {cause}. "
            f"Se abortan reintentos ciegos. Artefactos en {self.artifact_dir()}: {files}. {hint}"
        )


# ---------------------------------------------------------------------------
# Selectores (listas de respaldo: Perplexity cambia el DOM con frecuencia).
# ---------------------------------------------------------------------------
TEXTAREA_SELECTORS = [
    'textarea[placeholder*="Ask" i]',
    'textarea[placeholder*="Pregunta" i]',
    'textarea[placeholder*="Search" i]',
    "textarea",
    '[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"]',
]
SUBMIT_SELECTORS = [
    'button[aria-label*="Submit" i]',
    'button[aria-label*="Send" i]',
    'button[aria-label*="Enviar" i]',
    'button[type="submit"]',
    "button:has(svg.lucide-arrow-right)",
    "button:has(svg.lucide-arrow-up)",
    "button:has(svg.lucide-arrow-big-up)",
]
# Extraccion: solo .prose / div[dir=auto] (respuesta renderizada).
ANSWER_SELECTORS = [
    '[data-testid="answer"]',
    "article div.prose",
    "main div.prose",
    "div.prose",
    "div[dir='auto']",
    'div[dir="auto"]',
]
STOP_SELECTORS = [
    'button[aria-label*="Stop" i]',
    'button[aria-label*="Detener" i]',
    "button:has(svg.lucide-square)",
]
# Boton de copiar: aparece solo cuando el streaming termino (o hay
# respuesta lista para copiar). Se usa como senal de fin de generacion.
COPY_SELECTORS = [
    'button[aria-label*="Copiar" i]',
    'button[aria-label*="Copy" i]',
    'button[aria-label*="Copier" i]',
    'button[data-testid*="copy" i]',
]
# Boton de compartir: segunda senal de fin de generacion.
SHARE_SELECTORS = [
    'button[aria-label*="Compartir" i]',
    'button[aria-label*="Share" i]',
    'button[aria-label*="Partager" i]',
    'button[data-testid*="share" i]',
]
# Boton de adjuntar: abre un FileChooser nativo. Se maneja de forma
# dinamica con page.expect_file_chooser(...) + click en el boton,
# en vez de buscar pasivamente input[type=file] (que Perplexity no
# expone de forma estable en el DOM).
ATTACH_BUTTON_SELECTOR = (
    "button[aria-label*=Attach i], "
    "button[aria-label*=Adjuntar i], "
    "button:has(svg path[d*=M12])"
)
# Spinners / indicadores de carga: mientras alguno sea visible, NUNCA extraer.
SPINNER_SELECTORS = [
    '[role="status"]',
    '[data-testid*="loading" i]',
    '[data-testid*="spinner" i]',
    '[aria-label*="Cargando" i]',
    '[aria-label*="Loading" i]',
    ".animate-spin",
    "svg.animate-spin",
    ".animate-pulse",
    "[class*='spinner' i]",
    "[class*='loader' i]",
]
# Textos de estado transitorios (pildora "Pensando..." / "Thinking...").
# Se buscan como nodos visibles cortos (no la respuesta completa).
THINKING_TEXTS = [
    "Pensando",
    "Thinking",
    "Buscando",
    "Searching",
    "Generando",
    "Generating",
]


def _first_visible(page, selectors, timeout_ms=15000):
    """Devuelve el primer locator visible de la lista o None."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_exc = None
    while time.monotonic() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return loc
            except Exception as exc:  # noqa: BLE001 - DOM inestable, probar siguiente
                last_exc = exc
                continue
        time.sleep(0.3)
    if last_exc:
        logger.debug("ningun selector visible (%s)", last_exc)
    return None


def _is_generating(page) -> bool:
    """True si hay indicios de respuesta en curso (boton Stop visible)."""
    try:
        for sel in STOP_SELECTORS:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _copy_button_visible(page) -> bool:
    """True si el boton de copiar respuesta ya esta visible (fin de stream)."""
    try:
        for sel in COPY_SELECTORS:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _share_button_visible(page) -> bool:
    """True si el boton de compartir ya esta visible (fin de stream)."""
    try:
        for sel in SHARE_SELECTORS:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _has_spinner(page) -> bool:
    """True si hay algun spinner/indicador de carga visible."""
    try:
        for sel in SPINNER_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return False


def _has_thinking_text(page) -> bool:
    """True si hay una pildora de estado 'Pensando...' visible.

    Solo cuenta nodos cortos (<200 caracteres): evita confundir una
    respuesta final larga que mencione la palabra "pensando" con el
    indicador transitorio de carga.
    """
    try:
        for text in THINKING_TEXTS:
            try:
                nodes = page.get_by_text(text, exact=False).all()
            except Exception:  # noqa: BLE001 - get_by_text no disponible
                try:
                    nodes = page.locator(f"text={text}").all()
                except Exception:  # noqa: BLE001
                    continue
            for node in nodes:
                try:
                    if not node.is_visible():
                        continue
                    txt = (node.inner_text() or "").strip()
                    if txt and len(txt) < 200:
                        return True
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        pass
    return False


def _is_loading(page) -> bool:
    """True si la pagina aun esta generando/cargando.

    Condiciones (cualquiera basta): boton Stop visible, spinner visible,
    o pildora 'Pensando...' visible. Mientras sea True, NUNCA se extrae.
    """
    if _is_generating(page):
        return True
    if _has_spinner(page):
        return True
    if _has_thinking_text(page):
        return True
    return False


def _extract_answer_text(page) -> str:
    """Extrae el texto limpio del ultimo contenedor de respuesta.

    Prioridad: ultimo ``div[dir='auto']`` o ``.prose`` visible con texto,
    que corresponde a la respuesta mas reciente del streaming. Si no hay
    ninguno, se recurre al bloque mas largo y, en ultimo caso, a ``<main>``.

    NOTA: llamar solo cuando ``_is_loading(page)`` sea False. Si hay
    spinners o "Pensando..." visible, el llamante debe esperar y NO extraer.
    """
    candidates: list[str] = []
    for sel in ANSWER_SELECTORS:
        try:
            nodes = page.locator(sel).all()
            for node in nodes:
                try:
                    if not node.is_visible():
                        continue
                    # inner_text conserva saltos de linea razonables.
                    txt = (node.inner_text() or "").strip()
                    if txt:
                        candidates.append(txt)
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
    if candidates:
        # El ultimo contenedor visible suele ser la respuesta final del
        # streaming (los anteriores son mensajes/turnos previos o citas).
        # Se filtra ruido muy corto solo si hay alternativas con contenido.
        long_enough = [t for t in candidates if len(t) >= 20]
        pool = long_enough or candidates
        return pool[-1].strip()
    # Respaldo: todo el contenido principal.
    try:
        main = page.locator("main").first
        if main.count() > 0:
            return (main.inner_text() or "").strip()
        return (page.content() or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _resolve_files(files: list[str | Path] | None) -> list[str]:
    """Valida y resuelve rutas de archivos adjuntos. Lanza ValueError."""
    if not files:
        return []
    resolved: list[str] = []
    for f in files:
        p = Path(f).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        else:
            p = p.resolve()
        if not p.exists():
            raise ValueError(f"archivo no encontrado: {f}")
        if not p.is_file():
            raise ValueError(f"no es un archivo: {f}")
        resolved.append(str(p))
    return resolved


def _attach_files(page, files: list[str], timeout_ms: int = 30000) -> list[str]:
    """Adjunta *files* mediante el FileChooser dinamico de Perplexity.

    Hace click en el boton de adjuntar y captura el FileChooser nativo
    con ``page.expect_file_chooser(...)``, en vez de buscar pasivamente
    ``input[type=file]`` en el DOM. Devuelve la lista de rutas
    inyectadas. Lanza RuntimeError si no se puede adjuntar.
    """
    if not files:
        return []
    try:
        with page.expect_file_chooser(timeout=60000) as fc_info:
            attach_btn = page.locator('button[aria-label*="Attach" i], button[aria-label*="Adjuntar" i], button[aria-label*="Agregar" i], button[aria-label*="Add" i], button:has(svg path[d*="M12"])').first
            attach_btn.click()
            try:
                upload_btn = page.locator('button:has-text("Upload"), button:has-text("Subir"), [role="menuitem"]:has-text("Upload"), [role="menuitem"]:has-text("Subir")').first
                upload_btn.wait_for(state="visible", timeout=2000)
                upload_btn.click(force=True)
            except Exception:
                pass
        file_chooser = fc_info.value
        abs_files = [os.path.abspath(f) for f in files]
        file_chooser.set_files(abs_files)
        logger.info("archivos adjuntados (%d) via FileChooser", len(abs_files))
        # Esperar que aparezcan los elementos de archivo adjunto en la barra de entrada
        page.wait_for_timeout(2500)  # Breve margen de carga inicial
        page.wait_for_timeout(2500)
        page.wait_for_timeout(1000)  # Asegurar montaje completo en el estado de React
        return abs_files
    except Exception as exc:  # noqa: BLE001 - DOM cambiante o sin adjuntos
        raise RuntimeError(
            "no se pudo adjuntar archivos via FileChooser "
            f"(cambio el DOM o la pagina no soporta adjuntos): {exc}"
        ) from exc


class PerplexityBrowser:
    """Navegador persistente con una sola pagina reutilizada (thread-safe).

    Integra un :class:`CircuitBreaker`: cada fallo de selector, timeout o
    pagina se cuenta; al tercero se vuelcan los artefactos y se lanza
    :class:`CircuitBreakerOpenError`. Una consulta exitosa reinicia el
    contador.
    """

    def __init__(self, breaker: CircuitBreaker | None = None) -> None:
        self._lock = threading.Lock()
        self._breaker = breaker or CircuitBreaker()
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    def reset_circuit_breaker(self) -> None:
        """Reinicia manualmente el contador de fallos consecutivos."""
        self._breaker.reset()

    # -- ciclo de vida ----------------------------------------------------
    def start(self):
        if self._page is not None:
            return self
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        if CDP_URL:
            logger.info("conectando por CDP a %s", CDP_URL)
            self._browser = self._pw.chromium.connect_over_cdp(CDP_URL)
            ctx = (
                self._browser.contexts[0]
                if self._browser.contexts
                else self._browser.new_context()
            )
            self._context = ctx
            self._page = ctx.pages[0] if ctx.pages else ctx.new_page()
        else:
            USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(
                "lanzando Chromium persistente (perfil=%s headless=%s)",
                USER_DATA_DIR, HEADLESS,
            )
            launch_args = chromium_launch_args()
            launch_kwargs: dict = {
                "headless": HEADLESS,
                "slow_mo": SLOW_MO_MS or None,
                "args": launch_args,
                "viewport": {"width": 1366, "height": 900},
                "locale": "es-ES",
            }
            if BROWSER_EXECUTABLE:
                launch_kwargs["executable_path"] = BROWSER_EXECUTABLE
                logger.info("ejecutable Chromium resuelto: %s", BROWSER_EXECUTABLE)
            self._context = self._pw.chromium.launch_persistent_context(
                str(USER_DATA_DIR), **launch_kwargs
            )
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else self._context.new_page()
            )
            self._page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        return self

    def close(self) -> None:
        try:
            if self._context is not None and not CDP_URL:
                self._context.close()
            if self._browser is not None and CDP_URL:
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._pw = self._browser = self._context = self._page = None

    def _ensure(self):
        if self._page is None:
            self.start()

    def health(self) -> dict[str, Any]:
        """Estado del navegador y del circuit breaker (no arranca nada)."""
        started = self._page is not None
        breaker = self._breaker.snapshot()
        status = "degraded" if breaker["state"] == "open" else "ok"
        return {
            "status": status,
            "adapter": "perplexity",
            "browser": browser_kind(BROWSER_EXECUTABLE) or "playwright-chromium",
            "executable": BROWSER_EXECUTABLE,
            "user_data_dir": str(USER_DATA_DIR),
            "cdp_url": CDP_URL or None,
            "headless": HEADLESS,
            "start_url": START_URL,
            "login_marker": str(login_marker_path()),
            "logged_in": login_marker_present(),
            "session_started": started,
            "circuit_breaker": breaker,
        }

    # -- consulta ---------------------------------------------------------
    def ask(
        self,
        prompt: str,
        timeout_s: float | None = None,
        files: list[str | Path] | None = None,
        *,
        attachments: list[str | Path] | None = None,
    ) -> str:
        """Envia *prompt* a Perplexity web y devuelve el texto final.

        :param prompt: consulta/instrucciones. Si se adjuntan archivos,
            debe contener SOLO el prompt (los archivos se leen como
            adjuntos nativos).
        :param timeout_s: timeout global (por defecto 180s).
        :param files: alias legacy de *attachments*.
        :param attachments: rutas de archivos a adjuntar via FileChooser
            (``page.expect_file_chooser(...)`` + ``set_files``).
        """
        if not prompt or not prompt.strip():
            raise ValueError("prompt vacio")
        attached = _resolve_files(list(attachments) if attachments else files)
        self._breaker.check()
        timeout = ASK_TIMEOUT_S if timeout_s is None else float(timeout_s)
        deadline = time.monotonic() + timeout

        with self._lock:
            stage = "arranque"
            page = None
            try:
                self._ensure()
                page = self._page

                # 1) Nueva busqueda: pagina limpia con el textarea vacio.
                stage = "navegacion"
                page.goto(START_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)

                # 2) Adjuntar archivos ANTES de escribir el prompt, para que
                # Perplexity los procese como adjuntos nativos. El prompt queda
                # entonces con SOLO las instrucciones.
                if attached:
                    stage = "adjuntos"
                    _attach_files(page, attached)
                    logger.info(
                        "modo adjuntos: %d archivo(s), prompt solo instrucciones (%d chars)",
                        len(attached), len(prompt),
                    )
                    page.wait_for_timeout(1000)

                # 3) Foco en el area de texto (tras subir archivos el foco se
                # pierde: hay que devolverlo antes de inyectar el prompt).
                stage = "selector"
                box = _first_visible(page, TEXTAREA_SELECTORS, timeout_ms=30000)
                if box is None:
                    raise RuntimeError(
                        "no se encontro el cuadro de texto de Perplexity "
                        "(cambio el DOM o hace falta login)"
                    )
                # Foco en el area de texto
                input_box = page.locator('textarea, div[contenteditable=true]').first
                input_box.click()
                try:
                    input_box.fill(prompt)
                except Exception:  # noqa: BLE001 - contenteditable u otro widget
                    try:
                        box.fill(prompt)
                    except Exception:  # noqa: BLE001
                        page.keyboard.press("ControlOrMeta+a")
                        page.keyboard.type(prompt, delay=5)
                page.wait_for_timeout(500)

                # 4) Intentar clic en el boton de enviar habilitado
                # (flecha derecha/submit); si no esta visible/habilitado,
                # presionar Enter como respaldo.
                stage = "envio"
                send_btn = page.locator('button:has(svg path[d*=M13]), button[aria-label*=Enviar i], button[aria-label*=Submit i]').last
                try:
                    if send_btn.is_visible() and send_btn.is_enabled():
                        send_btn.click()
                    else:
                        page.keyboard.press("Enter")
                except Exception:  # noqa: BLE001 - boton no clicable, usar Enter
                    page.keyboard.press("Enter")
                logger.debug("prompt enviado, esperando respuesta...")

                # 5) Esperar a que aparezca la respuesta / arranque el streaming.
                # NUNCA se extrae mientras haya spinners o "Pensando...": solo
                # se considera arranque si hay contenido Y no hay loading.
                stage = "espera_respuesta"
                answer_seen = False
                while time.monotonic() < deadline:
                    if _is_loading(page):
                        time.sleep(POLL_S)
                        continue
                    if _copy_button_visible(page) or _share_button_visible(page):
                        answer_seen = True
                        break
                    if _extract_answer_text(page):
                        answer_seen = True
                        break
                    # La URL de busqueda tambien indica que arranco la generacion.
                    if "/search/" in (page.url or ""):
                        answer_seen = True
                        break
                    time.sleep(POLL_S)
                if not answer_seen:
                    raise TimeoutError(
                        "Perplexity no mostro respuesta a tiempo "
                        "(posible login requerido o selector obsoleto)"
                    )

                # 6) Espera activa del fin del streaming. Condiciones TODAS:
                #   a) sin spinners ni "Pensando..." (_is_loading False),
                #   b) sin boton Stop (sin generacion en curso),
                #   c) texto de .prose/div[dir=auto] estable durante STABLE_S
                #      (por defecto 3s),
                #   d) boton Copiar o Compartir visible (respuesta lista).
                # Mientras haya loading, NUNCA se extrae ni se actualiza
                # la marca de estabilidad.
                stage = "espera_estable"
                last_text = ""
                stable_since = time.monotonic()
                final_text = ""
                while time.monotonic() < deadline:
                    if _is_loading(page):
                        # Reiniciar la ventana de estabilidad: el DOM cambio.
                        stable_since = time.monotonic()
                        time.sleep(POLL_S)
                        continue
                    copy_visible = _copy_button_visible(page)
                    share_visible = _share_button_visible(page)
                    buttons_ready = copy_visible or share_visible
                    text = _extract_answer_text(page)
                    if text and text != last_text:
                        last_text = text
                        stable_since = time.monotonic()
                    stable_for = time.monotonic() - stable_since
                    if text and buttons_ready and stable_for >= STABLE_S:
                        # Fin de stream: DOM quieto STABLE_S + Copiar/Compartir.
                        final_text = text
                        logger.debug(
                            "fin de stream: estable %.1fs copy=%s share=%s chars=%d",
                            stable_for, copy_visible, share_visible, len(text),
                        )
                        break
                    time.sleep(POLL_S)
                else:
                    raise TimeoutError(
                        f"la respuesta no se estabilizo en {timeout:.0f}s"
                    )

                stage = "extraccion"
                final_text = (final_text or last_text).strip()
                if not final_text:
                    raise RuntimeError("respuesta vacia tras el renderizado")
                # Guardia final: no devolver si reaparecio un spinner.
                if _is_loading(page):
                    raise RuntimeError(
                        "respuesta descartada: reaparecio spinner/Pensando... "
                        "tras la extraccion"
                    )
            except CircuitBreakerOpenError:
                raise
            except Exception as exc:  # noqa: BLE001 - clasificar y contar el fallo
                self._breaker.record_failure(exc, stage=stage, page=page)
                raise

            self._breaker.record_success()
            logger.info("respuesta extraida (%d caracteres)", len(final_text))
            return final_text


_singleton: PerplexityBrowser | None = None
_singleton_lock = threading.Lock()


def get_browser() -> PerplexityBrowser:
    """Devuelve el navegador singleton (lo crea y arranca si hace falta)."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = PerplexityBrowser().start()
            atexit.register(_singleton.close)
        return _singleton


def browser_health() -> dict[str, Any]:
    """Health del navegador sin arrancarlo (usa el singleton si existe)."""
    browser = _singleton if _singleton is not None else PerplexityBrowser()
    return browser.health()


def ask_perplexity(
    prompt: str,
    timeout_s: float | None = None,
    files: list[str | Path] | None = None,
    *,
    attachments: list[str | Path] | None = None,
) -> str:
    """Atajo thread-safe: pregunta a Perplexity web y devuelve el texto.

    :param prompt: SOLO las instrucciones cuando *attachments* no es vacio.
    :param attachments: archivos a adjuntar via FileChooser (``set_files``).
    """
    return get_browser().ask(
        prompt, timeout_s=timeout_s, files=files, attachments=attachments
    )


async def aask_perplexity(
    prompt: str,
    timeout_s: float | None = None,
    files: list[str | Path] | None = None,
    *,
    attachments: list[str | Path] | None = None,
) -> str:
    """Variante async para usar desde FastAPI (delega a un hilo)."""
    import anyio

    return await anyio.to_thread.run_sync(
        lambda: ask_perplexity(
            prompt, timeout_s=timeout_s, files=files, attachments=attachments
        )
    )


__all__ = [
    "ASK_TIMEOUT_S",
    "BROWSER_EXECUTABLE",
    "CDP_URL",
    "CHROMIUM_LAUNCH_ARGS",
    "HEADLESS",
    "NAV_TIMEOUT_MS",
    "POLL_S",
    "SLOW_MO_MS",
    "STABLE_S",
    "START_URL",
    "USER_DATA_DIR",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "PerplexityBrowser",
    "aask_perplexity",
    "ask_perplexity",
    "browser_candidates",
    "browser_health",
    "browser_kind",
    "chromium_launch_args",
    "default_user_data_dir",
    "detect_login_wall",
    "diagnose_failure",
    "dump_failure_artifacts",
    "error_artifacts_dir",
    "find_browser_executable",
    "get_browser",
    "login_marker_path",
    "login_marker_present",
    "resolve_browser_executable",
]
