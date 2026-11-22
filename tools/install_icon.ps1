# install_icon.ps1 - Install Claude icon component (no admin rights needed)
# ============================================================================
# Installs the icon guard script + official icon into the user profile,
# registers per-user autostart, and starts the guard immediately. Keeps the
# official Claude icon consistent on taskbar / Start menu / desktop.
#
# Usage (normal PowerShell):
#   powershell -ExecutionPolicy Bypass -File tools\install_icon.ps1

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$SrcScript = Join-Path $PSScriptRoot "icon_guard.pyw"
$SrcIcon = Join-Path $Repo "assets\claude.ico"
$InstallDir = Join-Path $env:LOCALAPPDATA "ClaudeIconGuard"
$DstScript = Join-Path $InstallDir "icon_guard.pyw"
$DstIcon = Join-Path $InstallDir "claude.ico"
$RunName = "ClaudeIconGuard"

function Log($m) { Write-Host "[icon] $m" }

# 1. Locate pythonw.exe (runs the guard with no console window)
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $pyw) {
    $py = (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if ($py) { $pyw = Join-Path (Split-Path $py) "pythonw.exe" }
}
if (-not $pyw -or -not (Test-Path $pyw)) {
    throw "pythonw.exe not found. Install Python 3.10+ and add it to PATH."
}
Log "pythonw: $pyw"

# 2. Verify source files
foreach ($f in @($SrcScript, $SrcIcon)) {
    if (-not (Test-Path $f)) { throw "Missing file: $f" }
}

# 3. Install into user profile
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item $SrcScript $DstScript -Force
Copy-Item $SrcIcon $DstIcon -Force
Log "installed to $InstallDir"

# 4. Register per-user autostart (HKCU, no admin needed)
$cmd = '"' + $pyw + '" "' + $DstScript + '"'
New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
    -Name $RunName -Value $cmd -PropertyType String -Force | Out-Null
Log "autostart registered: $RunName"

# 5. Start the guard now (a second instance exits on its own via mutex)
$running = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*icon_guard.pyw*" }
if (-not $running) {
    Start-Process -FilePath $pyw -ArgumentList ('"' + $DstScript + '"') -WindowStyle Hidden
    Log "guard started"
} else {
    Log "guard already running, skip"
}

Log "DONE. Taskbar / Start menu / desktop show the official Claude icon."
