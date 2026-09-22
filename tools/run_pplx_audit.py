"""Runner de auditoria Perplexity: asegura Chrome CDP + bridge y llama a /v1/evaluate.

Flujo:
  1. Si el puerto 9222 (CDP) no esta activo, arranca el navegador Chromium
     resuelto (Brave/Chrome/Edge, ver mas abajo) en 2do plano.
  2. Si el puerto 8000 (FastAPI Bridge) no esta activo, arranca
     tools/pplx_bridge/server.py en 2do plano con PPLX_CDP_URL fijado.
  3. Canonaliza rutas de archivos a absolutas.
  4. POST a http://127.0.0.1:8000/v1/evaluate con timeout de 300 s.
  5. Guarda la respuesta en --out (por defecto audit_result.txt) e imprime resumen.
  6. Registra hallazgos en PROJECT_MEMORY.md (SECURITY, REGRESSIONS,
     ARCHITECTURE, OBSERVATIONS), actualiza BEST_SCORE en .best_known_state.json
     y alerta si el puntaje cae respecto al mejor conocido.

Navegador (multi-navegador):
  Se puede fijar con --browser, o con las variables de entorno PPLX_BROWSER /
  PPLX_BROWSER_PATH, indicando un nombre ('brave', 'chrome', 'edge') o una
  ruta directa al ejecutable. Sin especificarlo se autodetecta por prioridad:
  Brave > Google Chrome > Microsoft Edge.

Uso:
    python tools/run_pplx_audit.py --files src/a.py src/b.py --prompt "Rubrica..." --out audit_result.txt
    python tools/run_pplx_audit.py --browser brave --files src/a.py --prompt "Rubrica..."
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

try:
    from tools.pplx_bridge.browser_client import (
        default_user_data_dir,
        resolve_browser_executable,
    )
except ImportError:  # pragma: no cover - ejecucion directa dentro de tools/
    from pplx_bridge.browser_client import (  # type: ignore
        default_user_data_dir,
        resolve_browser_executable,
    )

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
SERVER_PY = HERE / "pplx_bridge" / "server.py"

CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
EVALUATE_URL = "http://127.0.0.1:8000/v1/evaluate"
REQUEST_TIMEOUT_S = 300

DEFAULT_MEMORY_FILE = "PROJECT_MEMORY.md"
DEFAULT_STATE_FILE = ".best_known_state.json"
MEMORY_SECTIONS = ("SECURITY", "REGRESSIONS", "ARCHITECTURE", "OBSERVATIONS")
MEMORY_HEADER = """# PROJECT MEMORY

Memoria acumulada de auditorias Perplexity (generado por tools/run_pplx_audit.py).
Cada corrida registra hallazgos por categoria y actualiza BEST_SCORE.
"""

_FINDING_PREFIX_RE = re.compile(r"^\s*(?:[-*+\u2022]|\d+[.)])\s+")
_MARKDOWN_HEADER_RE = re.compile(r"^\s*#{1,6}\s+")
_META_LINE_RE = re.compile(
    r"\b(PUNTAJE|SCORE|VEREDICTO|VERDICT)\b", re.IGNORECASE
)

_SCORE_PATTERNS = (
    re.compile(
        r"\b(?:PUNTAJE|SCORE|NOTA|CALIFICACION|CALIFICACI\u00d3N)\b\s*[:=]?\s*"
        r"\[?\s*(\d{1,3})\s*\]?\s*(?:/\s*(\d{1,3}))?",
        re.IGNORECASE,
    ),
    re.compile(r"\b(\d{1,3})\s*/\s*100\b"),
    re.compile(r"\b(\d{1,3})\s*(?:de|sobre)\s*100\b", re.IGNORECASE),
)

_CATEGORY_KEYWORDS = (
    (
        "SECURITY",
        (
            "security", "seguridad", "vulnerab", "exploit", "cve-", "injection",
            "inyecc", "xss", "csrf", "ssrf", "rce", "secret", "credencial",
            "credential", "password", "contrasen", "sanitiz", "auth", "permiso",
            "privileg", "sandbox", "deserializ", "sql", "path traversal",
            "cifrad", "encrypt", "tls", "hardening", "malware",
        ),
    ),
    (
        "REGRESSIONS",
        (
            "regres", "regression", "roto", "rota", "broken", "falla", "fallo",
            "fail", "bug", "error", "crash", "excepcion", "exception",
            "no funciona", "cayo", "rompe", "timeout", "flaky",
        ),
    ),
    (
        "ARCHITECTURE",
        (
            "arquitect", "architect", "diseno", "dise\u00f1o", "design", "acoplam",
            "coupling", "deuda tecnica", "deuda t\u00e9cnica", "debt", "refactor",
            "escalab", "scalab", "mantenib", "mantenibilidad", "maintainab",
            "complejidad", "complexity", "patron", "patr\u00f3n", "pattern",
            "modular", "capas", "layers", "estructura", "cohesi",
        ),
    ),
)

def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_port_open(host, port, timeout=1.0):
            return True
        time.sleep(1.0)
    return is_port_open(host, port, timeout=2.0)


def ensure_browser(browser: str | None = None) -> str | None:
    """Garantiza CDP en 9222 lanzando el navegador Chromium resuelto.

    :param browser: nombre ('brave'/'chrome'/'edge') o ruta al ejecutable;
        si es None se usan PPLX_BROWSER / PPLX_BROWSER_PATH y, en ultimo
        termino, la autodeteccion Brave > Chrome > Edge.
    :return: ruta del ejecutable lanzado, o None si el CDP ya estaba activo.
    """
    if is_port_open("127.0.0.1", CDP_PORT):
        print(f"[pplx-audit] CDP ya activo en puerto {CDP_PORT}.")
        return None
    try:
        executable = resolve_browser_executable(browser, required=True)
    except FileNotFoundError as exc:
        print(f"[pplx-audit] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    user_data_dir = os.environ.get("PPLX_USER_DATA_DIR", "").strip()
    if not user_data_dir:
        user_data_dir = str(default_user_data_dir(executable))
    cmd = [
        executable,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={user_data_dir}",
    ]
    print(f"[pplx-audit] Arrancando navegador: {' '.join(cmd)}")
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except FileNotFoundError:
        print(
            f"[pplx-audit] ERROR: no se encontro el ejecutable {executable}",
            file=sys.stderr,
        )
        sys.exit(1)
    if not wait_for_port("127.0.0.1", CDP_PORT, timeout=30.0):
        print(
            f"[pplx-audit] AVISO: el navegador no abrio el puerto {CDP_PORT} a tiempo.",
            file=sys.stderr,
        )
    else:
        print(f"[pplx-audit] CDP listo en puerto {CDP_PORT}.")
    return executable


def ensure_chrome() -> None:
    """Alias retrocompatible de :func:`ensure_browser`."""
    ensure_browser()


def ensure_bridge() -> None:
    if is_port_open("127.0.0.1", 8000):
        print("[pplx-audit] Bridge ya activo en puerto 8000.")
        return
    if not SERVER_PY.exists():
        print(
            f"[pplx-audit] ERROR: no se encontro el servidor {SERVER_PY}",
            file=sys.stderr,
        )
        sys.exit(1)
    env = os.environ.copy()
    env["PPLX_CDP_URL"] = CDP_URL
    cmd = [sys.executable, str(SERVER_PY)]
    print(f"[pplx-audit] Arrancando bridge: {' '.join(cmd)} (PPLX_CDP_URL={CDP_URL})")
    try:
        subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        print(f"[pplx-audit] ERROR al arrancar el bridge: {exc}", file=sys.stderr)
        sys.exit(1)
    if not wait_for_port("127.0.0.1", 8000, timeout=45.0):
        print(
            "[pplx-audit] AVISO: el bridge no abrio el puerto 8000 a tiempo.",
            file=sys.stderr,
        )
    else:
        print("[pplx-audit] Bridge listo en puerto 8000.")


def canonicalize_files(files: list[str]) -> list[str]:
    canonical: list[str] = []
    for f in files:
        p = Path(f).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        else:
            p = p.resolve()
        canonical.append(str(p))
    return canonical


def post_evaluate(prompt: str, files: list[str]) -> dict:
    payload = {"prompt": prompt, "files": files}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        EVALUATE_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(
            f"[pplx-audit] ERROR HTTP {exc.code}: {detail}",
            file=sys.stderr,
        )
        sys.exit(1)
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        print(f"[pplx-audit] ERROR de conexion/timeout: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


# ---------------------------------------------------------------------------
# Memoria de proyecto (PROJECT_MEMORY.md) y Best-Known-State
# ---------------------------------------------------------------------------

def resolve_project_path(name: str) -> Path:
    p = Path(name).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


def format_score(score: float | None) -> str:
    if score is None:
        return "-"
    value = float(score)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def extract_score(text: str) -> float | None:
    """Extrae el puntaje de la respuesta y lo normaliza a escala 0-100."""
    for pattern in _SCORE_PATTERNS:
        for match in pattern.finditer(text):
            try:
                value = float(match.group(1))
                base = None
                if pattern.groups >= 2 and match.group(2):
                    base = float(match.group(2))
            except (TypeError, ValueError):
                continue
            if base is not None:
                if base <= 0:
                    continue
                normalized = value * 100.0 / base
            else:
                normalized = value
            if 0.0 <= normalized <= 100.0:
                return round(normalized, 2)
    return None


def classify_finding(line: str) -> str:
    low = line.lower()
    for section, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in low for keyword in keywords):
            return section
    return "OBSERVATIONS"


def extract_findings(text: str, limit: int = 40) -> dict[str, list[str]]:
    """Clasifica hallazgos (vulnerabilidades, bugs, deuda) por seccion."""
    findings: dict[str, list[str]] = {section: [] for section in MEMORY_SECTIONS}
    seen: set[str] = set()
    total = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _MARKDOWN_HEADER_RE.match(line):
            continue
        if _META_LINE_RE.search(line):
            continue
        line = _FINDING_PREFIX_RE.sub("", line).strip()
        if len(line) < 8:
            continue
        low = line.lower()
        if low.startswith(("puntaje", "veredicto", "score", "verdict")):
            continue
        key = re.sub(r"\s+", " ", low)[:120]
        if key in seen:
            continue
        seen.add(key)
        section = classify_finding(line)
        findings[section].append(line[:400])
        total += 1
        if total >= limit:
            break
    return findings


def load_memory_text(path: Path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8")
    lines = [MEMORY_HEADER.rstrip(), ""]
    lines += ["## BEST_KNOWN_STATE", "", "BEST_SCORE: -", ""]
    for section in MEMORY_SECTIONS:
        lines += [f"## {section}", "", "(sin entradas)", ""]
    return "\n".join(lines).rstrip() + "\n"


def _section_bounds(text: str, section: str) -> tuple[int, int] | None:
    header = re.compile(rf"^##\s+{re.escape(section)}\s*$", re.IGNORECASE | re.MULTILINE)
    match = header.search(text)
    if not match:
        return None
    nxt = re.compile(r"^##\s+", re.MULTILINE).search(text, match.end())
    return match.end(), (nxt.start() if nxt else len(text))


def update_memory_best_score(memory_text: str, score: float | None, when: str) -> str:
    line = f"BEST_SCORE: {format_score(score)}  (actualizado {when})"
    bounds = _section_bounds(memory_text, "BEST_KNOWN_STATE")
    if bounds is None:
        return memory_text.rstrip() + f"\n\n## BEST_KNOWN_STATE\n\n{line}\n"
    start, end = bounds
    body = memory_text[start:end]
    if re.search(r"^BEST_SCORE:", body, re.MULTILINE):
        body = re.sub(r"^BEST_SCORE:.*$", line, body, count=1, flags=re.MULTILINE)
    else:
        body = "\n" + line + "\n" + body.lstrip("\n")
    return memory_text[:start] + body + memory_text[end:]


def append_findings(
    memory_text: str, findings: dict[str, list[str]], day: str
) -> tuple[str, int]:
    added = 0
    for section in MEMORY_SECTIONS:
        entries = findings.get(section) or []
        if not entries:
            continue
        bounds = _section_bounds(memory_text, section)
        existing = (
            memory_text[bounds[0]:bounds[1]].lower() if bounds else ""
        )
        fresh: list[str] = []
        for entry in entries:
            normalized = re.sub(r"\s+", " ", entry.strip().lower())
            if normalized in existing:
                continue
            fresh.append(f"- [{day}] {entry}")
        if not fresh:
            continue
        block = "\n".join(fresh)
        if bounds is None:
            memory_text = memory_text.rstrip() + f"\n\n## {section}\n\n{block}\n"
        else:
            start, end = bounds
            body = re.sub(
                r"^\s*\(sin entradas\)\s*$", "", memory_text[start:end], flags=re.MULTILINE
            ).strip("\n")
            body = f"{body}\n{block}" if body else block
            memory_text = memory_text[:start] + f"\n{body}\n\n" + memory_text[end:]
        added += len(fresh)
    return memory_text, added


def load_state(path: Path) -> dict:
    if not path.is_file():
        return {"best_score": None, "updated_at": None, "history": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("best_score", None)
            data.setdefault("updated_at", None)
            data.setdefault("history", [])
            return data
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[pplx-audit] AVISO: estado ilegible ({exc}); se reinicia.", file=sys.stderr)
    return {"best_score": None, "updated_at": None, "history": []}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def process_memory_and_state(
    answer_text: str,
    result: dict | None,
    out_path: Path,
    memory_path: Path | None,
    state_path: Path | None,
    files_count: int,
) -> tuple[float | None, bool]:
    """Registra hallazgos/BEST_SCORE y emite alerta explicita de regresion."""
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    timestamp = now.isoformat(timespec="seconds")
    score = extract_score(answer_text)
    findings = extract_findings(answer_text)

    state = load_state(state_path) if state_path is not None else {}
    previous = state.get("best_score")
    previous = float(previous) if isinstance(previous, (int, float)) else None

    regression = False
    if score is not None and previous is not None and score < previous:
        regression = True
        delta = round(score - previous, 2)
        alert = (
            f"ALERTA DE REGRESION: Puntaje cayo de {format_score(previous)} "
            f"a {format_score(score)} (delta: {delta:+g})"
        )
        print(f"[pplx-audit] {alert}")
        eval_id = (result or {}).get("id", "-")
        findings["REGRESSIONS"].insert(0, f"{alert} [run {eval_id}]")
    elif score is not None and previous is None:
        print(f"[pplx-audit] Puntaje {format_score(score)} (sin BEST_SCORE previo).")
    elif score is not None:
        print(
            f"[pplx-audit] Puntaje {format_score(score)} "
            f"(BEST_SCORE actual: {format_score(previous)})."
        )
    else:
        print("[pplx-audit] AVISO: no se encontro PUNTAJE en la respuesta.")

    best_score = previous
    if score is not None and (previous is None or score >= previous):
        best_score = score

    if memory_path is not None:
        memory_text = load_memory_text(memory_path)
        memory_text, added = append_findings(memory_text, findings, day)
        memory_text = update_memory_best_score(memory_text, best_score, timestamp)
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_path.write_text(memory_text, encoding="utf-8")
        print(f"[pplx-audit] Memoria: +{added} hallazgo(s) en {memory_path.name}.")

    if state_path is not None:
        state["best_score"] = best_score
        state["updated_at"] = timestamp
        history = state.get("history")
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "timestamp": timestamp,
                "score": score,
                "best_score": best_score,
                "regression": regression,
                "evaluation_id": (result or {}).get("id", "-"),
                "out": str(out_path),
                "files_count": files_count,
            }
        )
        state["history"] = history[-100:]
        save_state(state_path, state)

    return score, regression


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auditoria Perplexity via bridge local.")
    parser.add_argument("--files", nargs="*", default=[], help="Lista de archivos a auditar.")
    parser.add_argument("--prompt", default="", help="Rubrica / prompt de evaluacion.")
    parser.add_argument("--out", default="audit_result.txt", help="Archivo destino del resultado.")
    parser.add_argument(
        "--browser",
        default=None,
        help=(
            "Navegador a usar: 'brave' | 'chrome' | 'edge' o ruta directa al "
            ".exe. Por defecto PPLX_BROWSER / PPLX_BROWSER_PATH o autodeteccion "
            "Brave > Chrome > Edge."
        ),
    )
    parser.add_argument(
        "--memory",
        default=DEFAULT_MEMORY_FILE,
        help=f"Memoria de proyecto (por defecto {DEFAULT_MEMORY_FILE}).",
    )
    parser.add_argument(
        "--state",
        default=DEFAULT_STATE_FILE,
        help=f"Estado best-known (por defecto {DEFAULT_STATE_FILE}).",
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="No escribir PROJECT_MEMORY.md ni el estado best-known.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.prompt.strip():
        print("[pplx-audit] ERROR: --prompt vacio.", file=sys.stderr)
        return 2
    if not args.files:
        print("[pplx-audit] ERROR: --files vacio: indica al menos un archivo.", file=sys.stderr)
        return 2

    if not is_port_open("127.0.0.1", 8000):
        ensure_browser(args.browser)
    ensure_bridge()

    abs_files = canonicalize_files(args.files)
    missing = [f for f in abs_files if not Path(f).is_file()]
    if missing:
        for m in missing:
            print(f"[pplx-audit] ERROR: archivo no encontrado: {m}", file=sys.stderr)
        return 2

    print(f"[pplx-audit] Evaluando {len(abs_files)} archivo(s) ...")
    result = post_evaluate(args.prompt, abs_files)

    if isinstance(result, dict) and "answer" in result:
        text_to_save = str(result.get("answer") or "")
    elif isinstance(result, dict) and "raw" in result:
        text_to_save = str(result["raw"])
    else:
        text_to_save = json.dumps(result, ensure_ascii=False, indent=2)

    out_path = Path(args.out).expanduser()
    if not out_path.is_absolute():
        out_path = (Path.cwd() / out_path).resolve()
    else:
        out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text_to_save, encoding="utf-8")

    memory_path = None if args.no_memory else resolve_project_path(args.memory)
    state_path = None if args.no_memory else resolve_project_path(args.state)
    process_memory_and_state(
        answer_text=text_to_save,
        result=result if isinstance(result, dict) else None,
        out_path=out_path,
        memory_path=memory_path,
        state_path=state_path,
        files_count=len(abs_files),
    )

    answer_chars = len(text_to_save)
    eval_id = result.get("id", "-") if isinstance(result, dict) else "-"
    print(f"[pplx-audit] OK id={eval_id} archivos={len(abs_files)} "
          f"chars={answer_chars} out={out_path}")
    preview = text_to_save[:500].replace("\n", " ")
    if preview:
        print(f"[pplx-audit] Vista previa: {preview}{'...' if len(text_to_save) > 500 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
