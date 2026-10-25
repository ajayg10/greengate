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
  [string]$AwsRegionSecondary = "us-west-2",
  # Outputs of infra/aws/app.sh (not secrets).
  [string]$AppUrl = "https://u72w7ug22esgggvbemt4ovrfnu0xrfpe.lambda-url.eu-north-1.on.aws",
  [string]$AmplifyAppId = "d28t6c9al9r6ob"
)
$ErrorActionPreference = "Stop"
$out = Join-Path $PSScriptRoot "..\..\.gg-out"
New-Item -ItemType Directory -Force $out | Out-Null
$log = Join-Path $out "setup.txt"
"" | Out-File -Encoding utf8 $log

function Say([string]$s) { Write-Output $s; $s | Out-File -Append -Encoding utf8 $log }
. (Join-Path $PSScriptRoot "lib.ps1")

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
# Pipeline variables: new projects allow no one to set them. Maintainers (and trigger tokens they own) need
# them for GG_DEFERRED re-triggers and the manual smoke run, so allow Maintainers and above only.
$proj = Api -Method PUT -Path "projects/$id" `
  -Str @{ visibility = "public"; merge_method = "merge"; builds_access_level = "enabled"; issues_access_level = "enabled";
          ci_pipeline_variables_minimum_override_role = "maintainer" } `
  -Typed @{ only_allow_merge_if_pipeline_succeeds = "true"; allow_merge_on_skipped_pipeline = "false";
            remove_source_branch_after_merge = "true"; shared_runners_enabled = "true" }
Say ("[2] settings: visibility={0} pipelines_must_succeed={1} delete_source_branch={2} shared_runners={3} pipeline_variables={4}" -f `
  $proj.visibility, $proj.only_allow_merge_if_pipeline_succeeds, $proj.remove_source_branch_after_merge, $proj.shared_runners_enabled,
  $proj.ci_pipeline_variables_minimum_override_role)

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
  "GG_FUNCTION_NAME"      = @("ecolinks", $false)
  "GG_DEFERRER_ARN"       = @("arn:aws:lambda:${AwsRegionPrimary}:${AwsAccountId}:function:gg-deferrer", $false)
  "GG_APP_URL"            = @($AppUrl, $false)
  "GG_AMPLIFY_APP_ID"     = @($AmplifyAppId, $false)
  "GG_DASHBOARD_URL"      = @("https://main.$AmplifyAppId.amplifyapp.com", $false)
}
foreach ($k in $vars.Keys) {
  $v = $vars[$k][0]; $prot = if ($vars[$k][1]) { "true" } else { "false" }
  $cur = Api -Path "projects/$id/variables/$k" -AllowFail
  if ($cur -and $cur.value -eq $v -and "$($cur.protected)".ToLower() -eq $prot) {
    Say "[6] variable $k unchanged"
  } elseif ($cur) {
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
