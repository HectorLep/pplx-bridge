#!/usr/bin/env bash
# setup.sh - Instalacion "zero-friction" de Pplx Bridge (Linux/macOS).
#
#   ./setup.sh                  # auto: usa Docker si esta listo; si no, local
#   ./setup.sh --mode docker    # construye y levanta docker-compose
#   ./setup.sh --mode local     # deps Python + login guiado una sola vez
#   ./setup.sh --no-build       # docker: no reconstruye la imagen
#
# El login se guarda en el perfil persistente; no se vuelve a pedir.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MODE="auto"
NO_BUILD=0
FORCE_LOGIN=0
PORT=8000

while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:-auto}"; shift 2 ;;
    --no-build) NO_BUILD=1; shift ;;
    --force-login) FORCE_LOGIN=1; shift ;;
    --port) PORT="${2:-8000}"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | head -n 8
      exit 0 ;;
    *) echo "[aviso] argumento desconocido: $1"; shift ;;
  esac
done

step() { printf '\n==> %s\n' "$1"; }
ok()   { printf '[ok] %s\n' "$1"; }
warn() { printf '[aviso] %s\n' "$1"; }
err()  { printf '[error] %s\n' "$1" >&2; }

PY=""
find_python() {
  if command -v python3 >/dev/null 2>&1; then PY="python3"; return 0; fi
  if command -v python >/dev/null 2>&1; then PY="python"; return 0; fi
  return 1
}

python_ok() {
  [ -n "$PY" ] || return 1
  "$PY" - <<'EOF' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
EOF
}

python_deps_ok() {
  [ -n "$PY" ] || return 1
  "$PY" -c "import fastapi, uvicorn, pydantic, httpx, playwright" >/dev/null 2>&1
}

bridge_profile_dir() {
  [ -n "$PY" ] || return 1
  "$PY" -c "from core_bridge.browser import USER_DATA_DIR; print(USER_DATA_DIR)" 2>/dev/null
}

docker_ready() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
  else
    return 1
  fi
}

wait_bridge() {
  local url="http://127.0.0.1:${PORT}/health"
  local i
  for i in $(seq 1 90); do
    if command -v curl >/dev/null 2>&1; then
      curl -fsS "$url" >/dev/null 2>&1 && return 0
    elif [ -n "$PY" ]; then
      "$PY" -c "import urllib.request,sys; urllib.request.urlopen('$url', timeout=2)" >/dev/null 2>&1 && return 0
    else
      return 1
    fi
    sleep 1
  done
  return 1
}

login() {
  # Login guiado en el perfil efectivo del bridge. No impone rutas fijas:
  # 'python -m core_bridge.cli login' resuelve PPLX_USER_DATA_DIR o el
  # perfil aislado por navegador y escribe alli el marcador de sesion.
  local user_data_dir=""
  user_data_dir="$(bridge_profile_dir || true)"
  if [ -n "$user_data_dir" ] && [ -f "${user_data_dir}/.pplx_login_ok" ] && [ "$FORCE_LOGIN" -eq 0 ]; then
    ok "Sesion de Perplexity ya inicializada (${user_data_dir}/.pplx_login_ok)."
    return 0
  fi
  if ! python_deps_ok; then
    warn "No hay Python+Playwright locales para abrir la ventana de login."
    echo "       Ejecuta primero: ./setup.sh --mode local (o instala deps y repite)."
    return 1
  fi
  step "Login guiado (una sola vez) - perfil: ${user_data_dir:-por defecto de core_bridge}"
  if [ "$FORCE_LOGIN" -eq 1 ]; then
    "$PY" -m core_bridge.cli login --force
  else
    "$PY" -m core_bridge.cli login
  fi
}

install_local_deps() {
  step "Verificando Python 3.10+"
  if ! find_python; then
    err "No se encontro python3. Instala Python 3.10+."
    exit 1
  fi
  if ! python_ok; then
    err "Se requiere Python 3.10+ ($("$PY" --version 2>&1))."
    exit 1
  fi
  ok "Python detectado: $("$PY" --version 2>&1)"

  if ! python_deps_ok; then
    step "Instalando dependencias (requirements.txt)"
    "$PY" -m pip install -r requirements.txt || { err "pip install fallo."; exit 1; }
    step "Instalando navegador Chromium de Playwright"
    "$PY" -m playwright install chromium || warn "playwright install fallo; el bridge puede no arrancar."
  else
    ok "Dependencias Python listas (fastapi, uvicorn, pydantic, httpx, playwright)."
  fi
}

add_docker_env_file() {
  if [ ! -f .env ]; then
    cp .env.example .env
    ok "Creado .env con variables base (PPLX_PORT, PPLX_TIMEOUT_S)."
  fi
  if grep -q '^PPLX_PORT=' .env; then
    sed -i.bak "s/^PPLX_PORT=.*/PPLX_PORT=${PORT}/" .env && rm -f .env.bak
  else
    echo "PPLX_PORT=${PORT}" >> .env
  fi
}

echo "Pplx Bridge - setup"
echo "Modo solicitado: ${MODE}"

if [ "$MODE" = "auto" ]; then
  if docker_ready; then MODE="docker"; ok "Docker disponible: modo docker."
  else MODE="local"; ok "Docker no disponible: modo local."; fi
fi

if [ "$MODE" = "docker" ]; then
  step "Comprobando Docker"
  if ! docker_ready; then
    err "Docker no esta instalado o el daemon no responde."
    exit 1
  fi
  if ! COMPOSE="$(compose_cmd)"; then
    err "No se encontro 'docker compose' ni 'docker-compose'."
    exit 1
  fi
  ok "Docker listo."

  mkdir -p profile
  add_docker_env_file

  # El contenedor monta ./profile como PPLX_USER_DATA_DIR (/data/profile):
  # el login del host debe escribir en ese mismo perfil. Se exporta solo
  # durante el login y se restaura el valor previo del entorno.
  find_python || true
  SAVED_USER_DATA_DIR="${PPLX_USER_DATA_DIR:-}"
  export PPLX_USER_DATA_DIR="${ROOT}/profile"
  if ! login; then
    warn "El contenedor arrancara igualmente; sin login las consultas fallaran."
  fi
  if [ -n "$SAVED_USER_DATA_DIR" ]; then
    export PPLX_USER_DATA_DIR="$SAVED_USER_DATA_DIR"
  else
    unset PPLX_USER_DATA_DIR
  fi

  step "Levantando contenedor (puerto ${PORT})"
  if [ "$NO_BUILD" -eq 1 ]; then
    $COMPOSE up -d
  else
    $COMPOSE up -d --build
  fi
  if [ $? -ne 0 ]; then err "docker compose up fallo."; exit 1; fi

  find_python || true
  if wait_bridge; then
    ok "Bridge respondiendo en http://127.0.0.1:${PORT}/health"
  else
    warn "El bridge aun no responde. Revisa logs: $COMPOSE logs -f"
  fi
  echo ""
  echo "Siguientes pasos:"
  echo "  1. Auditar:  python tools/run_pplx_audit.py --files src/engine/trie.py --prompt \"PUNTAJE: [X]/100 ...\""
  echo "  2. Logs:     $COMPOSE logs -f"
  echo "  3. Parar:    $COMPOSE down"
  exit 0
fi

# Modo local
install_local_deps
if ! login; then
  warn "Sin login el bridge arrancara pero Perplexity pedira autenticacion."
fi
LOCAL_PROFILE="$(bridge_profile_dir || true)"
[ -n "$LOCAL_PROFILE" ] || LOCAL_PROFILE="(por defecto de core_bridge)"
echo ""
echo "Listo. Siguientes pasos (el bridge se arranca solo si hace falta):"
echo "  1. Auditar:  python tools/run_pplx_audit.py --files src/engine/trie.py --prompt \"PUNTAJE: [X]/100 ...\""
echo "  2. API:      ${PY} -m core_bridge.cli start   (http://127.0.0.1:${PORT}/health)"
echo "  3. Perfil:   ${LOCAL_PROFILE}"
exit 0
