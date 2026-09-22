# setup.ps1 - Instalacion "zero-friction" de Pplx Bridge (Windows).
#
#   .\setup.ps1                 # auto: usa Docker si esta listo; si no, local
#   .\setup.ps1 -Mode docker    # construye y levanta docker-compose
#   .\setup.ps1 -Mode local     # deps Python + login guiado una sola vez
#   .\setup.ps1 -NoBuild        # docker: no reconstruye la imagen
#
# El login se guarda en el perfil persistente; no se vuelve a pedir.

[CmdletBinding()]
param(
    [ValidateSet("auto", "docker", "local")]
    [string]$Mode = "auto",
    [switch]$NoBuild,
    [switch]$ForceLogin,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
if (-not $Root) { $Root = (Get-Location).Path }
Set-Location -LiteralPath $Root

$script:PyExe = $null
$script:PyPre = @()
$script:ComposePlugin = $true

function Write-Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "[ok] $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "[aviso] $Text" -ForegroundColor Yellow }
function Write-Err2([string]$Text) { Write-Host "[error] $Text" -ForegroundColor Red }

function Find-Python {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $script:PyExe = "python"; $script:PyPre = @(); return $true
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $script:PyExe = "py"; $script:PyPre = @("-3"); return $true
    }
    return $false
}

function Invoke-Py {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PyArgs)
    & $script:PyExe @($script:PyPre + $PyArgs)
}

function Get-PythonVersion {
    if (-not $script:PyExe) { return $null }
    $raw = (& $script:PyExe @($script:PyPre + @("--version")) 2>&1 | Out-String).Trim()
    if ($raw -match "Python (\d+)\.(\d+)") {
        return [pscustomobject]@{ Major = [int]$Matches[1]; Minor = [int]$Matches[2]; Raw = $raw }
    }
    return $null
}

function Test-PythonDeps {
    if (-not $script:PyExe) { return $false }
    # Un import fallido ensucia stderr y, con ErrorActionPreference=Stop en
    # PS 5.1, abortaria el script en vez de devolver $false (instalacion
    # limpia => dependencias ausentes). Se degrada a Continue y se captura.
    $prev = $ErrorActionPreference
    $ready = $false
    try {
        $ErrorActionPreference = "Continue"
        Invoke-Py "-c" "import fastapi, uvicorn, pydantic, httpx, playwright" *> $null
        $ready = ($LASTEXITCODE -eq 0)
    } catch {
        $ready = $false
    } finally {
        $ErrorActionPreference = $prev
    }
    return $ready
}

function Test-DockerReady {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    # Docker Desktop apagado escribe el error en stderr; en PS 5.1 con
    # ErrorActionPreference=Stop eso lanzaria NativeCommandError y abortaria
    # el setup. Se degrada a Continue y se devuelve $false para poder caer a
    # modo local.
    $prev = $ErrorActionPreference
    $ready = $false
    try {
        $ErrorActionPreference = "Continue"
        docker info *> $null
        $ready = ($LASTEXITCODE -eq 0)
    } catch {
        $ready = $false
    } finally {
        $ErrorActionPreference = $prev
    }
    return $ready
}

function Resolve-Compose {
    $prev = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        docker compose version *> $null
        if ($LASTEXITCODE -eq 0) { $script:ComposePlugin = $true; return $true }
    } catch {
    } finally {
        $ErrorActionPreference = $prev
    }
    if (Get-Command docker-compose -ErrorAction SilentlyContinue) {
        $script:ComposePlugin = $false; return $true
    }
    return $false
}

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)
    if ($script:ComposePlugin) { & docker compose @ComposeArgs }
    else { & docker-compose @ComposeArgs }
}

function Wait-Bridge([int]$BridgePort, [int]$TimeoutSec = 60) {
    $url = "http://127.0.0.1:$BridgePort/health"
    for ($i = 0; $i -lt $TimeoutSec; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Get-BridgeProfileDir {
    # Perfil efectivo que usaran 'core_bridge.cli start' y el servidor:
    # PPLX_USER_DATA_DIR si esta definido; si no, el perfil aislado por
    # navegador (p. ej. %LOCALAPPDATA%\PplxProfile\.profile_brave). No se
    # fija ninguna ruta: la resuelve core_bridge.browser.USER_DATA_DIR.
    if (-not $script:PyExe) { return $null }
    $prev = $ErrorActionPreference
    $dir = $null
    try {
        $ErrorActionPreference = "Continue"
        $raw = (& $script:PyExe @($script:PyPre + @(
            "-c",
            "from core_bridge.browser import USER_DATA_DIR; print(USER_DATA_DIR)"
        )) 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $raw) { $dir = $raw }
    } catch {
        $dir = $null
    } finally {
        $ErrorActionPreference = $prev
    }
    return $dir
}

function Invoke-Login {
    # Login guiado en el perfil efectivo del bridge. No impone rutas fijas:
    # 'python -m core_bridge.cli login' resuelve PPLX_USER_DATA_DIR o el
    # perfil aislado por navegador y escribe alli el marcador de sesion.
    $profileDir = Get-BridgeProfileDir
    if ($profileDir) {
        $marker = Join-Path $profileDir ".pplx_login_ok"
        if ((Test-Path -LiteralPath $marker) -and (-not $ForceLogin)) {
            Write-Ok "Sesion de Perplexity ya inicializada ($marker)."
            return $true
        }
    }
    if (-not (Test-PythonDeps)) {
        Write-Warn2 "No hay Python+Playwright locales para abrir la ventana de login."
        Write-Host "        Ejecuta primero: .\setup.ps1 -Mode local (o instala deps y repite)."
        return $false
    }
    if ($profileDir) {
        Write-Step "Login guiado (una sola vez) - perfil: $profileDir"
    } else {
        Write-Step "Login guiado (una sola vez) - perfil: por defecto de core_bridge"
    }
    if ($ForceLogin) {
        Invoke-Py "-m" "core_bridge.cli" "login" "--force"
    } else {
        Invoke-Py "-m" "core_bridge.cli" "login"
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "Login no confirmado. Puedes repetirlo con: python -m core_bridge.cli login"
        return $false
    }
    return $true
}

function Install-LocalDeps {
    Write-Step "Verificando Python 3.10+"
    if (-not (Find-Python)) {
        Write-Err2 "No se encontro Python. Instala Python 3.10+ desde https://www.python.org/downloads/"
        exit 1
    }
    $ver = Get-PythonVersion
    if (-not $ver -or ($ver.Major -lt 3) -or ($ver.Major -eq 3 -and $ver.Minor -lt 10)) {
        Write-Err2 "Se requiere Python 3.10+ (encontrado: $($ver.Raw))."
        exit 1
    }
    Write-Ok "Python detectado: $($ver.Raw)"

    if (-not (Test-PythonDeps)) {
        Write-Step "Instalando dependencias (requirements.txt)"
        Invoke-Py "-m" "pip" "install" "-r" "requirements.txt"
        if ($LASTEXITCODE -ne 0) { Write-Err2 "pip install fallo."; exit 1 }
        Write-Step "Instalando navegador Chromium de Playwright"
        Invoke-Py "-m" "playwright" "install" "chromium"
        if ($LASTEXITCODE -ne 0) { Write-Warn2 "playwright install fallo; el bridge puede no arrancar." }
    } else {
        Write-Ok "Dependencias Python listas (fastapi, uvicorn, pydantic, httpx, playwright)."
    }
}

function Show-LocalNextSteps([string]$UserDataDir) {
    Write-Host ""
    Write-Host "Listo. Siguientes pasos (el bridge se arranca solo si hace falta):" -ForegroundColor Green
    Write-Host "  1. Auditar:  .\pplx-audit.bat --files src/engine/trie.py --prompt `"PUNTAJE: [X]/100 ...`""
    Write-Host "  2. API:      python -m core_bridge.cli start   (http://127.0.0.1:8000/health)"
    Write-Host "  3. Perfil:   $UserDataDir"
}

function Add-DockerEnvFile {
    if (-not (Test-Path -LiteralPath ".env")) {
        Copy-Item -LiteralPath ".env.example" -Destination ".env"
        Write-Ok "Creado .env con variables base (PPLX_PORT, PPLX_TIMEOUT_S)."
    }
    $lines = @(Get-Content -LiteralPath ".env")
    $lines = @($lines | ForEach-Object {
        if ($_ -match "^\s*PPLX_PORT\s*=") { "PPLX_PORT=$Port" } else { $_ }
    })
    if (-not ($lines | Where-Object { $_ -match "^\s*PPLX_PORT\s*=" })) {
        $lines += "PPLX_PORT=$Port"
    }
    Set-Content -LiteralPath ".env" -Value $lines
}

# ---------------------------------------------------------------------------

Write-Host "Pplx Bridge - setup" -ForegroundColor White
Write-Host "Modo solicitado: $Mode"

if ($Mode -eq "auto") {
    if (Test-DockerReady) { $Mode = "docker"; Write-Ok "Docker disponible: modo docker." }
    else { $Mode = "local"; Write-Ok "Docker no disponible: modo local." }
}

if ($Mode -eq "docker") {
    Write-Step "Comprobando Docker"
    if (-not (Test-DockerReady)) {
        Write-Warn2 "Docker no esta instalado o el daemon no responde; se continua en modo local."
        $Mode = "local"
    } elseif (-not (Resolve-Compose)) {
        Write-Warn2 "No se encontro 'docker compose' ni 'docker-compose'; se continua en modo local."
        $Mode = "local"
    } else {
        Write-Ok "Docker listo."

        New-Item -ItemType Directory -Path "profile" -Force | Out-Null
        Add-DockerEnvFile

        # El contenedor monta ./profile como PPLX_USER_DATA_DIR (/data/profile):
        # el login del host debe escribir en ese mismo perfil. Se exporta solo
        # durante el login y se restaura el valor previo del entorno.
        Find-Python | Out-Null
        $savedUserDataDir = $env:PPLX_USER_DATA_DIR
        $env:PPLX_USER_DATA_DIR = (Join-Path $Root "profile")
        try {
            if (-not (Invoke-Login)) {
                Write-Warn2 "El contenedor arrancara igualmente; sin login las consultas a Perplexity fallaran."
            }
        } finally {
            if ($null -ne $savedUserDataDir) { $env:PPLX_USER_DATA_DIR = $savedUserDataDir }
            else { Remove-Item Env:\PPLX_USER_DATA_DIR -ErrorAction SilentlyContinue }
        }

        Write-Step "Levantando contenedor (puerto $Port)"
        if ($NoBuild) { Invoke-Compose "up" "-d" } else { Invoke-Compose "up" "-d" "--build" }
        if ($LASTEXITCODE -ne 0) { Write-Err2 "docker compose up fallo."; exit 1 }

        if (Wait-Bridge -BridgePort $Port -TimeoutSec 90) {
            Write-Ok "Bridge respondiendo en http://127.0.0.1:$Port/health"
        } else {
            Write-Warn2 "El bridge aun no responde. Revisa logs: docker compose logs -f"
        }
        Write-Host ""
        Write-Host "Siguientes pasos:" -ForegroundColor Green
        Write-Host "  1. Auditar:  .\pplx-audit.bat --files src/engine/trie.py --prompt `"PUNTAJE: [X]/100 ...`""
        Write-Host "  2. Logs:     docker compose logs -f"
        Write-Host "  3. Parar:    docker compose down"
        exit 0
    }
}

# Modo local
Write-Step "Modo local"
Install-LocalDeps
if (-not (Invoke-Login)) {
    Write-Warn2 "Sin login el bridge arrancara pero Perplexity pedira autenticacion."
}
$localProfile = Get-BridgeProfileDir
if (-not $localProfile) { $localProfile = "por defecto de core_bridge" }
$env:PPLX_PORT = "$Port"
Show-LocalNextSteps $localProfile
exit 0
