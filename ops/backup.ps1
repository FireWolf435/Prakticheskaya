param([string]$Output = ".\backups\servicedesk_$(Get-Date -Format yyyyMMdd_HHmmss).sql")
$ErrorActionPreference = "Stop"
$dir = Split-Path $Output -Parent
if ($dir) { New-Item -ItemType Directory -Force $dir | Out-Null }
docker compose exec -T postgres pg_dump -U dba_admin -d servicedesk | Out-File -FilePath $Output -Encoding utf8
Write-Output "Backup created: $Output"
