<#
.SYNOPSIS
  Remove the tokensfree desktop shortcuts (and optionally the local data dir).

.DESCRIPTION
  Deletes the Start Menu / Desktop / Startup shortcuts created by install.ps1.
  Never touches the repository. Use -RemoveData to also delete %APPDATA%\tokensfree
  (desktop config + SQLite state).
#>
[CmdletBinding()]
param(
  [string]$StartMenuDir = [Environment]::GetFolderPath('Programs'),
  [string]$DesktopDir = [Environment]::GetFolderPath('Desktop'),
  [string]$StartupDir = (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'),
  [string]$DataDir = (Join-Path $env:APPDATA 'tokensfree'),
  [switch]$RemoveData
)

$ErrorActionPreference = 'Stop'
$targets = @(
  (Join-Path $StartMenuDir 'tokensfree 控制台.lnk'),
  (Join-Path $DesktopDir 'tokensfree 控制台.lnk'),
  (Join-Path $StartupDir 'tokensfree 控制台.lnk')
)
foreach ($target in $targets) {
  if (Test-Path -LiteralPath $target) {
    Remove-Item -LiteralPath $target -Force
    Write-Host "removed: $target"
  }
}
if ($RemoveData) {
  if (Test-Path -LiteralPath $DataDir) {
    $resolved = (Resolve-Path -LiteralPath $DataDir).Path
    if ([string]::IsNullOrWhiteSpace($resolved) -or $resolved -eq [System.IO.Path]::GetPathRoot($resolved)) {
      Write-Warning "refusing to remove a drive root: $resolved"
    } else {
      Remove-Item -LiteralPath $resolved -Recurse -Force
      Write-Host "removed data: $resolved"
    }
  }
}
Write-Host 'tokensfree desktop uninstalled'
