[CmdletBinding()]
param([string]$OutputDirectory = "backups")

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$finalPath = Join-Path $OutputDirectory "inryeok-bot-$stamp.sql"
$partialPath = "$finalPath.partial"

try {
    & docker compose exec -T postgres pg_dump -U reviewbot reviewbot > $partialPath
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump failed"
    }
    Move-Item -LiteralPath $partialPath -Destination $finalPath
    Write-Output $finalPath
}
finally {
    if (Test-Path -LiteralPath $partialPath) {
        Remove-Item -LiteralPath $partialPath -Force
    }
}
