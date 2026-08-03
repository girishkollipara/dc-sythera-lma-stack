#!/bin/bash
# Copyright (c) 2025 Amazon.com
# This file is licensed under the MIT License.
# See the LICENSE file in the project root for full license information.
#
# Runs after a successful deploy. Does two things, without needing Terraform
# or any state of its own - CloudFormation already knows every resource it
# created; this just reads that and republishes it somewhere else can find:
#
#  1. Outputs -> individual SSM parameters, one per output, at a predictable
#     path. This is the "curated" list - the handful of ARNs/names other
#     systems (e.g. Synthera's Terraform, via `data "aws_ssm_parameter"`)
#     actually need to reference this stack. Cheap to read, one value each.
#
#  2. Every physical resource in the stack AND all its nested stacks -> one
#     JSON file per stack, uploaded to S3, with a single SSM parameter
#     pointing at where the full dump landed. Not written as one SSM
#     parameter per resource: a full LMA deploy has several hundred
#     resources across 11 nested stacks, which would blow past SSM's
#     parameter-count and size limits for no real benefit - nothing needs to
#     look up e.g. a single internal CloudWatch log group by SSM path, but
#     someone auditing "what did this stack actually create" wants the
#     whole list in one place.
#
# Usage: ./export_stack_resources_to_ssm.sh <stack-name> <environment> <region> <artifact-bucket>
#
# <artifact-bucket> must be the SAME bucket publish.sh already uploaded to
# for this deploy (Jenkinsfile passes CFN_BUCKET_BASENAME-<env> through) -
# this script does not construct the bucket name itself, so it can never
# drift from whatever bucket the rest of the pipeline actually used.

set -euo pipefail

USAGE="Usage: $0 <stack-name> <environment> <region> <artifact-bucket>"
STACK_NAME="${1:?$USAGE}"
ENVIRONMENT="${2:?$USAGE}"
REGION="${3:?$USAGE}"
ARTIFACT_BUCKET="${4:?$USAGE}"

SSM_PREFIX="/lma/${ENVIRONMENT}"
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

echo "== Exporting ${STACK_NAME} (${ENVIRONMENT}, ${REGION}) to SSM under ${SSM_PREFIX} =="

# ---------------------------------------------------------------------------
# 1. Outputs -> /lma/<env>/outputs/<OutputKey>
# ---------------------------------------------------------------------------
echo "-- Outputs --"
aws cloudformation describe-stacks \
  --region "$REGION" \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" \
  --output text |
while IFS=$'\t' read -r OUTPUT_KEY OUTPUT_VALUE; do
  [ -z "$OUTPUT_KEY" ] && continue
  PARAM_NAME="${SSM_PREFIX}/outputs/${OUTPUT_KEY}"
  echo "  ${PARAM_NAME}"
  aws ssm put-parameter \
    --region "$REGION" \
    --name "$PARAM_NAME" \
    --value "$OUTPUT_VALUE" \
    --type String \
    --overwrite \
    --tier Standard \
    --description "LMA ${ENVIRONMENT} - CloudFormation output ${OUTPUT_KEY}" \
    >/dev/null
done

# ---------------------------------------------------------------------------
# 2. Full resource inventory (root + every nested stack) -> S3, pointer in SSM
# ---------------------------------------------------------------------------
echo "-- Full resource inventory --"

S3_PREFIX="lma/${ENVIRONMENT}/resource-inventory/$(date -u +%Y-%m-%dT%H-%M-%SZ)"

# Root stack's own resources (includes the AWS::CloudFormation::Stack
# resources for each of the 11 nested stacks, with their physical stack IDs).
aws cloudformation describe-stack-resources \
  --region "$REGION" \
  --stack-name "$STACK_NAME" \
  --output json > "${WORKDIR}/root.json"

NESTED_STACK_IDS=$(aws cloudformation describe-stack-resources \
  --region "$REGION" \
  --stack-name "$STACK_NAME" \
  --query "StackResources[?ResourceType=='AWS::CloudFormation::Stack'].[LogicalResourceId,PhysicalResourceId]" \
  --output text)

echo "[" > "${WORKDIR}/all_resources.json"
{
  echo '{"nestedStackLogicalId":"__ROOT__","stackName":"'"$STACK_NAME"'","resources":'
  cat "${WORKDIR}/root.json"
  echo '}'
} >> "${WORKDIR}/all_resources.json"

if [ -n "$NESTED_STACK_IDS" ]; then
  while IFS=$'\t' read -r LOGICAL_ID PHYSICAL_ID; do
    [ -z "$LOGICAL_ID" ] && continue
    echo "  nested stack: ${LOGICAL_ID}"
    echo "," >> "${WORKDIR}/all_resources.json"
    {
      echo '{"nestedStackLogicalId":"'"$LOGICAL_ID"'","stackName":"'"$PHYSICAL_ID"'","resources":'
      aws cloudformation describe-stack-resources \
        --region "$REGION" \
        --stack-name "$PHYSICAL_ID" \
        --output json
      echo '}'
    } >> "${WORKDIR}/all_resources.json"
  done <<< "$NESTED_STACK_IDS"
fi
echo "]" >> "${WORKDIR}/all_resources.json"

S3_KEY="${S3_PREFIX}/all_resources.json"
aws s3 cp "${WORKDIR}/all_resources.json" "s3://${ARTIFACT_BUCKET}/${S3_KEY}" --region "$REGION" >/dev/null

S3_URI="s3://${ARTIFACT_BUCKET}/${S3_KEY}"
POINTER_PARAM="${SSM_PREFIX}/full-resource-inventory"
echo "  ${POINTER_PARAM} -> ${S3_URI}"
aws ssm put-parameter \
  --region "$REGION" \
  --name "$POINTER_PARAM" \
  --value "$S3_URI" \
  --type String \
  --overwrite \
  --tier Standard \
  --description "LMA ${ENVIRONMENT} - S3 location of the full resource inventory from the most recent deploy" \
  >/dev/null

echo "== Done. Outputs under ${SSM_PREFIX}/outputs/*, full inventory at ${S3_URI} =="