---
name: pplx-auditor
description: Audita codigo fuente via Perplexity web (Playwright) con archivos adjuntos nativos y extraccion sincronizada. Usar cuando se pida evaluar, auditar o puntuar ficheros del repo con rubrica.
---

# Skill pplx-auditor

Auditor tecnico via puente local `core_bridge` (Perplexity web con Playwright).
El prompt lleva **SOLO la rubrica**; los archivos se adjuntan de forma nativa al navegador.
`tools/pplx_bridge` y `skills/pplx_auditor/evaluate.py` se mantienen como shims de compatibilidad.

## Cuando usar

- Evaluar/auditar/puntuar archivos del repo (p. ej. `src/engine/*.py`) con una rubrica PUNTAJE/VEREDICTO.
- Obtener veredictos estrictos sin volcar codigo en el prompt (evita truncados y "Pensando...").

## Arquitectura

```text
examples/auditor/evaluate.py  (CLI de auditoria; shim en skills/pplx_auditor/evaluate.py)
  -> POST http://127.0.0.1:8000/v1/evaluate  {query, attachments}  (alias legacy: prompt, files)
    -> core_bridge/server.py  (FastAPI, mantiene /v1/chat/completions)
      -> core_bridge/browser.py  (Playwright persistente + Circuit Breaker de 3 fallos)
```

## Requisitos

- Servidor puente levantado:

```sh
python -m core_bridge.cli start --host 127.0.0.1 --port 8000
# o
uvicorn core_bridge.server:app --host 127.0.0.1 --port 8000
```

- Sesion de Perplexity iniciada en el perfil Playwright (`PPLX_USER_DATA_DIR`)
  o Chrome con `--remote-debugging-port=9222` + `PPLX_CDP_URL=http://127.0.0.1:9222`.
- Variables opcionales: `PPLX_TIMEOUT_S=180` (defecto 180),
  `PPLX_STABLE_S=3.0` (DOM estable), `PPLX_POLL_S=1.0`, `PPLX_HEADLESS=0`.

## Reglas del puente (no saltar)

1. **Archivos**: inyeccion con `page.set_input_files("input[type='file']", archivos)`
   antes de escribir el prompt. El prompt contiene SOLO la rubrica.
2. **Sincronizacion**: NUNCA extraer si hay spinners o "Pensando...".
   Fin de streaming = DOM/respuesta estable 3s (`STABLE_S`) Y boton
   Copiar o Compartir visible Y sin boton Stop. Timeout 180s.
3. **Extraccion**: ultimo bloque visible `.prose` / `div[dir=auto]`
   (respuesta mas reciente). Si reaparece spinner/`Pensando...`, se descarta.
4. **Endpoints**: `POST /v1/evaluate` = `{prompt, files}` para auditorias;
   `POST /v1/chat/completions` (OpenAI-compatible) se mantiene para chat.

## Script CLI

`skills/pplx_auditor/evaluate.py` — envia rubrica + archivos a `/v1/evaluate`.

```sh
# Rubrica desde fichero + 2 archivos adjuntos
python skills/pplx_auditor/evaluate.py --rubric-file rubric.txt --files src/engine/trie.py src/engine/indexer.py

# Rubrica inline + salida personalizada
python skills/pplx_auditor/evaluate.py --prompt "Actua como Tech Lead..." --files src/engine/trie.py --out audit_result.txt

# Solo validar payload (sin llamar al puente)
python skills/pplx_auditor/evaluate.py --rubric-file rubric.txt --files src/engine/trie.py --dry-run
```

Argumentos:

| Flag | Defecto | Descripcion |
|---|---|---|
| `--url` | `http://127.0.0.1:8000/v1/evaluate` | Endpoint de evaluacion |
| `--prompt` / `--rubric-file` | — | Rubrica (una de las dos, SOLO rubrica) |
| `--files` | — | Uno o mas archivos a adjuntar (requerido salvo `--dry-run`) |
| `--timeout` | `300.0` | Timeout HTTP (s) |
| `--out` | `audit_result.txt` | Fichero de salida con la respuesta |
| `--dry-run` | off | Valida rubrica + archivos sin enviar |

Contrato `POST /v1/evaluate` (generico; acepta alias legacy `prompt`/`files`):

```json
{"query": "<SOLO rubrica>", "attachments": ["src/engine/trie.py"], "timeout_s": 180}
```

Respuesta:

```json
{"object": "evaluation", "response": "...", "answer": "...", "attachments": ["..."]}
```

## Esquema de respuesta esperado

La rubrica debe exigir este esquema para que el CLI valide exito:

```text
- PUNTAJE: [X]/100
- VEREDICTO: [APROBADO] (solo si >=90) o [RECHAZADO]
- OBSERVACIONES: lista de fallas criticas/deuda/mejoras.
```

El CLI sale `0` si hay respuesta con esquema, `2` si falta PUNTAJE/VEREDICTO,
`1` si falla el puente o los archivos no existen.

## Ejemplo minimo

```sh
python -m core_bridge.cli start --host 127.0.0.1 --port 8000 &
python skills/pplx_auditor/evaluate.py --rubric-file rubric.txt --files src/engine/trie.py src/engine/indexer.py --out audit_result.txt
```
