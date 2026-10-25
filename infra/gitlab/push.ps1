# Push with retries (flaky networks). Usage: push.ps1 [-Remote origin] [-Ref main] [-Tags]
param([string]$Remote = "origin", [string]$Ref = "main", [switch]$Tags, [int]$Tries = 6)
$ErrorActionPreference = "Continue"
for ($i = 1; $i -le $Tries; $i++) {
  if ($Tags) { git push $Remote --tags } else { git push $Remote $Ref }
  if ($LASTEXITCODE -eq 0) { Write-Host "pushed $Ref to $Remote (attempt $i)"; exit 0 }
  Write-Host "push failed (attempt $i/$Tries), retrying..."
  Start-Sleep -Seconds (5 * $i)
}
exit 1
