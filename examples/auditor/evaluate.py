"""CLI de auditoria: evalua archivos adjuntos via POST /v1/evaluate.

El prompt lleva SOLO la rubrica; los archivos se adjuntan de forma nativa
en el navegador (no se vuelca el codigo fuente en el texto del prompt).

Ejemplos::

    python -m examples.auditor.evaluate --default-rubric --files src/engine/trie.py
    python -m examples.auditor.evaluate --rubric-file rubric.txt --attachments src/engine/trie.py src/engine/indexer.py
    python -m examples.auditor.evaluate --prompt "Evalua segun rubrica..." --files data/dictionary_es.txt --out audit_result.txt
    python -m examples.auditor.evaluate --rubric-file rubric.txt --files src/engine/trie.py --dry-run

El shim ``skills/pplx_auditor/evaluate.py`` reexporta este modulo.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from .prompts import DEFAULT_RUBRIC, extract_score

DEFAULT_URL = "http://127.0.0.1:8000/v1/evaluate"
DEFAULT_OUT = "audit_result.txt"


def load_rubric(
    prompt: str | None,
    rubric_file: str | None,
    *,
    use_default: bool = False,
) -> str:
    """Devuelve la rubrica (solo la rubrica) desde texto o fichero."""
    if rubric_file:
        text = Path(rubric_file).read_text(encoding="utf-8-sig").strip()
        if not text:
            raise ValueError(f"rubrica vacia: {rubric_file}")
        return text
    text = (prompt or "").strip()
    if not text and use_default:
        return DEFAULT_RUBRIC
    if not text:
        raise ValueError("indica --prompt, --rubric-file o --default-rubric")
    return text


def run_evaluate(url: str, rubric: str, attachments: list[str], timeout_s: float) -> dict:
    """POST /v1/evaluate y devuelve el JSON (lanza si falla)."""
    payload = {"query": rubric, "attachments": attachments}
    resp = httpx.post(url, json=payload, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    answer = (data.get("response") or data.get("answer") or "").strip()
    if not answer:
        raise ValueError("el puente devolvio respuesta vacia")
    low = answer.strip().lower()
    if low in {"respuesta", "pensando", "pensando..."} or (
        len(answer.strip()) < 50 and "puntaje" not in low
    ):
        raise ValueError(f"respuesta del puente incompleta/truncada: {answer[:200]!r}")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audita archivos via puente Perplexity (/v1/evaluate)"
    )
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--prompt", default=None, help="rubrica inline (SOLO rubrica)")
    parser.add_argument("--rubric-file", default=None, help="fichero con la rubrica")
    parser.add_argument(
        "--default-rubric",
        action="store_true",
        help="usa la rubrica por defecto de examples/auditor/prompts/rubric.md",
    )
    parser.add_argument(
        "--attachments",
        nargs="+",
        default=[],
        help="archivos a adjuntar (se inyectan de forma nativa en el navegador)",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        default=[],
        help="alias legacy de --attachments",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--dry-run", action="store_true", help="solo valida y muestra payload")
    args = parser.parse_args(argv)

    attachments = list(args.attachments or args.files)
    try:
        rubric = load_rubric(args.prompt, args.rubric_file, use_default=args.default_rubric)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR_RUBRICA: {exc}")
        return 1
    if not attachments and not args.dry_run:
        print("ERROR_FILES: indica al menos un --attachments/--files para /v1/evaluate")
        return 1
    missing = [f for f in attachments if not Path(f).exists()]
    if missing:
        print(f"ERROR_FILES: no encontrados: {missing}")
        return 1

    print(f"[evaluate] url={args.url} rubric_chars={len(rubric)} files={len(attachments)}")
    for f in attachments:
        print(f"[evaluate]   file: {f}")
    if args.dry_run:
        print("[evaluate] dry-run: payload valido, sin enviar")
        print(rubric[:2000])
        return 0

    try:
        data = run_evaluate(args.url, rubric, attachments, args.timeout)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR_BRIDGE: {exc}")
        return 1
    answer = data.get("response") or data.get("answer") or ""
    Path(args.out).write_text(answer, encoding="utf-8")
    print(f"[evaluate] OK: {len(answer)} caracteres -> {args.out}")
    low = answer.lower()
    if "puntaje" not in low or "veredicto" not in low:
        print("[evaluate] AVISO: la respuesta no contiene el esquema PUNTAJE/VEREDICTO")
        return 2
    score = extract_score(answer)
    if score is not None:
        print(f"[evaluate] PUNTAJE: {score:g}/100")
    print("Success")
    return 0


if __name__ == "__main__":
    sys.exit(main())
