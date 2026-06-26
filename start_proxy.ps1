$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $ScriptDir "proxy_server.py"

$PythonCandidates = @(
    "python",
    "py"
)

$Python = $null
foreach ($candidate in $PythonCandidates) {
    try {
        Get-Command $candidate -ErrorAction Stop | Out-Null
        $Python = $candidate
        break
    } catch {
    }
}

if (-not $Python) {
    throw "Python runtime not found. Install Python 3.10+ and ensure 'python' or 'py' is available in PATH."
}

Start-Process -FilePath $Python -ArgumentList @($Script) -WorkingDirectory $ScriptDir -WindowStyle Hidden
Write-Output "aria2-share-proxy started"
