"""CLI del motor léxico español.

Uso:
    python -m src.cli autocomplete cas --limit 5
    python -m src.cli search árbol
    python -m src.cli anagram amor
    python -m src.cli subanagram amor --min-len 3
    python -m src.cli contains ño --limit 10
    python -m src.cli stats
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .engine.anagram import AnagramSolver
from .engine.indexer import NGramIndexer
from .engine.loader import load_word_list
from .engine.trie import Trie, normalize

DEFAULT_DICT = Path(__file__).resolve().parent.parent / "data" / "dictionary_es.txt"


def load_all(dict_path: str | Path = DEFAULT_DICT) -> tuple[Trie, NGramIndexer, AnagramSolver, list[str]]:
    """Carga el diccionario en las tres estructuras.

    Args:
        dict_path: Ruta al diccionario.

    Returns:
        Tupla (trie, indexer, solver, words).
    """
    words: list[str] = load_word_list(dict_path)
    trie = Trie()
    indexer = NGramIndexer()
    solver = AnagramSolver()
    for w in words:
        trie.insert(w)
        indexer.add_word(w)
        solver.add_word(w)
    return trie, indexer, solver, words


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser de subcomandos."""
    parser = argparse.ArgumentParser(
        prog="lexico", description="Motor léxico español: trie, N-gramas y anagramas."
    )
    parser.add_argument(
        "--dict",
        dest="dict_path",
        default=str(DEFAULT_DICT),
        help="Ruta al diccionario (por defecto data/dictionary_es.txt)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ac = sub.add_parser("autocomplete", help="Completar un prefijo")
    p_ac.add_argument("prefix", help="Prefijo (insensible a tildes)")
    p_ac.add_argument("--limit", type=int, default=10, help="Máximo de resultados")

    p_s = sub.add_parser("search", help="Búsqueda exacta")
    p_s.add_argument("word", help="Palabra a buscar")

    p_a = sub.add_parser("anagram", help="Anagramas exactos")
    p_a.add_argument("word", help="Palabra base")
    p_a.add_argument("--include-self", action="store_true", help="Incluir la propia palabra")

    p_sub = sub.add_parser("subanagram", help="Sub-anagramas con letras dadas")
    p_sub.add_argument("letters", help="Letras disponibles")
    p_sub.add_argument("--min-len", type=int, default=2)
    p_sub.add_argument("--limit", type=int, default=50)

    p_c = sub.add_parser("contains", help="Búsqueda por subcadena (N-gramas)")
    p_c.add_argument("substring", help="Subcadena")
    p_c.add_argument("--limit", type=int, default=20)

    sub.add_parser("stats", help="Estadísticas del índice")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de la CLI. Devuelve código de salida."""
    parser: argparse.ArgumentParser = build_parser()
    args: argparse.Namespace = parser.parse_args(argv)
    t0: float = time.perf_counter()
    try:
        trie, indexer, solver, words = load_all(args.dict_path)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    load_ms: float = (time.perf_counter() - t0) * 1000.0

    if args.command == "autocomplete":
        for w in trie.autocomplete(args.prefix, limit=args.limit):
            print(w)
    elif args.command == "search":
        found: bool = trie.search(args.word)
        print(f"{args.word}: {'ENCONTRADA' if found else 'no encontrada'} (norm: {normalize(args.word)})")
        for f in trie.forms_of(args.word):
            print(f"  forma: {f}")
        return 0 if found else 1
    elif args.command == "anagram":
        for w in solver.anagrams_of(args.word, include_self=args.include_self):
            print(w)
    elif args.command == "subanagram":
        for w in solver.sub_anagrams(args.letters, min_len=args.min_len, limit=args.limit):
            print(w)
    elif args.command == "contains":
        for w in indexer.contains(args.substring, limit=args.limit):
            print(w)
    elif args.command == "stats":
        print(f"palabras: {len(words)}")
        print(f"trie: nodos={trie.node_count} claves={trie.key_count}")
        print(f"ngramas: {indexer.ngram_count} {indexer.stats()}")
        print(f"anagramas: {solver.stats()}")
        print(f"carga: {load_ms:.2f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
