# GreenGate: create the project runner for the AWS EC2 runner and store its authentication token in AWS SSM
# Parameter Store (SecureString). The token is never printed. Also disables instance (shared) runners so every
# job runs on GreenGate's own low-carbon runner.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\gitlab\runner-token.ps1 [-Rotate]
param(
  [string]$ProjectPath = "ajayg10/greengate",
  [string]$Parameter = "/greengate/runner-token",
  [string]$Region = "eu-north-1",
  [string]$Description = "greengate-aws-t3-large",
  [switch]$Rotate
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib.ps1")
$id = Enc $ProjectPath
$project = Api -Path "projects/$id"

$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$exists = aws ssm get-parameter --name $Parameter --region $Region --query "Parameter.Name" --output text 2>$null
$ErrorActionPreference = $prev
if ($exists -and -not $Rotate) {
  Write-Host "SSM parameter $Parameter already exists (use -Rotate to create a new runner token)"
} else {
  $runner = Api -Method POST -Path "user/runners" -Str @{ runner_type = "project_type"; description = $Description;
    tag_list = "greengate-aws,eu-north-1" } -Typed @{ project_id = $project.id; run_untagged = "true"; locked = "true" }
  $token = $runner.token
  if (-not $token) { throw "GitLab did not return a runner token" }
  aws ssm put-parameter --name $Parameter --region $Region --type SecureString --overwrite `
    --description "GitLab runner authentication token for $ProjectPath (gg-runner EC2)" --value $token | Out-Null
  $token = $null
  Write-Host "Runner $($runner.id) created for $ProjectPath; token stored in SSM $Parameter (value not shown)."
}

$p = Api -Method PUT -Path "projects/$id" -Typed @{ shared_runners_enabled = "false" }
Write-Host "Instance runners enabled: $($p.shared_runners_enabled) (jobs now run on the GreenGate AWS runner)"
