# GreenGate: create the GitLab pipeline trigger token used by the gg-deferrer Lambda and store it in AWS SSM
# Parameter Store as a SecureString. The token is never printed.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\gitlab\trigger-token.ps1 [-Rotate]
#
# Idempotent: without -Rotate it does nothing when the parameter already exists.
param(
  [string]$ProjectPath = "ajayg10/greengate",
  [string]$Parameter = "/greengate/gitlab-trigger-token",
  [string]$Region = "eu-north-1",
  [switch]$Rotate
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib.ps1")
$id = Enc $ProjectPath

$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$exists = aws ssm get-parameter --name $Parameter --region $Region --query "Parameter.Name" --output text 2>$null
$ErrorActionPreference = $prev
if ($exists -and -not $Rotate) { Write-Host "SSM parameter $Parameter already exists (use -Rotate to replace it)"; exit 0 }

$trigger = Api -Method POST -Path "projects/$id/triggers" -Str @{ description = "greengate-deferrer (EventBridge Scheduler re-trigger)" }
$token = $trigger.token
if (-not $token) { throw "GitLab did not return a trigger token" }
aws ssm put-parameter --name $Parameter --region $Region --type SecureString --overwrite `
  --description "GitLab pipeline trigger token for $ProjectPath (used by gg-deferrer)" --value $token | Out-Null
$token = $null
Write-Host "Trigger $($trigger.id) created in GitLab and stored in SSM $Parameter (value not shown)."
