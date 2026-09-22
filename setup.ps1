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
    Invoke-Py "-c" "import fastapi, uvicorn, pydantic, httpx, playwright" *> $null
    return ($LASTEXITCODE -eq 0)
}

function Test-DockerReady {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    docker info *> $null
    return ($LASTEXITCODE -eq 0)
}

function Resolve-Compose {
    docker compose version *> $null
    if ($LASTEXITCODE -eq 0) { $script:ComposePlugin = $true; return $true }
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

function Invoke-Login([string]$UserDataDir) {
    $marker = Join-Path $UserDataDir ".pplx_login_ok"
    if ((Test-Path -LiteralPath $marker) -and (-not $ForceLogin)) {
        Write-Ok "Sesion de Perplexity ya inicializada ($marker)."
        return $true
    }
    if (-not (Test-PythonDeps)) {
        Write-Warn2 "No hay Python+Playwright locales para abrir la ventana de login."
        Write-Host "        Ejecuta primero: .\setup.ps1 -Mode local (o instala deps y repite)."
        return $false
    }
    Write-Step "Login guiado (una sola vez) - perfil: $UserDataDir"
    Invoke-Py "-m" "core_bridge.cli" "login" "--user-data-dir" $UserDataDir
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "Login no confirmado. Puedes repetirlo con: python -m core_bridge.cli login --user-data-dir `"$UserDataDir`""
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
        Write-Err2 "Docker no esta instalado o el daemon no responde. Arranca Docker Desktop y repite."
        exit 1
    }
    if (-not (Resolve-Compose)) {
        Write-Err2 "No se encontro 'docker compose' ni 'docker-compose'."
        exit 1
    }
    Write-Ok "Docker listo."

    New-Item -ItemType Directory -Path "profile" -Force | Out-Null
    Add-DockerEnvFile

    $profilePath = Join-Path $Root "profile"
    if (-not (Invoke-Login $profilePath)) {
        Write-Warn2 "El contenedor arrancara igualmente; sin login las consultas a Perplexity fallaran."
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

# Modo local
Install-LocalDeps
$localProfile = Join-Path $Root "tools\pplx_bridge\.profile"
if (-not (Invoke-Login $localProfile)) {
    Write-Warn2 "Sin login el bridge arrancara pero Perplexity pedira autenticacion."
}
$env:PPLX_PORT = "$Port"
Show-LocalNextSteps $localProfile
exit 0
