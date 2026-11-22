param(
    [string]$Python = "python",
    [switch]$SkipDeps
)

$ErrorActionPreference = "Stop"
$ServerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ServerDir

Write-Host "=============================================="
Write-Host " DeepSeek Chat Server - installation"
Write-Host "=============================================="

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    throw "Python not found. Install Python 3.12 x64 and add it to PATH, or pass -Python 'C:\Path\python.exe'."
}

$versionText = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
$version = [version]$versionText
if ($version -lt [version]"3.10") {
    throw "Python 3.10+ is required (found $versionText). Python 3.12 is recommended."
}
Write-Host "[OK] Python $versionText"

$VenvDir = Join-Path $ServerDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "[..] Creating virtual environment: $VenvDir"
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment." }
} else {
    Write-Host "[OK] Virtual environment already exists"
}

if (-not $SkipDeps) {
    Write-Host "[..] Installing dependencies (requirements.txt)"
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r (Join-Path $ServerDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Check network or use a PyPI mirror." }
    Write-Host "[OK] Dependencies installed"
}

if (-not (Test-Path (Join-Path $ServerDir ".env"))) {
    Copy-Item (Join-Path $ServerDir ".env.example") (Join-Path $ServerDir ".env")
    Write-Host "[..] Created .env from .env.example"
    Write-Host "     IMPORTANT: edit .env and change ADMIN_PASSWORD before the first start."
} else {
    Write-Host "[OK] .env already exists (kept unchanged)"
}

$DataDir = Join-Path $ServerDir "data"
if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir | Out-Null
    Write-Host "[OK] Created data directory: $DataDir"
}

Write-Host ""
Write-Host "Installation finished. Next steps:"
Write-Host "  1. Edit .env (ADMIN_PASSWORD, COOKIE_SECURE, HOST/PORT)"
Write-Host "  2. Run start.bat"
Write-Host "  3. Open http://server-ip:8000 and log in as the admin"
Write-Host "  4. Configure the DeepSeek API Key and create users in the admin console"
