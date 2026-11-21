# deploy.ps1 — Claude Desktop 汉化部署脚本（需管理员权限运行）
# ==============================================================
# 用法（管理员 PowerShell）:
#   powershell -ExecutionPolicy Bypass -File deploy.ps1 `
#     -AppDir "C:\Program Files\WindowsApps\Claude_6.28899.2.0_x64__pzs8sxrjxfjjc\app" `
#     -IonAsar "ion-dist-new.asar" -AppAsar "app-patched.asar" `
#     -IonHash "<ion-dist 新 header 哈希>" -AppHash "<app 新 header 哈希>"
#
# 做四件事:
#   1. 结束 Claude 进程
#   2. takeown + icacls 取得安装目录文件写权限（MSIX 包默认只有 SYSTEM/TrustedInstaller 可写）
#   3. 覆盖 ion-dist.asar / app.asar
#   4. 定点改写 Claude.exe 内两个 asar 的完整性哈希标记（64 位十六进制，等长替换）
#   5. 三处 config.json 写 locale=zh-Hans（双保险，主力是 app.asar 里的代码补丁）

param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [Parameter(Mandatory = $true)][string]$IonAsar,
    [Parameter(Mandatory = $true)][string]$AppAsar,
    [string]$IonHash = "",
    [string]$AppHash = "",
    [string]$ExeName = "Claude.exe"
)

$ErrorActionPreference = "Stop"
$Res = Join-Path $AppDir "resources"
$Exe = Join-Path $AppDir $ExeName

function Log($m) { Write-Host "[deploy] $m" }

# 1. 停进程
$cp = Get-Process Claude -ErrorAction SilentlyContinue
if ($cp) { $cp | Stop-Process -Force; Start-Sleep -Seconds 2 }

# 2. 提权文件权限
foreach ($f in @("$Res\app.asar", "$Res\ion-dist.asar", $Exe)) {
    if (Test-Path $f) {
        & takeown /F $f /A | Out-Null
        & icacls $f /grant Administrators:F | Out-Null
    }
}

# 3. 覆盖 asar
Log "replacing ion-dist.asar ..."
Copy-Item $IonAsar "$Res\ion-dist.asar" -Force
Log "replacing app.asar ..."
Copy-Item $AppAsar "$Res\app.asar" -Force

# 4. 同步 exe 哈希标记（ASCII 字符串定位，字节偏移=字符偏移）
function Set-ExeAsarHash([string]$AsarName, [string]$NewHash) {
    if (-not $NewHash) { Log "$AsarName 未提供新哈希，跳过"; return }
    $text = [System.IO.File]::ReadAllText($Exe, [System.Text.Encoding]::ASCII)
    $marker = '{"file":"resources\\' + $AsarName + '","alg":"SHA256","value":"'
    $i = $text.IndexOf($marker, [System.StringComparison]::Ordinal)
    if ($i -lt 0) { throw "exe 中找不到 $AsarName 的完整性标记" }
    $off = $i + $marker.Length
    $cur = $text.Substring($off, 64)
    Log "$AsarName 当前哈希: $cur"
    if ($cur -eq $NewHash) { Log "$AsarName 已是新值"; return }
    if ($cur -notmatch '^[0-9a-fA-F]{64}$') { throw "$AsarName 标记内容异常: $cur" }
    $fs = [System.IO.File]::Open($Exe, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite)
    try {
        $fs.Seek($off, [System.IO.SeekOrigin]::Begin) | Out-Null
        $fs.Write([System.Text.Encoding]::ASCII.GetBytes($NewHash), 0, 64)
    } finally { $fs.Close() }
    Log "$AsarName 哈希已更新 -> $NewHash"
}
Set-ExeAsarHash "ion-dist.asar" $IonHash
Set-ExeAsarHash "app.asar" $AppHash

# 5. config locale（三处都写，真实生效的是补丁代码，这是双保险）
$cfgs = @("$env:LOCALAPPDATA\Claude-3p\config.json",
          "$env:APPDATA\Claude-3p\config.json",
          "$env:APPDATA\Claude\config.json")
foreach ($c in $cfgs) {
    if (Test-Path $c) {
        $j = Get-Content $c -Raw -Encoding UTF8 | ConvertFrom-Json
        $j | Add-Member -NotePropertyName locale -NotePropertyValue "zh-Hans" -Force
        ($j | ConvertTo-Json -Depth 20) | Set-Content $c -Encoding UTF8
        Log "config set: $c"
    }
}
Log "DONE. 现在可以启动 Claude 验证。"
