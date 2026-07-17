# One-shot setup for Windows (PowerShell). Installs the toolchain (Rust, uv,
# Node + pnpm) via winget, then all project dependencies. Re-runnable.
#
#   Run in PowerShell:
#     irm https://raw.githubusercontent.com/SrinivasRavi/finds-you-jobs/main/scripts/setup.ps1 | iex
#   or, if you already cloned the repo:
#     powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Have($c) { $null -ne (Get-Command $c -ErrorAction SilentlyContinue) }

Step "Checking git"
if (-not (Have git)) { winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements }

Step "Getting the code"
if ((Test-Path package.json) -and (Select-String -Path package.json -Pattern '"name": "finds-you-jobs"' -Quiet)) {
  git pull --ff-only
} elseif (Test-Path finds-you-jobs\.git) {
  Set-Location finds-you-jobs; git pull --ff-only
} else {
  git clone https://github.com/SrinivasRavi/finds-you-jobs.git; Set-Location finds-you-jobs
}

Step "Rust toolchain (for the desktop shell)"
if (-not (Have cargo)) { winget install --id Rustlang.Rustup -e --accept-package-agreements --accept-source-agreements }

Step "uv (Python package manager)"
if (-not (Have uv)) { powershell -c "irm https://astral.sh/uv/install.ps1 | iex" }

Step "Node + pnpm (for the UI)"
if (-not (Have node)) { winget install --id OpenJS.NodeJS.LTS -e --accept-package-agreements --accept-source-agreements }
if (-not (Have pnpm)) { corepack enable pnpm }

Step "Installing project dependencies (this pulls everything)"
# Refresh PATH so freshly-installed tools are visible in this session.
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
pnpm run boot

# The script's Set-Location doesn't persist into the user's shell when piped
# via `irm | iex` from elsewhere — print the full path so the command works.
Write-Host "`nDone. Start the app with:" -ForegroundColor Green
Write-Host "    cd `"$((Get-Location).Path)`"; pnpm dev"
