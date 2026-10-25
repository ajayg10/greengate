#!/usr/bin/env bash
# GreenGate CI runner on AWS EC2 in eu-north-1 (Stockholm, a low-carbon grid). Idempotent.
#
# - IAM role + instance profile gg-runner-ec2 (SSM Session Manager for debugging, read the runner token only)
# - security group gg-runner-sg: no inbound at all, outbound only
# - one t3.large (2 vCPU, 8 GB: the same size as GitLab's default hosted runner) running gitlab-runner with the
#   Docker executor, concurrent = 1 so every job owns the VM (this is what the SCI model assumes)
#
# Prerequisite: infra/gitlab/runner-token.ps1 stored the runner token in SSM /greengate/runner-token.
# Usage: bash infra/aws/runner.sh            (STOP=1 bash infra/aws/runner.sh stops the instance)
set -euo pipefail
export MSYS_NO_PATHCONV=1 AWS_PAGER=""
REGION="${AWS_REGION_PRIMARY:-eu-north-1}"; export AWS_REGION="$REGION"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text | tr -d '\r')"
TYPE="${RUNNER_INSTANCE_TYPE:-t3.large}"
ROLE=gg-runner-ec2
TMPD="$(mktemp -d)"; trap 'rm -rf "$TMPD"' EXIT
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi; }
q() { aws "$@" | tr -d '\r'; }

ID="$(q ec2 describe-instances --filters Name=tag:Name,Values=gg-runner Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].InstanceId | [0]' --output text)"
if [ "${STOP:-}" = "1" ]; then
  [ "$ID" != "None" ] && aws ec2 stop-instances --instance-ids "$ID" >/dev/null && echo "stopping $ID"; exit 0
fi

echo "[1/4] IAM role and instance profile $ROLE"
cat > "$TMPD/trust.json" <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF
cat > "$TMPD/token.json" <<EOF
{"Version":"2012-10-17","Statement":[{"Sid":"ReadRunnerToken","Effect":"Allow","Action":"ssm:GetParameter",
 "Resource":"arn:aws:ssm:$REGION:$ACCOUNT_ID:parameter/greengate/runner-token"}]}
EOF
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document "file://$(native "$TMPD/trust.json")" \
    --description "GreenGate GitLab runner EC2 instance" --tags Key=project,Value=greengate >/dev/null
  echo "  role created"
fi
aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam put-role-policy --role-name "$ROLE" --policy-name read-runner-token --policy-document "file://$(native "$TMPD/token.json")"
if ! aws iam get-instance-profile --instance-profile-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE"
  echo "  instance profile created (waiting for IAM to propagate)"; sleep 15
fi

echo "[2/4] Security group gg-runner-sg (no inbound)"
VPC="$(q ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)"
SG="$(q ec2 describe-security-groups --filters Name=group-name,Values=gg-runner-sg Name=vpc-id,Values="$VPC" --query 'SecurityGroups[0].GroupId' --output text)"
if [ "$SG" = "None" ]; then
  SG="$(q ec2 create-security-group --group-name gg-runner-sg --description "GreenGate runner: egress only" --vpc-id "$VPC" \
    --tag-specifications 'ResourceType=security-group,Tags=[{Key=project,Value=greengate}]' --query GroupId --output text)"
  echo "  created $SG"
fi

echo "[3/4] Runner instance ($TYPE)"
if [ "$ID" = "None" ] || [ -z "$ID" ]; then
  AMI="$(q ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 --query Parameter.Value --output text)"
  cat > "$TMPD/user-data.sh" <<'EOF'
#!/bin/bash
set -euxo pipefail
dnf install -y docker
systemctl enable --now docker
curl -fsSL --retry 5 -o /usr/local/bin/gitlab-runner \
  https://s3.dualstack.us-east-1.amazonaws.com/gitlab-runner-downloads/latest/binaries/gitlab-runner-linux-amd64
chmod +x /usr/local/bin/gitlab-runner
mkdir -p /var/lib/gitlab-runner
TOKEN="$(aws ssm get-parameter --name /greengate/runner-token --with-decryption --query Parameter.Value --output text --region eu-north-1)"
/usr/local/bin/gitlab-runner register --non-interactive --url https://gitlab.com --token "$TOKEN" \
  --executor docker --docker-image alpine:3.20 --docker-privileged \
  --docker-volumes /certs/client --docker-volumes /cache
unset TOKEN
sed -i 's/^concurrent = .*/concurrent = 1/' /etc/gitlab-runner/config.toml
/usr/local/bin/gitlab-runner install --user=root --working-directory=/var/lib/gitlab-runner --config /etc/gitlab-runner/config.toml
/usr/local/bin/gitlab-runner start
# Weekly cleanup of unused images and volumes so the disk never fills up (AL2023 has no cron: systemd timer).
printf '[Unit]
Description=GreenGate weekly docker prune
[Service]
Type=oneshot
ExecStart=/usr/bin/docker system prune -af --volumes
' > /etc/systemd/system/gg-docker-prune.service
printf '[Unit]
Description=GreenGate weekly docker prune
[Timer]
OnCalendar=Sun 03:00
Persistent=true
[Install]
WantedBy=timers.target
' > /etc/systemd/system/gg-docker-prune.timer
systemctl daemon-reload
systemctl enable --now gg-docker-prune.timer
EOF
  ID="$(q ec2 run-instances --image-id "$AMI" --instance-type "$TYPE" --iam-instance-profile Name="$ROLE" \
    --security-group-ids "$SG" --user-data "file://$(native "$TMPD/user-data.sh")" \
    --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=40,VolumeType=gp3,DeleteOnTermination=true}' \
    --metadata-options HttpTokens=required,HttpEndpoint=enabled \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=gg-runner},{Key=project,Value=greengate}]' \
    --query 'Instances[0].InstanceId' --output text)"
  echo "  launched $ID"
else
  STATE="$(q ec2 describe-instances --instance-ids "$ID" --query 'Reservations[0].Instances[0].State.Name' --output text)"
  [ "$STATE" = "stopped" ] && aws ec2 start-instances --instance-ids "$ID" >/dev/null && echo "  starting $ID"
  echo "  exists: $ID ($STATE)"
fi

echo "[4/4] Waiting for $ID to run"
aws ec2 wait instance-running --instance-ids "$ID"
echo "  running. Registration happens in user data (about 1-2 minutes); check the project's Settings > CI/CD > Runners."
