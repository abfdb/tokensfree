<#
.SYNOPSIS
  User-level installer for the tokensfree desktop shell (no admin required).

.DESCRIPTION
  Creates Start Menu / Desktop / (optional) Startup shortcuts that launch
  pythonw -m tokensfree.desktop from this repository. Nothing is copied and no
  system-wide state is touched; uninstall.ps1 removes the shortcuts.
#>
[CmdletBinding()]
param(
  [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
  [string]$StartMenuDir = [Environment]::GetFolderPath('Programs'),
  [string]$DesktopDir = [Environment]::GetFolderPath('Desktop'),
  [string]$StartupDir = (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'),
  [string]$DataDir = (Join-Path $env:APPDATA 'tokensfree'),
  [switch]$Autostart,
  [switch]$NoDesktopShortcut
)

$ErrorActionPreference = 'Stop'

function Resolve-Pythonw {
  param([string]$Root)
  $venv = Join-Path $Root '.venv\Scripts\pythonw.exe'
  if (Test-Path -LiteralPath $venv) { return $venv }
  $candidate = Get-Command pythonw.exe -ErrorAction SilentlyContinue
  if ($candidate) { return $candidate.Source }
  throw 'pythonw.exe not found. Create the venv first: python -m venv .venv'
}

function New-Shortcut {
  param([string]$Path, [string]$Target, [string]$Arguments, [string]$WorkingDirectory)
  $shell = New-Object -ComObject WScript.Shell
  $link = $shell.CreateShortcut($Path)
  $link.TargetPath = $Target
  $link.Arguments = $Arguments
  $link.WorkingDirectory = $WorkingDirectory
  $link.Description = 'tokensfree local gateway console'
  $link.Save()
}

$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $resolvedRepo 'tokensfree'))) {
  throw "RepoRoot does not look like the tokensfree repository: $resolvedRepo"
}
$pythonw = Resolve-Pythonw -Root $resolvedRepo
$arguments = '-m tokensfree.desktop --start-gateway'

New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
$startMenuShortcut = Join-Path $StartMenuDir 'tokensfree 控制台.lnk'
New-Shortcut -Path $startMenuShortcut -Target $pythonw -Arguments $arguments -WorkingDirectory $resolvedRepo
Write-Host "start menu: $startMenuShortcut"

if (-not $NoDesktopShortcut) {
  $desktopShortcut = Join-Path $DesktopDir 'tokensfree 控制台.lnk'
  New-Shortcut -Path $desktopShortcut -Target $pythonw -Arguments $arguments -WorkingDirectory $resolvedRepo
  Write-Host "desktop: $desktopShortcut"
}

if ($Autostart) {
  New-Item -ItemType Directory -Force -Path $StartupDir | Out-Null
  $startupShortcut = Join-Path $StartupDir 'tokensfree 控制台.lnk'
  New-Shortcut -Path $startupShortcut -Target $pythonw -Arguments $arguments -WorkingDirectory $resolvedRepo
  Write-Host "autostart: $startupShortcut"
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Write-Host "data dir: $DataDir"
Write-Host 'tokensfree desktop installed. Remove it with install\uninstall.ps1'
