[CmdletBinding()]
param([string]$OutputDirectory = "backups")

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$finalPath = Join-Path $OutputDirectory "inryeok-bot-$stamp.sql"
$partialPath = "$finalPath.partial"

try {
    # Native stdout redirected with PowerShell's `>` is encoded as UTF-16 on
    # Windows PowerShell. A PostgreSQL plain SQL dump is a byte stream, so
    # copy the child process stream directly and never materialize it as text.
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = "docker"
    $startInfo.Arguments = "compose exec -T postgres pg_dump -U reviewbot reviewbot"
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "could not start pg_dump"
    }
    $output = [System.IO.File]::Open($partialPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $process.StandardOutput.BaseStream.CopyTo($output)
    }
    finally {
        $output.Dispose()
    }
    $errorText = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw "pg_dump failed: $errorText"
    }
    if ((Get-Item -LiteralPath $partialPath).Length -eq 0) {
        throw "pg_dump produced an empty backup"
    }
    Move-Item -LiteralPath $partialPath -Destination $finalPath
    Write-Output $finalPath
}
finally {
    if (Test-Path -LiteralPath $partialPath) {
        Remove-Item -LiteralPath $partialPath -Force
    }
}
