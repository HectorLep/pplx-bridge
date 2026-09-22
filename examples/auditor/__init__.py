"""Caso de uso auditor: prompts de Tech Lead/rubrica y CLI de evaluacion.

Vive fuera de ``core_bridge`` a proposito: el puente es generico y este
paquete contiene la logica de auditoria (LexiEngine, esquema PUNTAJE 0-100).
"""

from .prompts import (
    DEFAULT_RUBRIC,
    TECH_LEAD_PROMPT,
    extract_score,
    load_prompt,
)

__all__ = [
    "DEFAULT_RUBRIC",
    "TECH_LEAD_PROMPT",
    "extract_score",
    "load_prompt",
]
