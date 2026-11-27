[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('\A[^"%\\\x00-\x1F]+\z')]
    [string] $Distribution,

    [Parameter(Mandatory)]
    [ValidatePattern('\A/[^"%\\\x00-\x1F]*\z')]
    [string] $Checkout
)

$ErrorActionPreference = 'Stop'
$wsl = (Get-Command wsl.exe -CommandType Application | Select-Object -First 1).Source
$wslArguments = @('--distribution', $Distribution, '--cd', $Checkout, '--exec')
& $wsl @wslArguments /usr/bin/test -x ./scripts/datum
if ($LASTEXITCODE -ne 0) {
    throw "Не найден исполняемый scripts/datum в $Checkout (WSL: $Distribution)."
}

$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
if (-not $desktop -or -not (Test-Path -LiteralPath $desktop -PathType Container)) {
    throw 'Папка рабочего стола Windows недоступна.'
}

$path = Join-Path $desktop 'Datum.lnk'
$launcher = Join-Path $PSScriptRoot 'start-wsl.wsf'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Не найден сценарий запуска Datum: $launcher"
}
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($path)
$shortcut.TargetPath = (Get-Command wscript.exe -CommandType Application | Select-Object -First 1).Source
$shortcut.Arguments = '"{0}" "{1}" "{2}" "{3}"' -f $launcher, $wsl, $Distribution, $Checkout
$shortcut.Description = 'Открыть Datum через WSL 2'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.WindowStyle = 1
$shortcut.Save()
Write-Output $path
