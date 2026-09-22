# Core Bridge + Motor léxico español

Puente local OpenAI-compatible hacia **Perplexity web** (Playwright, con
Circuit Breaker anti-bucles) + motor léxico (Trie/radix + índice invertido de
N-gramas + anagramas) con API FastAPI y CLI. Insensible a tildes, distingue
`ñ` de `n`, memoria eficiente (`__slots__`).

El puente vive en `core_bridge/` y es **genérico** (`query`, `attachments`,
`response`); la lógica de auditoría (Tech Lead, PUNTAJE 0-100, memoria) vive
en `examples/auditor/` como caso de uso.

---

## Instalar y usar en 2 minutos

### Opción A — Local (flujo principal, Python 3.10+)

1. Instala dependencias y el Chromium de Playwright:

   ```sh
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

   O ejecuta el instalador: `.\setup.ps1 -Mode local` (Windows) /
   `./setup.sh --mode local` (Linux/macOS).

2. Haz el login guiado **una sola vez** (abre el navegador detectado con el
   perfil persistente y guarda el marcador `.pplx_login_ok`):

   ```sh
   python -m core_bridge.cli login [--browser brave|chrome|edge] [--user-data-dir DIR]
   ```

3. Arranca el puente:

   ```sh
   python -m core_bridge.cli start [--host 127.0.0.1] [--port 8000]
   ```

4. Audita con el lanzador (arranca navegador CDP + puente si hace falta):

   ```bat
   pplx-audit.bat --files src/engine/trie.py --prompt "Actua como auditor: PUNTAJE: [X]/100, VEREDICTO y fallas criticas."
   ```

   Verifica el servicio en `http://127.0.0.1:8000/health`.

> Multi-navegador: se autodetecta Brave > Google Chrome > Microsoft Edge
> (`PPLX_BROWSER`, `PPLX_BROWSER_PATH` o `--browser`). Con
> `PPLX_CDP_URL=http://127.0.0.1:9222` se conecta a un Chrome ya abierto.

### Opción B — Docker (alternativa)

1. `docker compose up -d --build` (o `.\setup.ps1` / `./setup.sh`, que eligen
   Docker si está disponible). El contenedor arranca
   `python -m core_bridge.cli start` en `http://127.0.0.1:8000` con
   healthcheck sobre `/health`.
2. La primera vez se abre la ventana guiada de login; la sesión queda en
   `./profile/` y no se vuelve a pedir.
3. Logs: `docker compose logs -f` · Parar: `docker compose down`.
   Variables base en `.env` (`PPLX_PORT`, `PPLX_TIMEOUT_S`).

---

## Regla anti-bucles (Circuit Breaker)

`core_bridge/browser.py` contabiliza **fallos consecutivos** (selectores DOM,
timeouts, errores de página). Al llegar a **3**:

- Aborta los reintentos ciegos (las siguientes consultas fallan al instante
  con `CircuitBreakerOpenError`, salvo sonda *half-open* tras
  `PPLX_BREAKER_COOLDOWN_S`, 300 s por defecto).
- Vuelca en `PPLX_ERROR_DIR` (por defecto `./pplx_errors/`):
  `error_snapshot.png`, `error_dom.html` y `error_state.log` (estado de
  sesión: URL, cookies contadas —nunca sus valores—, marcador de login…).
- Lanza una excepción con mensaje explícito indicando si la causa probable es
  **sesión caducada** (renueva con `python -m core_bridge.cli login`) o
  **cambio de DOM** (revisa `error_dom.html` y los selectores).
- Una consulta exitosa reinicia el contador a 0.

Umbral y cooldown configurables con `PPLX_FAILURE_THRESHOLD` y
`PPLX_BREAKER_COOLDOWN_S`.

---

## Estructura

```text
core_bridge/                      # puente generico (sin logica de auditoria)
  adapter.py                      # BaseWebAdapter + PerplexityAdapter (query/attachments/response)
  browser.py                      # Playwright multi-navegador + Circuit Breaker de 3 fallos
  server.py                       # FastAPI: /health, /v1/chat/completions, /v1/evaluate
  cli.py                          # python -m core_bridge.cli login | start
examples/auditor/                 # caso de uso auditor
  audit.py                        # auditoria LexiEngine (prompt compacto + metricas)
  evaluate.py                     # CLI rubrica + adjuntos (POST /v1/evaluate)
  prompts/tech_lead.md            # prompt Tech Lead
  prompts/rubric.md               # rubrica generica 0-100
  prompts.py                      # carga de prompts + extract_score (0-100)
audit.py                          # shim de compatibilidad -> examples/auditor/audit.py
Dockerfile / docker-compose.yml   # bridge en contenedor (puerto 8000, perfil ./profile)
setup.ps1 / setup.sh              # instalacion + login guiado (local o Docker)
pplx-audit.bat                    # lanzador global de auditorias (Windows)
data/dictionary_es.txt
src/engine/{trie.py,indexer.py,anagram.py,loader.py}
src/server.py                     # FastAPI del motor lexico
src/cli.py                        # CLI del motor lexico
tools/run_pplx_audit.py           # auditoria + memoria + best-known-state
tools/pplx_bridge/                # shim -> core_bridge (browser_client, server)
tools/pplx_login.py               # shim -> core_bridge.cli login
tools/audit.py                    # shim -> examples/auditor/audit.py
skills/pplx_auditor/evaluate.py   # shim -> examples/auditor/evaluate.py
tests/                            # pytest (75 tests)
```

## CLI unificada

```sh
python -m core_bridge.cli login [--browser BROWSER] [--user-data-dir DIR] [--force]
python -m core_bridge.cli start [--host HOST] [--port PORT]
```

- `login`: abre el Chromium detectado con el perfil persistente, navega a
  Perplexity y espera tu confirmación para escribir `.pplx_login_ok`.
- `start`: sirve el puente FastAPI (equivalentes: `python -m core_bridge`,
  `uvicorn core_bridge.server:app`, `python tools/pplx_bridge/server.py`).

## Memoria de proyecto y Best-Known-State

`tools/run_pplx_audit.py` analiza cada respuesta de Perplexity y:

- Registra hallazgos en `PROJECT_MEMORY.md`, clasificados en `SECURITY`,
  `REGRESSIONS`, `ARCHITECTURE` y `OBSERVATIONS` (sin duplicados).
- Mantiene `BEST_SCORE` en `.best_known_state.json` (histórico de corridas) y
  lo refleja en `PROJECT_MEMORY.md`.
- Si el puntaje cae respecto al mejor conocido, imprime una alerta explícita
  para permitir rollback inmediato:

  ```text
  [pplx-audit] ALERTA DE REGRESION: Puntaje cayo de 88 a 82 (delta: -6)
  ```

Opciones: `--memory PROJECT_MEMORY.md`, `--state .best_known_state.json`,
`--no-memory`. El resultado crudo se guarda en `--out` (por defecto
`audit_result.txt`). Estas compuertas no cambian con la refactorización.

## Uso del motor léxico

```sh
python -m src.cli autocomplete cas --limit 5
python -m src.cli search árbol
python -m src.cli anagram amor
python -m src.cli contains ño --limit 10
python -m src.cli stats
uvicorn src.server:app --reload
pytest tests/
```

## Endpoints

Puente genérico (`core_bridge/server.py`):
`GET /health` · `GET /v1/models` · `POST /v1/chat/completions`
(OpenAI-compatible) · `POST /v1/evaluate`
(`{"query": "...", "attachments": ["archivo.py"]}`; se aceptan los alias
legacy `prompt`/`files` y la respuesta incluye `response` y `answer`).

Motor léxico (`src/server.py`):
`GET /health /autocomplete /search /anagram /subanagram /contains /stats`.

## Compatibilidad

Se mantienen shims para no romper scripts ni tests:
`audit.py` (raíz), `tools/audit.py`, `tools/pplx_bridge/browser_client.py`,
`tools/pplx_bridge/server.py`, `tools/pplx_login.py` y
`skills/pplx_auditor/evaluate.py` reexportan la implementación nueva.

## Seguridad

`.gitignore` bloquea credenciales, cookies, `.profile/`, `profile/`,
`chrome_data/`, logs, `pplx_errors/` y artefactos de build. Nunca subas el
perfil de navegador: contiene tu sesión de Perplexity.
