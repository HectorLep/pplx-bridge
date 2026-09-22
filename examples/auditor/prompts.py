"""Prompts de Tech Lead / rubrica y calificacion 0-100 del caso auditor.

Los textos viven en ``examples/auditor/prompts/*.md`` para poder editarlos
sin tocar codigo; si faltan, se usan los fallbacks embebidos.
"""

from __future__ import annotations

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

SCORE_SCHEMA = (
    "- PUNTAJE: [X]/100\n"
    "- VEREDICTO: [APROBADO] (solo si >=90) o [RECHAZADO]\n"
    "- OBSERVACIONES: lista de fallas criticas/deuda/mejoras."
)

_FALLBACK_TECH_LEAD = (
    "Actua como Tech Lead Principal y Evaluador Tecnico Estricto. Analiza "
    "LexiEngine evaluando: 1. Complejidad algoritmica de indexer.py y trie.py "
    "(latencia minima p95<2ms?). 2. Modularidad, memoria (__slots__), tipado "
    "estricto. 3. Cobertura de tests y rigor del benchmark.\n"
    "Responde OBLIGATORIAMENTE con este esquema, sin recortarlo:\n"
    f"{SCORE_SCHEMA}"
)

_FALLBACK_RUBRIC = (
    "Actua como auditor tecnico estricto y evalua los archivos adjuntos.\n"
    "Responde OBLIGATORIAMENTE con este esquema, sin recortarlo:\n"
    f"{SCORE_SCHEMA}\n\n"
    "Criterios de calificacion (0-100):\n"
    "1. Correccion funcional y manejo de errores (40 puntos).\n"
    "2. Diseno, modularidad y arquitectura (20 puntos).\n"
    "3. Cobertura y rigor de los tests (20 puntos).\n"
    "4. Legibilidad, tipado y documentacion (10 puntos).\n"
    "5. Seguridad y limites de entrada (10 puntos)."
)


def load_prompt(name: str, fallback: str = "") -> str:
    """Lee un prompt de ``prompts/<name>`` o devuelve *fallback*."""
    path = PROMPTS_DIR / name
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return fallback.strip()
    return text or fallback.strip()


TECH_LEAD_PROMPT = load_prompt("tech_lead.md", _FALLBACK_TECH_LEAD)
DEFAULT_RUBRIC = load_prompt("rubric.md", _FALLBACK_RUBRIC)

_SCORE_PATTERNS = (
    re.compile(
        r"\b(?:PUNTAJE|SCORE|NOTA|CALIFICACION|CALIFICACI\u00d3N)\b\s*[:=]?\s*"
        r"\[?\s*(\d{1,3})\s*\]?\s*(?:/\s*(\d{1,3}))?",
        re.IGNORECASE,
    ),
    re.compile(r"\b(\d{1,3})\s*/\s*100\b"),
    re.compile(r"\b(\d{1,3})\s*(?:de|sobre)\s*100\b", re.IGNORECASE),
)


def extract_score(text: str) -> float | None:
    """Extrae el puntaje de una respuesta y lo normaliza a escala 0-100."""
    for pattern in _SCORE_PATTERNS:
        for match in pattern.finditer(text or ""):
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


__all__ = [
    "DEFAULT_RUBRIC",
    "PROMPTS_DIR",
    "SCORE_SCHEMA",
    "TECH_LEAD_PROMPT",
    "extract_score",
    "load_prompt",
]
