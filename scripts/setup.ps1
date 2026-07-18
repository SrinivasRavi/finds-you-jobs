# One-shot setup for Windows 10/11 (PowerShell — works on the built-in
# "Windows PowerShell" 5.1; no `&&` or other PowerShell-7-only syntax).
# Installs git, the Microsoft C++ Build Tools + WebView2 (the desktop shell
# needs both), Rust, uv, Node + pnpm, then all project dependencies including
# the local Chromium the app's browser features use. Safe to re-run.
#
#   Run in PowerShell:
#     irm https://raw.githubusercontent.com/SrinivasRavi/finds-you-jobs/main/scripts/setup.ps1 | iex
#   or, if you already cloned the repo:
#     powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Have($c) { $null -ne (Get-Command $c -ErrorAction SilentlyContinue) }
function RefreshPath {
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
              [System.Environment]::GetEnvironmentVariable("Path", "User")
}

if (-not (Have winget)) {
  Write-Host "winget (App Installer) is required. Install 'App Installer' from the Microsoft Store, then re-run." -ForegroundColor Yellow
  exit 1
}

Step "PowerShell script policy"
# Windows ships with policy 'Restricted', which blocks the uv installer AND
# the pnpm/npm command shims (they are .ps1 files) — observed on a real
# install 2026-07-18. RemoteSigned (Microsoft's own suggestion in that error)
# lets local scripts run; set it for this user only, and only when needed.
$policy = Get-ExecutionPolicy
if ($policy -eq "Restricted" -or $policy -eq "AllSigned" -or $policy -eq "Undefined") {
  try {
    Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
    Write-Host "Set your user's execution policy to RemoteSigned (local scripts can run)."
  } catch {
    Write-Host "Could not set the execution policy (it may be locked by your organization). If later steps fail with 'running scripts is disabled', run:" -ForegroundColor Yellow
    Write-Host "    Set-ExecutionPolicy RemoteSigned -Scope CurrentUser"
  }
} else {
  Write-Host "Execution policy is $policy — fine as is."
}

Step "git"
if (-not (Have git)) {
  winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
  RefreshPath
}
git --version

Step "Getting the code"
if ((Test-Path package.json) -and (Select-String -Path package.json -Pattern '"name": "finds-you-jobs"' -Quiet)) {
  git pull --ff-only
} elseif (Test-Path finds-you-jobs\.git) {
  Set-Location finds-you-jobs
  git pull --ff-only
} else {
  # `irm | iex` runs from wherever the shell happens to sit — an elevated
  # PowerShell starts in C:\WINDOWS\System32, where a clone fails with
  # "Permission denied" (observed 2026-07-18). Clone somewhere sane: keep the
  # current directory only when it is writable and not a system path.
  $cwd = (Get-Location).Path
  $isSystemPath = $false
  foreach ($root in @($env:WINDIR, $env:ProgramFiles)) {
    if (-not [string]::IsNullOrEmpty($root) -and
        $cwd.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
      $isSystemPath = $true
    }
  }
  $canWrite = $true
  try {
    $probe = Join-Path $cwd ([System.IO.Path]::GetRandomFileName())
    New-Item -ItemType File -Path $probe -ErrorAction Stop | Out-Null
    Remove-Item $probe -ErrorAction SilentlyContinue
  } catch { $canWrite = $false }
  if ($isSystemPath -or -not $canWrite) {
    Write-Host "Current directory ($cwd) is not a good home for the code — using $HOME instead." -ForegroundColor Yellow
    Set-Location $HOME
  }
  if (Test-Path finds-you-jobs\.git) {
    Set-Location finds-you-jobs
    git pull --ff-only
  } else {
    git clone https://github.com/SrinivasRavi/finds-you-jobs.git
    # PowerShell 5.1 does not stop on a native command's exit code even with
    # ErrorActionPreference=Stop — check explicitly so a failed clone doesn't
    # cascade into confusing Set-Location errors.
    if ($LASTEXITCODE -ne 0) {
      Write-Host "git clone failed (see the error above). Fix that and re-run this script." -ForegroundColor Red
      exit 1
    }
    Set-Location finds-you-jobs
  }
}

Step "Microsoft C++ Build Tools (the desktop shell is compiled with these — a big one-time download)"
$vsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$hasVc = $false
if (Test-Path $vsWhere) {
  $found = & $vsWhere -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
  if ($found) { $hasVc = $true }
}
if (-not $hasVc) {
  winget install --id Microsoft.VisualStudio.2022.BuildTools -e --accept-package-agreements --accept-source-agreements --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
}

Step "WebView2 runtime (the app's window engine; usually already present on Windows 11)"
$wv = "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
if (-not (Test-Path $wv)) {
  winget install --id Microsoft.EdgeWebView2Runtime -e --accept-package-agreements --accept-source-agreements
}

Step "Rust toolchain"
if (-not (Have cargo)) {
  winget install --id Rustlang.Rustup -e --accept-package-agreements --accept-source-agreements
  RefreshPath
}
cargo --version

Step "uv (Python package manager)"
if (-not (Have uv)) {
  # -ExecutionPolicy Bypass: the child shell would otherwise inherit the
  # machine default and the uv installer refuses to run under 'Restricted'.
  powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
  RefreshPath
}
uv --version

Step "Node + pnpm (for the UI)"
if (-not (Have node)) {
  winget install --id OpenJS.NodeJS.LTS -e --accept-package-agreements --accept-source-agreements
  RefreshPath
}
if (-not (Have pnpm)) {
  corepack enable pnpm
  RefreshPath
}
if (-not (Have pnpm)) {
  # corepack writes shims into Node's install dir (Program Files) and fails
  # without admin rights; npm's global dir is per-user, so this always works.
  npm install -g pnpm
  RefreshPath
}
node --version
pnpm --version

Step "Installing project dependencies"
pnpm run boot

Step "Downloading the app's local Chromium (one-time, ~150 MB)"
uv run playwright install chromium

# The script's Set-Location doesn't persist into the user's shell when piped
# via `irm | iex` — print the full path so the next command actually works.
Write-Host "`nDone. Start the app with:" -ForegroundColor Green
Write-Host ("    cd `"" + (Get-Location).Path + "`"")
Write-Host "    pnpm dev"
Write-Host "`nIf 'pnpm' or 'cargo' is not recognized, close this window, open a NEW PowerShell, and run the two commands above again." -ForegroundColor Yellow
Write-Host "If an installer above said 'Restart your PC to finish installation' (the C++ Build Tools often do), restart before the first 'pnpm dev'." -ForegroundColor Yellow
