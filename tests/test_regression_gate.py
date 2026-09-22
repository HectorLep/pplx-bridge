"""Test de regresion best-known-state.

Verifica que ``process_memory_and_state`` (tools/run_pplx_audit.py):
  a) nunca baja BEST_SCORE en ``.best_known_state.json`` ante una caida,
  b) registra la alerta de regresion (con delta) en ``## REGRESSIONS``
     de PROJECT_MEMORY.md,
  c) solo actualiza BEST_SCORE cuando el puntaje mejora.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_pplx_audit import process_memory_and_state  # noqa: E402

BEST_SCORE = 91.0

REGRESSION_REPORT = """INFORME DE AUDITORIA
PUNTAJE: 70/100
Veredicto: no aprobado.

Fallas criticas detectadas:
- Falla critica: la API /v1/evaluate devuelve resultados incorrectos.
- Error grave: crash al cargar el diccionario con acentos.
"""

HAPPY_REPORT = """INFORME DE AUDITORIA
PUNTAJE: 95/100
Veredicto: aprobado sin hallazgos criticos.
"""


def _seed_state(tmp_path: Path, best_score: float = BEST_SCORE) -> Path:
    """Estado best-known base con BEST_SCORE=91.0 (o el indicado)."""
    state_path = tmp_path / ".best_known_state.json"
    state_path.write_text(
        json.dumps(
            {
                "best_score": best_score,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "history": [
                    {
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "score": best_score,
                        "best_score": best_score,
                        "regression": False,
                        "evaluation_id": "baseline",
                        "out": "audit_result.txt",
                        "files_count": 1,
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return state_path


def _seed_memory(tmp_path: Path, best_score: float = BEST_SCORE) -> Path:
    """PROJECT_MEMORY.md base con las secciones esperadas."""
    memory_path = tmp_path / "PROJECT_MEMORY.md"
    memory_path.write_text(
        "# PROJECT MEMORY\n\n"
        "Memoria acumulada de auditorias Perplexity.\n\n"
        "## BEST_KNOWN_STATE\n\n"
        f"BEST_SCORE: {best_score:g}  (actualizado 2026-01-01T00:00:00+00:00)\n\n"
        "## SECURITY\n\n(sin entradas)\n\n"
        "## REGRESSIONS\n\n(sin entradas)\n\n"
        "## ARCHITECTURE\n\n(sin entradas)\n\n"
        "## OBSERVATIONS\n\n(sin entradas)\n",
        encoding="utf-8",
    )
    return memory_path


def _section(text: str, name: str) -> str:
    """Devuelve el cuerpo de la seccion ``## name`` (sin el encabezado)."""
    marker = f"## {name}"
    start = text.index(marker) + len(marker)
    rest = text[start:]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def test_regression_keeps_best_known_state_and_alerts(tmp_path: Path) -> None:
    """Caida 91 -> 70: BEST_SCORE inmutable y alerta con delta en memoria."""
    state_path = _seed_state(tmp_path)
    memory_path = _seed_memory(tmp_path)
    out_path = tmp_path / "audit_result.txt"

    score, regression = process_memory_and_state(
        answer_text=REGRESSION_REPORT,
        result={"id": "eval-regression-70", "answer": REGRESSION_REPORT},
        out_path=out_path,
        memory_path=memory_path,
        state_path=state_path,
        files_count=3,
    )

    assert score == 70.0
    assert regression is True

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["best_score"] == BEST_SCORE
    assert state["history"][-1]["score"] == 70.0
    assert state["history"][-1]["best_score"] == BEST_SCORE
    assert state["history"][-1]["regression"] is True

    memory = memory_path.read_text(encoding="utf-8")
    regressions = _section(memory, "REGRESSIONS")
    assert "ALERTA DE REGRESION" in regressions
    assert "delta: -21" in regressions
    assert "eval-regression-70" in regressions
    best_state = _section(memory, "BEST_KNOWN_STATE")
    assert "BEST_SCORE: 91" in best_state


def test_improvement_updates_best_known_state(tmp_path: Path) -> None:
    """Camino feliz 91 -> 95: BEST_SCORE sube a 95.0 y regression=False."""
    state_path = _seed_state(tmp_path)
    memory_path = _seed_memory(tmp_path)
    out_path = tmp_path / "audit_result.txt"

    score, regression = process_memory_and_state(
        answer_text=HAPPY_REPORT,
        result={"id": "eval-happy-95", "answer": HAPPY_REPORT},
        out_path=out_path,
        memory_path=memory_path,
        state_path=state_path,
        files_count=2,
    )

    assert score == 95.0
    assert regression is False

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["best_score"] == 95.0
    assert state["history"][-1]["score"] == 95.0
    assert state["history"][-1]["best_score"] == 95.0
    assert state["history"][-1]["regression"] is False

    memory = memory_path.read_text(encoding="utf-8")
    assert "BEST_SCORE: 95" in _section(memory, "BEST_KNOWN_STATE")
    assert "ALERTA DE REGRESION" not in _section(memory, "REGRESSIONS")
