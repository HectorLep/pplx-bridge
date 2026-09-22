"""Shim de compatibilidad: la implementacion vive en ``core_bridge.browser``.

Mantiene la ruta historica ``tools/pplx_bridge/browser_client.py`` (la que
importan ``tools/run_pplx_audit.py`` y ``tools/pplx_login.py``) sin
duplicar la logica de Playwright ni el Circuit Breaker.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core_bridge.browser import (  # noqa: E402,F401
    ANSWER_SELECTORS,
    ASK_TIMEOUT_S,
    ATTACH_BUTTON_SELECTOR,
    BROWSER_EXECUTABLE,
    CDP_URL,
    CHROMIUM_LAUNCH_ARGS,
    COPY_SELECTORS,
    ERROR_DOM_FILE,
    ERROR_SNAPSHOT_FILE,
    ERROR_STATE_FILE,
    HEADLESS,
    LOGIN_MARKER_NAME,
    NAV_TIMEOUT_MS,
    POLL_S,
    SHARE_SELECTORS,
    SLOW_MO_MS,
    SPINNER_SELECTORS,
    STABLE_S,
    START_URL,
    STOP_SELECTORS,
    SUBMIT_SELECTORS,
    TEXTAREA_SELECTORS,
    THINKING_TEXTS,
    USER_DATA_DIR,
    CircuitBreaker,
    CircuitBreakerOpenError,
    PerplexityBrowser,
    aask_perplexity,
    ask_perplexity,
    browser_candidates,
    browser_health,
    browser_kind,
    chromium_launch_args,
    default_user_data_dir,
    detect_login_wall,
    diagnose_failure,
    dump_failure_artifacts,
    error_artifacts_dir,
    find_browser_executable,
    get_browser,
    login_marker_path,
    login_marker_present,
    resolve_browser_executable,
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
    "browser_health",
    "browser_kind",
    "default_user_data_dir",
    "find_browser_executable",
    "get_browser",
    "resolve_browser_executable",
]
