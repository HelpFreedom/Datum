[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('\A[^"%\\\s\x00-\x1F]+\z')]
    [string] $Distribution,

    [Parameter(Mandatory)]
    [ValidatePattern('\A/[^"%\\\x00-\x1F]*\z')]
    [string] $Checkout,

    [switch] $Launch
)

$ErrorActionPreference = 'Stop'
$wsl = (Get-Command wsl.exe -CommandType Application | Select-Object -First 1).Source
if ($Launch) {
    try {
        $start = New-Object System.Diagnostics.ProcessStartInfo
        $start.FileName = $wsl
        # WSL разбирает имя дистрибутива без кавычек, а путь может содержать пробелы.
        $start.Arguments = '--distribution {0} --cd "{1}" --exec /usr/bin/env DATUM_DESKTOP_LAUNCH=1 ./scripts/datum' -f $Distribution, $Checkout
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardInput = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $process = [Diagnostics.Process]::Start($start)
        try {
            $process.StandardInput.Close()
            $stdout = $process.StandardOutput.ReadToEndAsync()
            $stderr = $process.StandardError.ReadToEndAsync()
            if (-not $process.WaitForExit(30000)) {
                $process.Kill()
                throw 'WSL не ответила за 30 секунд.'
            }
            if ($process.ExitCode -ne 0) {
                $details = ($stderr.Result + [Environment]::NewLine + $stdout.Result).Trim()
                if ($details.Contains([char] 0)) { $details = '' }
                throw "WSL завершилась с кодом $($process.ExitCode).`n$details"
            }
        } finally {
            $process.Dispose()
        }
    } catch {
        $shell = New-Object -ComObject WScript.Shell
        $message = "Datum не запустился.`n$($_.Exception.Message)`nЖурнал окна в Linux: ~/.local/state/datum/window.log"
        $shell.Popup($message, 10, 'Datum', 16) | Out-Null
        exit 1
    }
    return
}

$wslArguments = @('--distribution', $Distribution, '--cd', $Checkout, '--exec')
& $wsl @wslArguments /usr/bin/test -x ./scripts/datum
if ($LASTEXITCODE -ne 0) {
    throw "Не найден исполняемый scripts/datum в $Checkout (WSL: $Distribution)."
}

$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
if (-not $desktop -or -not (Test-Path -LiteralPath $desktop -PathType Container)) {
    throw 'Папка рабочего стола Windows недоступна.'
}

$launcherSource = Join-Path $PSScriptRoot 'start-wsl.wsf'
if (-not (Test-Path -LiteralPath $launcherSource -PathType Leaf)) {
    throw "Не найден сценарий запуска Datum: $launcherSource"
}
$localData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$launcherDirectory = Join-Path $localData 'Datum'
New-Item -ItemType Directory -Path $launcherDirectory -Force | Out-Null
$launcher = Join-Path $launcherDirectory 'start-wsl.wsf'
Copy-Item -LiteralPath $launcherSource -Destination $launcher
Copy-Item -LiteralPath $PSCommandPath -Destination $launcherDirectory

$path = Join-Path $desktop 'Datum.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($path)
$shortcut.TargetPath = (Get-Command wscript.exe -CommandType Application | Select-Object -First 1).Source
$shortcut.Arguments = '"{0}" "{1}" "{2}"' -f $launcher, $Distribution, $Checkout
$shortcut.Description = 'Открыть Datum через WSL 2'
$shortcut.WorkingDirectory = $launcherDirectory
$shortcut.WindowStyle = 1
$shortcut.Save()
Write-Output $path
