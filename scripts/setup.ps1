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
if (-not (Have git)) {
  Write-Host "`ngit is still not on PATH after installing it. Close this window, open a NEW PowerShell, and re-run the install command." -ForegroundColor Red
  exit 1
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
# rustup's default host toolchain on ARM64 Windows is aarch64-pc-windows-msvc,
# which needs the ARM64 MSVC toolset — a DIFFERENT component from the classic
# x86/x64 one. Missing it produces "linker `link.exe` not found" at the first
# `cargo run`, even after this step reports Build Tools present — the old
# check only ever asked vswhere for the x86.x64 component, so on a machine
# that already had that one (from an earlier attempt) it kept saying "done"
# forever while cargo stayed broken (observed live, 2026-07-20/21). Require
# whichever component this machine's real host architecture needs.
# PROCESSOR_ARCHITEW6432 (only set when this PowerShell itself is running
# under x64 emulation) carries the TRUE OS arch in that case; otherwise
# PROCESSOR_ARCHITECTURE already is the true arch.
$hostArch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$vcComponent = if ($hostArch -eq "ARM64") {
  "Microsoft.VisualStudio.Component.VC.Tools.ARM64"
} else {
  "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
}
$vsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$hasVc = $false
if (Test-Path $vsWhere) {
  $found = & $vsWhere -products '*' -requires $vcComponent -property installationPath
  if ($found) { $hasVc = $true }
}
if (-not $hasVc) {
  # --source winget (not the default multi-source search): winget's OTHER
  # source, msstore, needs a Microsoft Store account/region handshake and its
  # certificate check has been observed failing outright on a fresh Windows
  # VM (0x8a15005e, 2026-07-20) — a store-account problem with nothing to do
  # with us. Every winget call below pins the source for the same reason
  # (`git`, above, already did).
  # --force: without it, winget can skip re-invoking the installer entirely
  # when it considers Build Tools "already installed" (a earlier attempt put
  # it there) — but the VS bootstrapper itself correctly treats being run
  # again with a different --add as a MODIFY of the existing install, adding
  # just the missing component, not a reinstall of everything.
  winget install --id Microsoft.VisualStudio.2022.BuildTools -e --source winget --force --accept-package-agreements --accept-source-agreements --override "--wait --passive --add $vcComponent --includeRecommended"
}

Step "WebView2 runtime (the app's window engine; usually already present on Windows 11)"
$wv = "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
if (-not (Test-Path $wv)) {
  winget install --id Microsoft.EdgeWebView2Runtime -e --source winget --accept-package-agreements --accept-source-agreements
}

Step "Rust toolchain"
if (-not (Have cargo)) {
  winget install --id Rustlang.Rustup -e --source winget --accept-package-agreements --accept-source-agreements
  RefreshPath
}
if (-not (Have cargo)) {
  Write-Host "`ncargo is still not on PATH after installing Rust. Close this window, open a NEW PowerShell, and re-run the install command." -ForegroundColor Red
  exit 1
}
cargo --version

Step "uv (Python package manager)"
if (-not (Have uv)) {
  # -ExecutionPolicy Bypass: the child shell would otherwise inherit the
  # machine default and the uv installer refuses to run under 'Restricted'.
  powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
  RefreshPath
}
if (-not (Have uv)) {
  Write-Host "`nuv is still not on PATH after installing it. Close this window, open a NEW PowerShell, and re-run the install command." -ForegroundColor Red
  exit 1
}
uv --version

Step "Node + pnpm (for the UI)"
if (-not (Have node)) {
  winget install --id OpenJS.NodeJS.LTS -e --source winget --accept-package-agreements --accept-source-agreements
  RefreshPath
}
if (-not (Have node)) {
  Write-Host "`nnode is still not on PATH after installing it. Close this window, open a NEW PowerShell, and re-run the install command." -ForegroundColor Red
  exit 1
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
if (-not (Have pnpm)) {
  Write-Host "`npnpm is still not on PATH after installing it. Close this window, open a NEW PowerShell, and re-run the install command." -ForegroundColor Red
  exit 1
}
node --version
pnpm --version

Step "Installing project dependencies"
pnpm run boot

Step "Downloading the app's local Chromium (one-time, ~150 MB)"
uv run playwright install chromium

# Every real install so far (2026-07-18/19/20) ended here: a wall of setup
# output, two commands printed as "do this next", and — from a fresh
# terminal — no visible signal whether the reader actually ran them. Stop
# leaving that as homework: start the app for them, unless Windows itself
# has a restart pending (the standard reboot-pending registry markers —
# Windows Update and CBS servicing flags, and a nonempty
# PendingFileRenameOperations, the same three checks tools like sccm/psexec
# use). The C++ Build Tools installer is the one step in this script that can
# set these; a build against half-installed tools fails confusingly, so
# restart first in that case instead of launching into it.
function Test-PendingReboot {
  if (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired") { return $true }
  if (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending") { return $true }
  $pfro = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager" -Name PendingFileRenameOperations -ErrorAction SilentlyContinue
  return $null -ne $pfro
}

# The script's Set-Location doesn't persist into the user's shell when piped
# via `irm | iex` — print the full path so a later manual run still works.
$repoPath = (Get-Location).Path
if (Test-PendingReboot) {
  Write-Host "`nSetup finished — but Windows needs a restart first (an installer above requested it)." -ForegroundColor Yellow
  Write-Host "Restart your PC, then open PowerShell and run:"
  Write-Host ("    cd `"$repoPath`"")
  Write-Host "    pnpm dev"
} else {
  Write-Host "`nSetup finished. Starting the app now — first launch compiles the desktop shell (a few minutes, one-time only)." -ForegroundColor Green
  Write-Host "Keep THIS window open while the app runs (closing it or pressing Ctrl-C here quits the app). To start it again later:"
  Write-Host ("    cd `"$repoPath`"")
  Write-Host "    pnpm dev`n"
  if (-not (Have pnpm)) {
    Write-Host "'pnpm' isn't recognized in this window. Close it, open a NEW PowerShell, and run the two commands above." -ForegroundColor Yellow
  } else {
    pnpm dev
  }
}
