"""Carga compartida del diccionario español.

Centraliza la lectura del .txt (una palabra por línea, UTF-8) para evitar
duplicación entre CLI y servidor y garantizar el mismo filtrado en ambos.
"""

from __future__ import annotations

from pathlib import Path


def load_word_list(path: str | Path) -> list[str]:
    """Lee el diccionario descartando vacías y comentarios (#).

    Args:
        path: Ruta al fichero UTF-8.

    Returns:
        Lista de formas originales en orden de aparición.

    Raises:
        FileNotFoundError: Si no existe el fichero.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Diccionario no encontrado: {p}")
    return [
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
