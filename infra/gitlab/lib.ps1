# Shared helpers for the GreenGate GitLab scripts (dot-source this file).

function Enc([string]$s) { [Uri]::EscapeDataString($s) }

# glab api wrapper. -Str values are sent as strings, -Typed values as JSON true/false/numbers.
# -AsText returns the response text instead of parsed JSON (for job logs).
function Api {
  param([string]$Method = "GET", [string]$Path, [hashtable]$Str = @{}, [hashtable]$Typed = @{},
        [switch]$AllowFail, [switch]$AsText)
  $a = @("api", "-X", $Method, $Path)
  foreach ($k in $Str.Keys) { $a += @("-f", "$k=$($Str[$k])") }
  foreach ($k in $Typed.Keys) { $a += @("-F", "$k=$($Typed[$k])") }
  # PowerShell 5.1 turns native stderr into errors; read it as plain text instead.
  # Writes are retried only when the request never reached GitLab (connect/TLS failure) or was rate limited,
  # so a retry can't create a duplicate. Reads are retried on any network or server error.
  $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  for ($try = 1; $try -le 5; $try++) {
    $raw = & glab @a 2>&1 | Out-String
    $code = $LASTEXITCODE
    $retryable = if ($Method -eq "GET") { 'timeout|TLS handshake|connection reset|EOF|HTTP 429|HTTP 5\d\d' } else { 'dial tcp|TLS handshake|HTTP 429' }
    if ($code -eq 0 -or $raw -notmatch $retryable) { break }
    Write-Host "  (network issue, retry $try/5)"
    Start-Sleep -Seconds (3 * $try)
  }
  $ErrorActionPreference = $prev
  if ($code -ne 0) {
    if ($AllowFail) { return $null }
    throw "glab api $Method $Path failed: $($raw.Trim())"
  }
  if ($AsText) { return $raw }
  if (-not $raw.Trim()) { return $null }
  $obj = $raw | ConvertFrom-Json
  # PowerShell 5.1 returns a JSON array as one object: emit its elements so callers get a flat list.
  if ($obj -is [System.Array]) { foreach ($o in $obj) { $o }; return }
  return $obj
}
