#!/usr/bin/env bash
# GreenGate app infrastructure on AWS. Idempotent: every step checks what exists first.
#
# Creates (primary region, default eu-north-1):
#   - DynamoDB table ecolinks-links (on-demand: zero cost when idle)
#   - Lambda "ecolinks" from a container image in ECR, aliases "live" and "canary", a public Function URL on
#     each (live = the app URL, canary = what the SLO probe hits)
#   - Lambda "gg-deferrer" (zip, arm64/Graviton) invoked by EventBridge Scheduler for deferred deploys
#   - Amplify Hosting app "greengate-dashboard" with branch "main" (static dashboard, manual deploys)
#
# Usage:  IMAGE_URI=<ecr repo>:<tag> bash infra/aws/app.sh
# The first run needs IMAGE_URI (an image already pushed to ECR); later runs can omit it.
set -euo pipefail
export MSYS_NO_PATHCONV=1
export AWS_PAGER=""

REGION="${AWS_REGION_PRIMARY:-eu-north-1}"
export AWS_REGION="$REGION"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text | tr -d '\r')"
FN=ecolinks
TABLE=ecolinks-links
RUNTIME_ROLE="arn:aws:iam::$ACCOUNT_ID:role/gg-runtime"
DEFERRER_ROLE="arn:aws:iam::$ACCOUNT_ID:role/gg-deferrer"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
TMPD="$(mktemp -d)"; trap 'rm -rf "$TMPD"' EXIT
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi; }
q() { aws "$@" 2>/dev/null | tr -d '\r'; }

echo "[1/5] DynamoDB table $TABLE"
if aws dynamodb describe-table --table-name "$TABLE" >/dev/null 2>&1; then
  echo "  exists"
else
  aws dynamodb create-table --table-name "$TABLE" --billing-mode PAY_PER_REQUEST \
    --attribute-definitions AttributeName=code,AttributeType=S --key-schema AttributeName=code,KeyType=HASH \
    --tags Key=project,Value=greengate >/dev/null
  aws dynamodb wait table-exists --table-name "$TABLE"
  echo "  created"
fi

echo "[2/5] Lambda $FN (container image)"
if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  echo "  exists"
else
  : "${IMAGE_URI:?first run needs IMAGE_URI=<ecr repo>:<tag>}"
  aws lambda create-function --function-name "$FN" --package-type Image --code "ImageUri=$IMAGE_URI" \
    --role "$RUNTIME_ROLE" --architectures x86_64 --memory-size 512 --timeout 15 \
    --environment "Variables={ECOLINKS_TABLE=$TABLE,GG_GIT_SHA=bootstrap}" \
    --description "EcoLinks URL shortener, shipped by GreenGate" --tags project=greengate >/dev/null
  aws lambda wait function-active-v2 --function-name "$FN"
  echo "  created"
fi

echo "[3/5] Aliases and Function URLs"
for alias in live canary; do
  if ! aws lambda get-alias --function-name "$FN" --name "$alias" >/dev/null 2>&1; then
    v="$(q lambda publish-version --function-name "$FN" --description "GreenGate bootstrap" --query Version --output text)"
    aws lambda create-alias --function-name "$FN" --name "$alias" --function-version "$v" >/dev/null
    echo "  alias $alias -> v$v created"
  fi
  if ! aws lambda get-function-url-config --function-name "$FN" --qualifier "$alias" >/dev/null 2>&1; then
    aws lambda create-function-url-config --function-name "$FN" --qualifier "$alias" --auth-type NONE >/dev/null
    echo "  function URL for $alias created"
  fi
  # Public URL: allow anonymous invocation through the Function URL only.
  aws lambda add-permission --function-name "$FN" --qualifier "$alias" --statement-id "public-url-$alias" \
    --action lambda:InvokeFunctionUrl --principal "*" --function-url-auth-type NONE >/dev/null 2>&1 || true
  aws lambda add-permission --function-name "$FN" --qualifier "$alias" --statement-id "public-invoke-$alias" \
    --action lambda:InvokeFunction --principal "*" --invoked-via-function-url >/dev/null 2>&1 || true
done
LIVE_URL="$(q lambda get-function-url-config --function-name "$FN" --qualifier live --query FunctionUrl --output text)"
CANARY_URL="$(q lambda get-function-url-config --function-name "$FN" --qualifier canary --query FunctionUrl --output text)"
# Short links must point at the live URL, never at the canary alias.
CUR_PUBLIC="$(q lambda get-function-configuration --function-name "$FN" --query 'Environment.Variables.ECOLINKS_PUBLIC_URL' --output text)"
if [ "$CUR_PUBLIC" != "${LIVE_URL%/}" ]; then
  SHA_NOW="$(q lambda get-function-configuration --function-name "$FN" --query 'Environment.Variables.GG_GIT_SHA' --output text)"
  aws lambda update-function-configuration --function-name "$FN" \
    --environment "Variables={ECOLINKS_TABLE=$TABLE,GG_GIT_SHA=${SHA_NOW:-bootstrap},ECOLINKS_PUBLIC_URL=${LIVE_URL%/}}" >/dev/null
  aws lambda wait function-updated-v2 --function-name "$FN"
  echo "  ECOLINKS_PUBLIC_URL set (takes effect from the next published version)"
fi

echo "[4/5] Lambda gg-deferrer (zip, arm64)"
mkdir -p "$TMPD/def" && cp "$ROOT/greengate/deploy/deferrer_handler.py" "$TMPD/def/deferrer_handler.py"
(cd "$TMPD/def" && python -c "import zipfile; z=zipfile.ZipFile('../def.zip','w',zipfile.ZIP_DEFLATED); z.write('deferrer_handler.py'); z.close()")
ZIP="fileb://$(native "$TMPD/def.zip")"
if aws lambda get-function --function-name gg-deferrer >/dev/null 2>&1; then
  aws lambda update-function-code --function-name gg-deferrer --zip-file "$ZIP" >/dev/null
  echo "  code updated"
else
  aws lambda create-function --function-name gg-deferrer --runtime python3.12 --architectures arm64 \
    --handler deferrer_handler.handler --role "$DEFERRER_ROLE" --zip-file "$ZIP" --timeout 30 --memory-size 128 \
    --environment "Variables={GG_TRIGGER_TOKEN_PARAM=/greengate/gitlab-trigger-token}" \
    --description "GreenGate: re-triggers a deferred GitLab deploy pipeline" --tags project=greengate >/dev/null
  echo "  created"
fi
DEFERRER_ARN="arn:aws:lambda:$REGION:$ACCOUNT_ID:function:gg-deferrer"

echo "[5/5] Amplify app greengate-dashboard"
APP_ID="$(q amplify list-apps --query "apps[?name=='greengate-dashboard'].appId | [0]" --output text)"
if [ -z "$APP_ID" ] || [ "$APP_ID" = "None" ]; then
  APP_ID="$(q amplify create-app --name greengate-dashboard --platform WEB \
    --description "GreenGate carbon dashboard (static, deployed by CI)" --tags project=greengate \
    --query app.appId --output text)"
  echo "  app created: $APP_ID"
else
  echo "  app exists: $APP_ID"
fi
if ! aws amplify get-branch --app-id "$APP_ID" --branch-name main >/dev/null 2>&1; then
  aws amplify create-branch --app-id "$APP_ID" --branch-name main --stage PRODUCTION >/dev/null
  echo "  branch main created"
fi
DOMAIN="$(q amplify get-app --app-id "$APP_ID" --query app.defaultDomain --output text)"

echo
echo "Summary (no secrets):"
echo "  GG_APP_URL=${LIVE_URL%/}"
echo "  GG_CANARY_URL=${CANARY_URL%/}"
echo "  GG_FUNCTION_NAME=$FN"
echo "  GG_DEFERRER_ARN=$DEFERRER_ARN"
echo "  GG_AMPLIFY_APP_ID=$APP_ID"
echo "  GG_DASHBOARD_URL=https://main.$DOMAIN"
