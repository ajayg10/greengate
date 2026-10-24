# GreenGate: idempotent GitLab project setup. Every step checks what exists first, so re-running is safe.
#
# Usage (from the repo root, with glab logged in):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\gitlab\setup.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\gitlab\setup.ps1 -ProjectPath gitlab-ai-hackathon/transcend/<id>/<project>
#
# Without -ProjectPath it creates/uses <your username>/greengate (public).
# It never deletes anything and never touches existing CI files or templates.
# No secret values: GITLAB_BOT_TOKEN and ELECTRICITYMAPS_API_KEY are set by a human in the UI.
param(
  [string]$ProjectPath = "",
  [string]$ProjectName = "greengate",
  [string]$AwsAccountId = "221082195174",
  [string]$AwsRegionPrimary = "eu-north-1",
  [string]$AwsRegionSecondary = "us-west-2"
)
$ErrorActionPreference = "Stop"
$out = Join-Path $PSScriptRoot "..\..\.gg-out"
New-Item -ItemType Directory -Force $out | Out-Null
$log = Join-Path $out "setup.txt"
"" | Out-File -Encoding utf8 $log

function Say([string]$s) { Write-Output $s; $s | Out-File -Append -Encoding utf8 $log }
function Enc([string]$s) { [Uri]::EscapeDataString($s) }

# glab api wrapper. -Str values are sent as strings, -Typed values as JSON true/false/numbers.
function Api {
  param([string]$Method = "GET", [string]$Path, [hashtable]$Str = @{}, [hashtable]$Typed = @{}, [switch]$AllowFail)
  $a = @("api", "-X", $Method, $Path)
  foreach ($k in $Str.Keys) { $a += @("-f", "$k=$($Str[$k])") }
  foreach ($k in $Typed.Keys) { $a += @("-F", "$k=$($Typed[$k])") }
  # PowerShell 5.1 turns native stderr into errors; read it as plain text instead.
  # Retry with backoff on network timeouts, rate limits (429) and server errors.
  $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  for ($try = 1; $try -le 5; $try++) {
    $raw = & glab @a 2>&1 | Out-String
    $code = $LASTEXITCODE
    # Writes are retried only when the request never reached GitLab (connect/TLS failure) or was rate limited,
    # so a retry can't create a duplicate. Reads are retried on any network or server error.
    $retryable = if ($Method -eq "GET") { 'timeout|TLS handshake|connection reset|EOF|HTTP 429|HTTP 5\d\d' } else { 'dial tcp|TLS handshake|HTTP 429' }
    if ($code -eq 0 -or $raw -notmatch $retryable) { break }
    Write-Output "  (network issue, retry $try/5)"
    Start-Sleep -Seconds (3 * $try)
  }
  $ErrorActionPreference = $prev
  if ($code -ne 0) {
    if ($AllowFail) { return $null }
    throw "glab api $Method $Path failed: $($raw.Trim())"
  }
  if ($raw.Trim()) { return $raw | ConvertFrom-Json }
  return $null
}

$me = Api -Path "user"
Say "Logged in as $($me.username)"

# ---- 1. Project -------------------------------------------------------------
if (-not $ProjectPath) { $ProjectPath = "$($me.username)/$ProjectName" }
$id = Enc $ProjectPath
$proj = Api -Path "projects/$id" -AllowFail
if (-not $proj) {
  $nsPath = $ProjectPath.Substring(0, $ProjectPath.LastIndexOf("/"))
  $ns = Api -Path "namespaces/$(Enc $nsPath)"
  $proj = Api -Method POST -Path "projects" `
    -Str @{ name = $ProjectName; path = $ProjectName; visibility = "public"; default_branch = "main";
            description = "Hands-off, carbon-aware DevSecOps on the GitLab Duo Agent Platform. MIT licensed." } `
    -Typed @{ namespace_id = $ns.id; initialize_with_readme = "false" }
  Say "[1] project $ProjectPath created (id $($proj.id))"
} else {
  Say "[1] project $ProjectPath exists (id $($proj.id), created $($proj.created_at))"
}

# ---- 2. Settings: public, pipelines must succeed, delete source branch --------
$proj = Api -Method PUT -Path "projects/$id" `
  -Str @{ visibility = "public"; merge_method = "merge"; builds_access_level = "enabled"; issues_access_level = "enabled" } `
  -Typed @{ only_allow_merge_if_pipeline_succeeds = "true"; allow_merge_on_skipped_pipeline = "false";
            remove_source_branch_after_merge = "true"; shared_runners_enabled = "true" }
Say ("[2] settings: visibility={0} pipelines_must_succeed={1} delete_source_branch={2} shared_runners={3}" -f `
  $proj.visibility, $proj.only_allow_merge_if_pipeline_succeeds, $proj.remove_source_branch_after_merge, $proj.shared_runners_enabled)

# ---- 3. Labels -----------------------------------------------------------------
$labels = [ordered]@{
  "greengate-auto"      = @("#2E7D32", "Opened by GreenGate automation (loop guard: never re-triggers the optimizer)")
  "greengate-optimizer" = @("#43A047", "CI optimizer merge request")
  "incident"            = @("#C62828", "Production incident")
  "phoenix"             = @("#EF6C00", "Handled by the phoenix-responder flow")
  "urgent"              = @("#D32F2F", "Deploy now, skip carbon-aware deferral")
  "security"            = @("#6A1B9A", "Security fix, deploy now")
  "hotfix"              = @("#B71C1C", "Hotfix, deploy now")
  "carbon-deferred"     = @("#1565C0", "Deploy deferred to a lower-carbon window")
  "sci-report"          = @("#00838F", "Software Carbon Intensity report")
  "needs-human"         = @("#F9A825", "Automation stopped safely, a human must look")
}
$existing = @((Api -Path "projects/$id/labels?per_page=100") | ForEach-Object { $_.name })
foreach ($name in $labels.Keys) {
  if ($existing -contains $name) { Say "[3] label $name exists"; continue }
  Api -Method POST -Path "projects/$id/labels" -Str @{ name = $name; color = $labels[$name][0]; description = $labels[$name][1] } | Out-Null
  Say "[3] label $name created"
}

# ---- 4. Control Room issue -------------------------------------------------------
$title = "GreenGate Control Room"
$found = @(@(Api -Path "projects/$id/issues?search=$(Enc $title)&in=title&state=opened&per_page=20") | Where-Object { $_.title -eq $title })
if ($found.Count -gt 0) {
  $issue = $found[0]; Say "[4] Control Room issue exists: #$($issue.iid)"
} else {
  $body = "CI bridge jobs post here to trigger GreenGate Duo flows (optimizer, phoenix-responder) and to report results.`n`nAutomation only: please do not close this issue."
  $issue = Api -Method POST -Path "projects/$id/issues" -Str @{ title = $title; description = $body; labels = "greengate-auto" }
  Say "[4] Control Room issue created: #$($issue.iid)"
}

# ---- 5. Protected main branch ------------------------------------------------------
$pb = Api -Path "projects/$id/protected_branches/main" -AllowFail
if (-not $pb) {
  $pb = Api -Method POST -Path "projects/$id/protected_branches" -Str @{ name = "main" } `
    -Typed @{ push_access_level = 40; merge_access_level = 40; allow_force_push = "false" }
  Say "[5] main protected (push=Maintainers, merge=Maintainers, no force push)"
} else {
  Say ("[5] main already protected: push={0} merge={1} force_push={2}" -f `
    ($pb.push_access_levels.access_level -join ","), ($pb.merge_access_levels.access_level -join ","), $pb.allow_force_push)
}

# ---- 6. CI/CD variables (no secrets here) -----------------------------------------
$vars = [ordered]@{
  "AWS_ACCOUNT_ID"        = @($AwsAccountId, $false)
  "AWS_REGION_PRIMARY"    = @($AwsRegionPrimary, $false)
  "AWS_REGION_SECONDARY"  = @($AwsRegionSecondary, $false)
  "AWS_DEPLOYER_ROLE_ARN" = @("arn:aws:iam::${AwsAccountId}:role/gg-deployer", $true)
  "AWS_CI_ROLE_ARN"       = @("arn:aws:iam::${AwsAccountId}:role/gg-ci", $false)
  "ECR_REPOSITORY_URI"    = @("$AwsAccountId.dkr.ecr.$AwsRegionPrimary.amazonaws.com/greengate", $false)
  "GG_LEDGER_BUCKET"      = @("greengate-ledger-$AwsAccountId", $false)
  "GG_SCHEDULE_GROUP"     = @("gg-deferred-deploys", $false)
  "GG_RUNTIME_ROLE_ARN"   = @("arn:aws:iam::${AwsAccountId}:role/gg-runtime", $false)
  "GG_SCHEDULER_ROLE_ARN" = @("arn:aws:iam::${AwsAccountId}:role/gg-scheduler", $false)
  "GG_DEFERRER_ROLE_ARN"  = @("arn:aws:iam::${AwsAccountId}:role/gg-deferrer", $false)
  "GG_CONTROL_ISSUE_IID"  = @("$($issue.iid)", $false)
  "GG_DEMO_MODE"          = @("true", $false)
}
foreach ($k in $vars.Keys) {
  $v = $vars[$k][0]; $prot = if ($vars[$k][1]) { "true" } else { "false" }
  $cur = Api -Path "projects/$id/variables/$k" -AllowFail
  if ($cur) {
    Api -Method PUT -Path "projects/$id/variables/$k" -Str @{ value = $v } -Typed @{ protected = $prot; masked = "false" } | Out-Null
    Say "[6] variable $k updated (protected=$prot)"
  } else {
    Api -Method POST -Path "projects/$id/variables" -Str @{ key = $k; value = $v } -Typed @{ protected = $prot; masked = "false" } | Out-Null
    Say "[6] variable $k created (protected=$prot)"
  }
}
$names = @((Api -Path "projects/$id/variables?per_page=100") | ForEach-Object { $_.key })
foreach ($secret in @("GITLAB_BOT_TOKEN", "ELECTRICITYMAPS_API_KEY")) {
  $state = if ($names -contains $secret) { "present" } else { "MISSING (a human sets it in the UI)" }
  Say "[6] secret $secret : $state"
}

# ---- 7. Runners ---------------------------------------------------------------------
$runners = @(Api -Path "projects/$id/runners?type=instance_type&per_page=100" -AllowFail)
Say ("[7] shared runners enabled={0}, instance runners visible to this project: {1}" -f $proj.shared_runners_enabled, $runners.Count)

# ---- Summary --------------------------------------------------------------------------
$summary = [ordered]@{
  project_path = $ProjectPath; project_id = $proj.id; web_url = $proj.web_url; http_url = $proj.http_url_to_repo
  created_at = $proj.created_at; visibility = $proj.visibility; control_issue_iid = $issue.iid; control_issue_url = $issue.web_url
}
$summary | ConvertTo-Json | Out-File -Encoding utf8 (Join-Path $out "project.json")
Say "`nSummary:"
$summary.GetEnumerator() | ForEach-Object { Say ("  {0} = {1}" -f $_.Key, $_.Value) }
