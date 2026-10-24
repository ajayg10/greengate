# GreenGate: run and watch pipelines from the terminal.
#
#   ci.ps1 status [-PipelineId N]      latest pipeline on main (or N): status, YAML errors, jobs, failed-job log tails
#   ci.ps1 smoke                       start a main pipeline with GG_RUN_SMOKE=true and watch it
#   ci.ps1 play -Job gg-smoke          play a manual job in the latest main pipeline and watch it
#
# Output also goes to .gg-out\ci.txt (gitignored).
param(
  [Parameter(Position = 0)][ValidateSet("status", "smoke", "play")][string]$Action = "status",
  [string]$ProjectPath = "ajayg10/greengate",
  [long]$PipelineId = 0,
  [string]$Job = "gg-smoke",
  [int]$TimeoutMin = 15
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib.ps1")
$out = Join-Path $PSScriptRoot "..\..\.gg-out"
New-Item -ItemType Directory -Force $out | Out-Null
$log = Join-Path $out "ci.txt"
"" | Out-File -Encoding utf8 $log
function Say([string]$s) { Write-Host $s; $s | Out-File -Append -Encoding utf8 $log }

$id = Enc $ProjectPath
$done = @("success", "failed", "canceled", "skipped", "manual")

function Show([long]$pid_) {
  $p = Api -Path "projects/$id/pipelines/$pid_"
  Say ("pipeline {0}  status={1}  source={2}  ref={3}  {4}" -f $p.id, $p.status, $p.source, $p.ref, $p.web_url)
  if ($p.yaml_errors) { Say "  YAML errors: $($p.yaml_errors)" }
  $jobs = @(@(Api -Path "projects/$id/pipelines/$pid_/jobs?per_page=100&include_retried=false") | Where-Object { $_ })
  if ($jobs.Count -eq 0) { Say "  (no jobs: the pipeline failed before creating any, see its web page for the reason)" }
  foreach ($j in $jobs) {
    Say ("  job {0}  {1,-10} stage={2} duration={3}s reason={4}  {5}" -f $j.name, $j.status, $j.stage, [math]::Round([double]$j.duration), $j.failure_reason, $j.web_url)
    if ($j.status -eq "failed" -or $j.status -eq "success") {
      $trace = Api -Path "projects/$id/jobs/$($j.id)/trace" -AsText -AllowFail
      if ($trace) {
        $lines = $trace -split "`n" | ForEach-Object { $_ -replace "\x1b\[[0-9;]*[A-Za-z]", "" -replace "section_(start|end):\d+:\S+\r?", "" }
        Say "  ---- last 40 log lines of $($j.name) ----"
        $lines | Select-Object -Last 40 | ForEach-Object { Say "  | $_" }
      }
    }
  }
  return $p
}

function Watch([long]$pid_) {
  $deadline = (Get-Date).AddMinutes($TimeoutMin)
  while ((Get-Date) -lt $deadline) {
    $p = Api -Path "projects/$id/pipelines/$pid_"
    $jobs = @(Api -Path "projects/$id/pipelines/$pid_/jobs?per_page=100")
    $active = @($jobs | Where-Object { $_.status -in @("created", "pending", "running", "preparing", "waiting_for_resource") })
    Write-Host (("  {0}  pipeline={1}  " -f (Get-Date -Format "HH:mm:ss"), $p.status) + (($jobs | ForEach-Object { "$($_.name)=$($_.status)" }) -join " "))
    if ($p.status -in $done -and $active.Count -eq 0) { break }
    Start-Sleep -Seconds 10
  }
  Show $pid_ | Out-Null
}

switch ($Action) {
  "status" {
    if ($PipelineId -eq 0) { $PipelineId = (@(Api -Path "projects/$id/pipelines?ref=main&per_page=1"))[0].id }
    Show $PipelineId | Out-Null
  }
  "smoke" {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $msg = & glab ci run -b main --variables GG_RUN_SMOKE:true -R $ProjectPath 2>&1 | Out-String
    $ErrorActionPreference = $prev
    if ($LASTEXITCODE -ne 0) { throw "glab ci run failed: $($msg.Trim())" }
    Start-Sleep -Seconds 3
    $p = (@(Api -Path "projects/$id/pipelines?ref=main&per_page=1"))[0]
    Say "started pipeline $($p.id) $($p.web_url)"
    Watch $p.id
  }
  "play" {
    if ($PipelineId -eq 0) { $PipelineId = (@(Api -Path "projects/$id/pipelines?ref=main&per_page=1"))[0].id }
    $j = @(Api -Path "projects/$id/pipelines/$PipelineId/jobs?per_page=100") | Where-Object { $_.name -eq $Job } | Select-Object -First 1
    if (-not $j) { throw "job $Job not found in pipeline $PipelineId" }
    Api -Method POST -Path "projects/$id/jobs/$($j.id)/play" | Out-Null
    Say "played $Job (job $($j.id)) in pipeline $PipelineId"
    Watch $PipelineId
  }
}
