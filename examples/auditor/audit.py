"""Auditoría LexiEngine vía el puente web (caso de uso de ejemplo).

Empaqueta un resumen compacto (<8000 caracteres) con APIs, métricas de
latencia y cobertura para evitar el truncado a mitad de fichero que
provocaba respuestas "Pensando..." del puente web.

El shim ``audit.py`` de la raíz reexporta este módulo para no romper los
scripts existentes.
"""

from __future__ import annotations

import argparse
import ast
import os
import time
from pathlib import Path

import httpx

from .prompts import TECH_LEAD_PROMPT, extract_score

BRIDGE_URL = "http://127.0.0.1:8000/v1/chat/completions"
MAX_PROMPT_CHARS = 7000
TARGET_FILES: list[str] = [
    "src/engine/trie.py",
    "src/engine/indexer.py",
    "src/engine/anagram.py",
    "src/engine/loader.py",
    "src/server.py",
    "src/cli.py",
]


def file_api_summary(path: Path) -> str:
    """Resume funciones/clases con sus type hints (una línea por def)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return f"{path}: (no parseable)"
    lines: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(
                f"{a.arg}: {ast.unparse(a.annotation) if a.annotation else '?'}"
                for a in node.args.args
            )
            ret = ast.unparse(node.returns) if node.returns else "?"
            lines.append(f"  def {node.name}({args}) -> {ret}")
        elif isinstance(node, ast.ClassDef):
            lines.append(f"  class {node.name}")
    return f"{path} ({len(lines)} defs):\n" + "\n".join(lines[:20])


def measure_latency() -> dict[str, float]:
    """Mide p95 real de trie/indexer sobre el diccionario (100 muestras)."""
    from src.engine.indexer import NGramIndexer
    from src.engine.loader import load_word_list
    from src.engine.trie import Trie

    words = load_word_list(Path("data/dictionary_es.txt"))
    trie = Trie()
    idx = NGramIndexer()
    for w in words:
        trie.insert(w)
        idx.add_word(w)
    out: dict[str, float] = {"words": float(len(words))}
    for name, fn, queries in [
        ("trie_p95", lambda q: trie.autocomplete(q, limit=10), ["cas", "ar", "niñ", "a"]),
        ("index_p95", lambda q: idx.contains(q, limit=20), ["cas", "arbol", "ño", "a"]),
    ]:
        for q in queries:  # calentamiento
            fn(q)
        lat: list[float] = []
        for q in queries * 25:
            t0 = time.perf_counter()
            fn(q)
            lat.append((time.perf_counter() - t0) * 1000.0)
        lat.sort()
        out[name] = round(lat[int(len(lat) * 0.95) - 1], 4)
    out["trie_nodes"] = float(trie.node_count)
    out["ngrams"] = float(idx.ngram_count)
    return out


def count_tests() -> dict[str, int]:
    """Cuenta tests y líneas sin ejecutar pytest (rápido y sin dependencias)."""
    n_tests = 0
    for root, _, files in os.walk(Path("tests")):
        for f in files:
            if f.startswith("test_") and f.endswith(".py"):
                text = (Path(root) / f).read_text(encoding="utf-8")
                n_tests += text.count("def test_")
    src_lines = sum(
        len(Path(p).read_text(encoding="utf-8").splitlines())
        for p in TARGET_FILES
        if Path(p).exists()
    )
    return {"n_tests": n_tests, "src_lines": src_lines}


def build_prompt(max_chars: int = MAX_PROMPT_CHARS) -> str:
    """Construye el prompt compacto sin cortar a mitad de línea."""
    metrics = measure_latency()
    counts = count_tests()
    parts: list[str] = [
        "Proyecto LexiEngine: Trie+Radix con __slots__, índice N-gramas "
        "dict[ngram,set[id]] O(1), anagramas dict[firma,list] O(1), "
        "sub-anagramas O(N·A) lineal, FastAPI+CLI, normalize NFD preservando ñ.",
        f"MÉTRICAS: palabras={metrics.get('words')} trie_nodos={metrics.get('trie_nodes')} "
        f"ngramas={metrics.get('ngrams')} trie_p95={metrics.get('trie_p95')}ms "
        f"index_p95={metrics.get('index_p95')}ms tests={counts['n_tests']} "
        f"src_lineas={counts['src_lines']}",
    ]
    for f in TARGET_FILES:
        p = Path(f)
        if p.exists():
            parts.append(file_api_summary(p))
    header = TECH_LEAD_PROMPT + "\n\nRESUMEN:\n"
    body = "\n\n".join(parts)
    prompt = header + body
    if len(prompt) > max_chars:
        # Cortar solo en frontera de línea y marcar el corte.
        cut = prompt[:max_chars].rsplit("\n", 1)[0]
        prompt = cut + "\n[RESUMEN TRUNCADO EN FRONTERA DE LÍNEA]"
    return prompt


def get_code(max_chars: int = MAX_PROMPT_CHARS) -> str:
    """Compat: devuelve el prompt compacto (ya no vuelca 20k a mitad de fichero)."""
    return build_prompt(max_chars=max_chars)


def run_audit(
    url: str = BRIDGE_URL, timeout_s: float = 300.0, max_chars: int = MAX_PROMPT_CHARS
) -> str:
    """Envía el prompt al puente y devuelve el contenido del auditor."""
    prompt = build_prompt(max_chars=max_chars)
    payload = {
        "model": "perplexity-web",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    resp = httpx.post(url, json=payload, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    if not content or not content.strip():
        raise ValueError("puente devolvió contenido vacío")
    # Detectar fallo típico del puente (extracción de UI en vez de respuesta).
    low = content.strip().lower()
    if low in {"respuesta", "pensando", "pensando..."} or (
        len(content.strip()) < 50 and "puntaje" not in low
    ):
        raise ValueError(f"respuesta del puente incompleta/truncada: {content[:200]!r}")
    return content


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI. Devuelve código de salida."""
    parser = argparse.ArgumentParser(description="Auditoría LexiEngine vía Perplexity")
    parser.add_argument("--url", default=BRIDGE_URL)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--max-chars", type=int, default=MAX_PROMPT_CHARS)
    parser.add_argument("--dry-run", action="store_true", help="solo imprime el prompt")
    parser.add_argument("--out", default="audit_result.txt")
    args = parser.parse_args(argv)

    try:
        prompt = build_prompt(max_chars=args.max_chars)
        print(f"[audit] prompt_chars={len(prompt)}")
        if args.dry_run:
            print(prompt)
            return 0
        content = run_audit(url=args.url, timeout_s=args.timeout, max_chars=args.max_chars)
    except Exception as exc:  # noqa: BLE001 - reportar cualquier fallo del puente
        print(f"ERROR_BRIDGE: {exc}")
        return 1
    Path(args.out).write_text(content, encoding="utf-8")
    print(f"[audit] OK: {len(content)} caracteres -> {args.out}")
    # Validar esquema mínimo.
    low = content.lower()
    if "puntaje" not in low or "veredicto" not in low:
        print("[audit] AVISO: la respuesta no contiene el esquema PUNTAJE/VEREDICTO")
        return 2
    score = extract_score(content)
    if score is not None:
        print(f"[audit] PUNTAJE: {score:g}/100")
    print("Success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
