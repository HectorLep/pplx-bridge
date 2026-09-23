"""CLI unificado del puente.

Subcomandos::

    python -m core_bridge.cli login [--browser BROWSER] [--user-data-dir DIR]
        Abre el navegador Chromium seleccionado (Brave > Chrome > Edge por
        autodeteccion) con el perfil persistente para un login guiado unico.
        Si no se indica --user-data-dir ni PPLX_USER_DATA_DIR se usa un
        perfil aislado por navegador (``.profile_brave`` / ``.profile_chrome``
        / ``.profile_edge``) para evitar colisiones de SingletonLock entre
        navegadores. Al confirmar guarda el marcador ``.pplx_login_ok`` dentro
        del perfil.

    python -m core_bridge.cli start [--host HOST] [--port PORT]
        Arranca el servidor FastAPI del puente (por defecto 127.0.0.1:8000,
        configurable tambien con PPLX_HOST / PPLX_PORT).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

LOGIN_TIMEOUT_MS = 300_000

# Flags anti-singleton y anti-background de Chromium en Windows: evitan que
# Edge/Chrome quede vivo en segundo plano reteniendo el SingletonLock del
# perfil, lo que hace que Playwright pierda el contexto recien lanzado
# (TargetClosedError). Deben coincidir con
# ``core_bridge.browser.CHROMIUM_LAUNCH_ARGS``.
CHROMIUM_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-default-browser-check",
    "--disable-background-mode",
    "--disable-features=msEdgeStartupBoost",
    "--new-window",
]


def _close_quietly(target) -> None:
    """Cierra un contexto de Playwright ignorando errores de cierre."""
    try:
        if target is not None:
            target.close()
    except Exception:  # noqa: BLE001 - cierre best-effort
        pass


def _prompt(text: str) -> str:
    """Muestra un prompt en la terminal y lee una linea.

    ``input(prompt)`` puede retener el texto en el buffer de stdout (sobre
    todo con la salida redirigida o capturada por el proceso padre), dejando
    la consola aparentemente colgada. Se escribe con ``flush=True`` para
    garantizar que el prompt sea visible antes de bloquear la lectura.
    """
    print(text, end="", flush=True)
    return input()


def _stop_playwright(pw) -> None:
    """Detiene el driver de Playwright sin propagar errores de tuberia.

    Si el usuario cancela con Ctrl+C, el subproceso Node del driver recibe
    tambien la senal y puede cerrar sus tuberias antes de que Python lo
    detenga; las escrituras a esa tuberia rota (EPIPE) se ignoran para no
    ensuciar la consola con un error no capturado.
    """
    if pw is None:
        return
    try:
        pw.stop()
    except Exception:  # noqa: BLE001 - cierre best-effort
        pass


def _cmd_login(args: argparse.Namespace) -> int:
    from .browser import (
        LOGIN_MARKER_NAME,
        START_URL,
        browser_kind,
        default_user_data_dir,
        resolve_browser_executable,
    )

    # El ejecutable se resuelve ANTES de elegir el perfil: el user_data_dir
    # por defecto debe ser el del navegador seleccionado en este comando
    # (--browser / PPLX_BROWSER / PPLX_BROWSER_PATH), no el del navegador
    # autodetectado al importar. Asi Edge no reutiliza el perfil de Chrome/
    # Brave (ni al reves) y no se colisiona el SingletonLock de Chromium.
    executable = resolve_browser_executable(args.browser)
    explicit_dir = (
        args.user_data_dir
        or os.environ.get("PPLX_USER_DATA_DIR", "").strip()
    )
    if explicit_dir:
        user_data_dir = Path(explicit_dir).expanduser().resolve()
    else:
        user_data_dir = default_user_data_dir(executable)
    start_url = args.start_url or START_URL
    marker = user_data_dir / LOGIN_MARKER_NAME

    if marker.is_file() and not args.force:
        print(f"[core_bridge] Sesion ya inicializada ({marker}). Nada que hacer.")
        return 0

    user_data_dir.mkdir(parents=True, exist_ok=True)
    if executable:
        print(
            f"[core_bridge] Navegador: {browser_kind(executable) or 'chromium'} "
            f"-> {executable}"
        )
    else:
        print("[core_bridge] Navegador: Chromium empaquetado por Playwright.")
    print(f"[core_bridge] Perfil persistente: {user_data_dir}")
    print("[core_bridge] Se abrira una ventana de Chromium. Inicia sesion en Perplexity.")
    print("[core_bridge] Cuando termines, vuelve a esta consola y pulsa ENTER.")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "[core_bridge] ERROR: falta Playwright. Instala con:\n"
            "  pip install -r requirements.txt\n"
            "  python -m playwright install chromium",
            file=sys.stderr,
        )
        return 1

    launch_kwargs: dict = {
        "headless": False,
        "args": list(CHROMIUM_LAUNCH_ARGS),
        "viewport": {"width": 1366, "height": 900},
        "locale": "es-ES",
    }
    if executable:
        launch_kwargs["executable_path"] = executable

    pw = None
    context = None
    try:
        pw = sync_playwright().start()
        context = pw.chromium.launch_persistent_context(
            str(user_data_dir), **launch_kwargs
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_navigation_timeout(LOGIN_TIMEOUT_MS)
        try:
            page.goto(start_url)
        except Exception as exc:  # noqa: BLE001
            print(f"[core_bridge] AVISO: no se pudo cargar {start_url}: {exc}")
        try:
            _prompt("[core_bridge] Pulsa ENTER cuando hayas iniciado sesion... ")
            answer = _prompt(
                "[core_bridge] ¿Sesion iniciada correctamente? [S/n]: "
            ).strip().lower()
        except EOFError:
            print(
                "[core_bridge] ERROR: consola no interactiva; ejecuta este "
                "comando manualmente en una terminal.",
                file=sys.stderr,
            )
            return 1
        except KeyboardInterrupt:
            print(
                "\n[core_bridge] Cancelado por el usuario.",
                file=sys.stderr,
            )
            return 1
    except KeyboardInterrupt:
        # Ctrl+C durante el arranque/navegacion: se sale limpio sin traceback.
        print("\n[core_bridge] Cancelado por el usuario.", file=sys.stderr)
        return 1
    finally:
        # Cerrar contexto y SIEMPRE detener el driver de Playwright antes de
        # salir de la funcion: si Python termina mientras el driver Node sigue
        # vivo, este escribe en la tuberia cerrada y emite el error no
        # capturado 'EPIPE: broken pipe, write'.
        _close_quietly(context)
        _stop_playwright(pw)

    # ENTER (cadena vacia) confirma: la confirmacion es [S/n], no [s/N].
    if answer in {"", "s", "si", "sí", "y", "yes"}:
        marker.write_text("ok\n", encoding="utf-8")
        print(f"[core_bridge] Sesion guardada. Marcador: {marker}")
        print(
            "[core_bridge] Ya puedes arrancar el puente: "
            "python -m core_bridge.cli start"
        )
        return 0

    print(
        "[core_bridge] Login no confirmado; repite el proceso cuando quieras.",
        file=sys.stderr,
    )
    return 1


def _cmd_start(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "[core_bridge] ERROR: falta uvicorn. Instala con: "
            "pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    from .server import app

    host = args.host or os.environ.get("PPLX_HOST", "127.0.0.1")
    port = args.port if args.port is not None else int(os.environ.get("PPLX_PORT", "8000"))
    print(f"[core_bridge] Sirviendo en http://{host}:{port} (health: /health)")
    uvicorn.run(app, host=host, port=port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m core_bridge.cli",
        description="Puente web local: login guiado y servidor FastAPI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser(
        "login", help="login guiado unico en el perfil persistente"
    )
    login.add_argument(
        "--browser",
        default=None,
        help=(
            "Navegador a usar: 'brave' | 'chrome' | 'edge' o ruta directa al "
            ".exe. Por defecto PPLX_BROWSER / PPLX_BROWSER_PATH o autodeteccion "
            "Brave > Chrome > Edge."
        ),
    )
    login.add_argument(
        "--user-data-dir",
        default=None,
        help=(
            "Perfil persistente. Por defecto PPLX_USER_DATA_DIR o un perfil "
            "aislado por navegador (.profile_brave/.profile_chrome/"
            ".profile_edge) para evitar colisiones de lock entre navegadores."
        ),
    )
    login.add_argument(
        "--start-url",
        default=None,
        help="URL inicial (por defecto la de PPLX_START_URL).",
    )
    login.add_argument(
        "--force",
        action="store_true",
        help="Repite el login aunque exista el marcador .pplx_login_ok.",
    )
    login.set_defaults(func=_cmd_login)

    start = sub.add_parser("start", help="arranca el servidor FastAPI del puente")
    start.add_argument("--host", default=None, help="Host (por defecto PPLX_HOST o 127.0.0.1).")
    start.add_argument(
        "--port",
        type=int,
        default=None,
        help="Puerto (por defecto PPLX_PORT o 8000).",
    )
    start.set_defaults(func=_cmd_start)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        # Ultimo recurso (p. ej. Ctrl+C fuera del flujo de login): salida
        # limpia y codigo convencional 130 en vez de traceback.
        print("\n[core_bridge] Cancelado por el usuario.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
