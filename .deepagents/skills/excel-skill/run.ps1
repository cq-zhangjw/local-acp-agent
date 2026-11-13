# Entry point for excel-skill: ensures .venv exists, forces UTF-8, then forwards all args to excel_cli.py.
# No param() block on purpose so tokens like --file-path land in $args untouched.
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$skillDir = $PSScriptRoot
$venvDir = Join-Path $skillDir '.venv'
$venvPy = Join-Path $venvDir 'Scripts\python.exe'

if (-not (Test-Path $venvPy)) {
    python -m venv $venvDir | Out-Null
    & $venvPy -m pip install -q --disable-pip-version-check -r (Join-Path $skillDir 'requirements.txt') | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Output '{"status": "error", "message": "failed to install requirements into .venv"}'
        exit 1
    }
}

& $venvPy (Join-Path $skillDir 'excel_cli.py') @args
exit $LASTEXITCODE
