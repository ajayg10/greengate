#!/usr/bin/env bash
# GreenGate AWS bootstrap. Idempotent: every step checks what exists first, so re-running is safe.
#
# Creates (primary region, default eu-north-1):
#   - S3 bucket for the carbon ledger (private, versioned)
#   - ECR repository "greengate" (scan on push, lifecycle, Lambda pull policy)
#   - IAM roles: gg-runtime (Lambda), gg-deferrer (Lambda), gg-scheduler (EventBridge Scheduler)
#   - EventBridge Scheduler group "gg-deferred-deploys"
#   - IAM OIDC provider for https://gitlab.com
#   - IAM roles assumed by GitLab CI (keyless OIDC, no access keys):
#       gg-deployer  -> only the main branch of our project(s)
#       gg-ci        -> any ref of our project(s), ledger + image push only
#   - AWS Budget "greengate-monthly" ($10) with email alerts
#
# Usage:
#   GG_BUDGET_EMAIL=you@example.com \
#   GITLAB_PROJECT_PATHS="your-user/greengate gitlab-ai-hackathon/transcend/123/greengate" \
#   bash infra/aws/bootstrap.sh
#
# GITLAB_PROJECT_PATHS is optional on the first run: without it the two CI roles are skipped.
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: don't rewrite "/aws/..." arguments into Windows paths
export AWS_PAGER=""

REGION="${AWS_REGION_PRIMARY:-eu-north-1}"
export AWS_REGION="$REGION"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text | tr -d '\r')"
BUCKET="${GG_LEDGER_BUCKET:-greengate-ledger-${ACCOUNT_ID}}"
ECR_REPO="${ECR_REPO:-greengate}"
SCHEDULE_GROUP="${GG_SCHEDULE_GROUP:-gg-deferred-deploys}"
BUDGET_EMAIL="${GG_BUDGET_EMAIL:-}"
GITLAB_PROJECT_PATHS="${GITLAB_PROJECT_PATHS:-}"
TAGS_IAM=(Key=project,Value=greengate)

HERE="$(cd "$(dirname "$0")" && pwd)"
POL="$HERE/policies"
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT

native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi; }

# render <template> [extra sed args...] -> prints a file:// URI the aws CLI can read
render() {
  local name="$1"; shift
  local out="$TMPD/$name"
  sed -e "s|__ACCOUNT_ID__|$ACCOUNT_ID|g" -e "s|__REGION__|$REGION|g" -e "s|__BUCKET__|$BUCKET|g" \
      -e "s|__ECR_REPO__|$ECR_REPO|g" -e "s|__SCHEDULE_GROUP__|$SCHEDULE_GROUP|g" \
      -e "s|__BUDGET_EMAIL__|$BUDGET_EMAIL|g" "$@" "$POL/$name" > "$out"
  echo "file://$(native "$out")"
}

ensure_role() { # <name> <trust-uri> <description>
  if aws iam get-role --role-name "$1" >/dev/null 2>&1; then
    aws iam update-assume-role-policy --role-name "$1" --policy-document "$2"
    echo "  role $1: exists, trust policy refreshed"
  else
    aws iam create-role --role-name "$1" --assume-role-policy-document "$2" \
      --description "$3" --tags "${TAGS_IAM[@]}" >/dev/null
    echo "  role $1: created"
  fi
}

put_inline() { # <role> <policy-name> <uri>
  aws iam put-role-policy --role-name "$1" --policy-name "$2" --policy-document "$3"
  echo "  role $1: inline policy $2 set"
}

echo "Account $ACCOUNT_ID, region $REGION"

echo "[1/7] Ledger bucket s3://$BUCKET"
if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  echo "  exists"
else
  aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  echo "  created"
fi
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration "$(render bucket-lifecycle.json)" >/dev/null
aws s3api put-bucket-tagging --bucket "$BUCKET" --tagging 'TagSet=[{Key=project,Value=greengate}]'
echo "  public access blocked, versioning on, lifecycle set"

echo "[2/7] ECR repository $ECR_REPO"
if aws ecr describe-repositories --repository-names "$ECR_REPO" >/dev/null 2>&1; then
  echo "  exists"
else
  aws ecr create-repository --repository-name "$ECR_REPO" --image-scanning-configuration scanOnPush=true \
    --tags Key=project,Value=greengate >/dev/null
  echo "  created"
fi
aws ecr put-lifecycle-policy --repository-name "$ECR_REPO" --lifecycle-policy-text "$(render ecr-lifecycle.json)" >/dev/null
aws ecr set-repository-policy --repository-name "$ECR_REPO" --policy-text "$(render ecr-repo-policy.json)" >/dev/null
echo "  lifecycle + Lambda pull policy set"

echo "[3/7] Lambda and scheduler roles"
ensure_role gg-runtime "$(render lambda-trust.json)" "GreenGate: runtime identity of the ecolinks and dashboard Lambdas"
aws iam attach-role-policy --role-name gg-runtime --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
put_inline gg-runtime gg-runtime-ledger-read "$(render runtime.json)"
ensure_role gg-deferrer "$(render lambda-trust.json)" "GreenGate: Lambda that re-triggers a deferred GitLab pipeline"
aws iam attach-role-policy --role-name gg-deferrer --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
put_inline gg-deferrer gg-deferrer-trigger-token "$(render deferrer.json)"
ensure_role gg-scheduler "$(render scheduler-trust.json)" "GreenGate: EventBridge Scheduler identity that invokes gg-deferrer"
put_inline gg-scheduler gg-scheduler-invoke "$(render scheduler.json)"

echo "[4/7] Scheduler group $SCHEDULE_GROUP"
if aws scheduler get-schedule-group --name "$SCHEDULE_GROUP" >/dev/null 2>&1; then
  echo "  exists"
else
  aws scheduler create-schedule-group --name "$SCHEDULE_GROUP" --tags Key=project,Value=greengate >/dev/null
  echo "  created"
fi

echo "[5/7] GitLab OIDC provider"
OIDC_ARN="arn:aws:iam::$ACCOUNT_ID:oidc-provider/gitlab.com"
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" >/dev/null 2>&1; then
  echo "  exists"
else
  aws iam create-open-id-connect-provider --url https://gitlab.com --client-id-list https://gitlab.com \
    --tags Key=project,Value=greengate >/dev/null
  echo "  created"
fi

echo "[6/7] CI roles (keyless OIDC)"
if [ -z "$GITLAB_PROJECT_PATHS" ]; then
  echo "  skipped: set GITLAB_PROJECT_PATHS to create gg-deployer and gg-ci"
else
  subs() { local out="" p; for p in $GITLAB_PROJECT_PATHS; do out+="\"project_path:${p}$1\","; done; echo "[${out%,}]"; }
  ensure_role gg-deployer "$(render oidc-trust.json -e "s|__SUBS__|$(subs ':ref_type:branch:ref:main')|")" \
    "GreenGate: GitLab CI deploy role (main branch only)"
  put_inline gg-deployer gg-deployer "$(render deployer.json)"
  ensure_role gg-ci "$(render oidc-trust.json -e "s|__SUBS__|$(subs ':*')|")" \
    "GreenGate: GitLab CI role for any ref (ledger + image push)"
  put_inline gg-ci gg-ci "$(render ci.json)"
fi

echo "[7/7] Budget"
if [ -z "$BUDGET_EMAIL" ]; then
  echo "  skipped: set GG_BUDGET_EMAIL to create the budget alert"
elif aws budgets describe-budget --region us-east-1 --account-id "$ACCOUNT_ID" --budget-name greengate-monthly >/dev/null 2>&1; then
  echo "  exists"
else
  aws budgets create-budget --region us-east-1 --account-id "$ACCOUNT_ID" --budget "$(render budget.json)" \
    --notifications-with-subscribers "$(render budget-notifications.json)"
  echo "  created (alerts at 50% and 100% actual, 100% forecast)"
fi

echo
echo "Summary (no secrets):"
echo "  AWS_ACCOUNT_ID=$ACCOUNT_ID"
echo "  AWS_REGION_PRIMARY=$REGION"
echo "  ECR_REPOSITORY_URI=$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$ECR_REPO"
echo "  GG_LEDGER_BUCKET=$BUCKET"
echo "  GG_SCHEDULE_GROUP=$SCHEDULE_GROUP"
echo "  GG_RUNTIME_ROLE_ARN=arn:aws:iam::$ACCOUNT_ID:role/gg-runtime"
echo "  GG_DEFERRER_ROLE_ARN=arn:aws:iam::$ACCOUNT_ID:role/gg-deferrer"
echo "  GG_SCHEDULER_ROLE_ARN=arn:aws:iam::$ACCOUNT_ID:role/gg-scheduler"
if [ -n "$GITLAB_PROJECT_PATHS" ]; then
  echo "  AWS_DEPLOYER_ROLE_ARN=arn:aws:iam::$ACCOUNT_ID:role/gg-deployer"
  echo "  AWS_CI_ROLE_ARN=arn:aws:iam::$ACCOUNT_ID:role/gg-ci"
fi
