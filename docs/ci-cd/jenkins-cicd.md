# Jenkins CI/CD Pipeline

How LMA gets built, published, and deployed through Jenkins - the full
setup, what each stage does, what's published after every deploy, and how
to stand up a new environment. This is the single place documenting the
CI/CD side of the repo; see [SSM Parameter Reference](ssm-parameter-reference.md)
for the authoritative, always-current list of every SSM parameter this
pipeline publishes.

## 1. Overview

One Jenkins Multibranch Pipeline job (`lma-cloudformation`) drives every
environment. The [`Jenkinsfile`](../../Jenkinsfile) at the repo root is the
single source of truth - one pipeline definition, branch decides the
target environment:

| Git branch | Environment | CloudFormation stack |
|---|---|---|
| `qa` | `qa` | `LMASA-qa` |
| `dev01` | `dev01` | `LMASA-dev01` |
| `main` | `prod` | `LMASA-prod` |
| anything else | none | build/lint only - publish/deploy are **skipped**, not failed, so feature branches still get CI feedback |

Only `qa` is actually stood up today (IAM, parameters, Jenkins credentials).
`dev01` and `prod` are mapped in the Jenkinsfile but not yet provisioned -
see [Section 5](#5-standing-up-a-new-environment).

## 2. The build agent: Docker, not the Jenkins host

Every stage runs inside a container built from [`ci/Dockerfile`](../../ci/Dockerfile),
not directly on the Jenkins EC2 host. The host only needs Docker itself;
every build tool (node 20, make, zip, python 3.12, aws cli v2, sam cli,
docker cli) lives in the image. This was a deliberate choice over
hand-installing tools on the host - keeps the host clean, keeps the
toolchain versioned and reproducible alongside the code, and any other
Jenkins job can reuse the same image.

Two things had to be worked out to make this work reliably:

- **`ENV HOME=/tmp`** - Jenkins' Docker Pipeline plugin runs the container
  as its own numeric UID (to keep workspace file ownership consistent with
  the host), which has no matching entry in the image's `/etc/passwd`. With
  no passwd entry, `$HOME` falls back to `/`, and npm/pip/aws-cli all fail
  trying to write caches there. Pointing `HOME` at `/tmp` fixes this for
  every tool at once.
- **`--group-add 988`** in the Jenkinsfile's `agent` block - the container
  talks to the *host's* Docker daemon over a mounted socket
  (`-v /var/run/docker.sock:/var/run/docker.sock`, no Docker-in-Docker).
  That socket is owned by `root:988` on this specific host, and the
  container's user isn't a member of that group by default. Without this,
  every `docker` command inside the container fails with "permission
  denied" even though the socket is mounted correctly. **If this host's
  docker.sock group ever changes, this number has to change with it** -
  check via `ls -la /var/run/docker.sock` on the Jenkins host.

Python specifically is built `FROM python:3.12-slim-bookworm` rather than
adding Python on top of a Node base image or using Debian's system Python.
Debian's own `python3` package is pinned to 3.11.2, which predates a
security feature (PEP 706) that AWS SAM CLI now hard-requires. Node 20 is
added on top via NodeSource's official apt repo rather than copied in from
a separate Node image - an earlier version of this Dockerfile tried
copying a compiled Python binary from `python:3.12-slim` into
`node:20-bookworm` and broke with `GLIBC_2.38 not found`, because the two
"bookworm" tagged images were actually built against different glibc
versions.

## 3. Pipeline stages

In order, from [`Jenkinsfile`](../../Jenkinsfile):

1. **Validate Branch Mapping** - looks up the branch in `ENV_MAP`, sets
   `env.DEPLOY_ENV` (or leaves it null for an unmapped branch).
2. **Check Environment** - fails fast if any required tool
   (bash/node/npm/docker/zip/python3/pip3/virtualenv/aws/sam/make) is
   missing from the image, plus a non-fatal docker-socket diagnostic.
3. **Setup CLI** - `make setup-python setup-cli`. `setup-python` creates
   `.venv` and installs Python dev/lint tooling (bandit, black, cfn-lint,
   flake8, mypy, pylint, yamllint); `setup-cli` installs the `lma` SDK/CLI
   into that venv. (Both are needed - `setup-cli` alone assumes the venv
   already exists and fails with `.venv/bin/pip: No such file or
   directory` otherwise.)
4. **Build** - `make build`, which runs `build-ui` + `build-websocket` +
   `build-vp` (each does its own `npm ci && npm run build` for the React
   UI, the WebSocket transcriber, and the Virtual Participant backend).
   Note: the Python Lambda functions aren't built here - SAM packages
   those during Publish.
5. **Publish** *(qa/dev01/prod only)* - activates the venv and runs
   `lma publish --bucket-basename <basename>-<env> --prefix lma --region
   <region> --force` directly (not `publish.sh` - see the inline comment
   in the Jenkinsfile for why `--force` and the direct CLI call are both
   necessary). This does the SAM build/package for every Lambda and
   uploads every stack template (root + all nested) to S3, with
   placeholder tokens like `<REGION_TOKEN>` in nested `TemplateURL`s
   substituted for real values in the uploaded copies only - the local
   workspace files keep the placeholders.
6. **Deploy** *(qa/dev01/prod only)* - downloads the already-rendered
   `lma-main.yaml` back from S3 (since `aws cloudformation deploy` has no
   `--template-url` flag - `--template-file` only accepts a local path,
   and the local workspace copy still has unrendered placeholder tokens),
   then runs `aws cloudformation deploy` against `LMASA-<env>` using that
   file, `deploy/params/lma-<env>.json` for parameter overrides, and the
   environment's CloudFormation service role.
7. **Verify** *(qa/dev01/prod only)* - confirms the stack reached
   `CREATE_COMPLETE` or `UPDATE_COMPLETE`; fails the build otherwise.
8. **Export Outputs to SSM** *(qa/dev01/prod only)* - runs
   [`scripts/export_stack_resources_to_ssm.sh`](../../scripts/export_stack_resources_to_ssm.sh)
   (see [Section 6](#6-what-gets-published-after-every-deploy)).

Every AWS-touching stage (Publish, Deploy, Verify, Export) is wrapped in
`withCredentials` binding a per-environment Jenkins credential
(`aws-lma-<env>`) - nothing AWS-related runs on ambient/instance
credentials.

**Known gap:** there's no lint or test stage today, even though
`make lint-cicd` and `make test` both already exist in the root
`Makefile` and their tools are already installed by `Setup CLI`. Nothing
currently blocks a broken test or invalid template from reaching a live
deploy.

## 4. IAM design (per environment, least-privilege)

Two separate identities per environment, deliberately not shared across
`qa`/`dev01`/`prod` - isolating blast radius was chosen over the
convenience of one shared role:

**1. Deployer** (`lma-<env>-deployer`, an IAM user) - the identity behind
the Jenkins `aws-lma-<env>` credential. Can only touch its own stack and
artifact bucket. Policy: [`deploy/iam/deployer-policy-qa.json`](../../deploy/iam/deployer-policy-qa.json)
(a `dev01` copy is prepped but not yet applied), 5 statements:
- `cloudformation:*` scoped to `stack/LMASA-<env>*/*`
- `iam:PassRole` scoped to exactly `LMASA-<env>-cfn-service-role`
- S3 actions scoped to `lma-artifacts-<env>-<region>` (create/put/get/list/versioning)
- `ssm:PutParameter`/`GetParameter(s)` scoped to `parameter/lma/<env>/*`
- Two account-wide, resource-less actions that have no ARN to scope to:
  `cloudformation:ValidateTemplate` (no stack exists yet during
  validation) and `s3:ListAllMyBuckets`

**2. CloudFormation service role** (`LMASA-<env>-cfn-service-role`) - what
CloudFormation itself assumes (via `--role-arn` in the Deploy stage) to
actually create resources. Trust policy
([`deploy/iam/cfn-service-role-trust-policy.json`](../../deploy/iam/cfn-service-role-trust-policy.json))
only trusts `cloudformation.amazonaws.com`, and is identical/reusable
across every environment. Permissions: the `PowerUserAccess` AWS managed
policy (everything except IAM) plus a custom inline policy
([`deploy/iam/cfn-service-role-iam-management-policy.json`](../../deploy/iam/cfn-service-role-iam-management-policy.json))
covering exactly the IAM actions this stack needs for the roles/instance
profiles/standalone managed policies it creates, scoped to
`role|instance-profile|policy/LMASA-<env>*` only.

This split matters in practice: several rounds of debugging this stack's
first real deploy were `AccessDenied` errors from resources this stack
itself creates (ECS roles, Lambda execution roles, standalone managed
policies for ECS/Logs) - each one required adding the specific missing IAM
action to the *service role's* policy, not the deployer's.

## 5. Standing up a new environment

To bring up `dev01` or `prod` (not yet done for either):

1. Create the deployer IAM user + apply its policy (copy
   `deployer-policy-qa.json` → `deployer-policy-<env>.json`, swap every
   `qa` for the new env name) and generate an access key for it.
2. Create the CloudFormation service role, attach `PowerUserAccess` +
   the custom IAM-management inline policy (`dev01`'s copy is already
   prepped at [`deploy/iam/cfn-service-role-iam-management-policy-dev01.json`](../../deploy/iam/cfn-service-role-iam-management-policy-dev01.json),
   just not yet applied via `iam put-role-policy`), using the same,
   reusable trust policy.
3. Create `deploy/params/lma-<env>.json` with at least `AdminEmail` and
   `AllowedSignUpEmailDomain` overrides (see
   [`deploy/params/lma-qa.json`](../../deploy/params/lma-qa.json) for the
   pattern - everything else uses template defaults). The 4 `NoEcho`
   secret parameters (`TavilyApiKey`, `ElevenLabsApiKey`, `SimliApiKey`,
   `ZoomMeetingSdkClientSecret`) must **never** go in as plaintext -
   reference them via CloudFormation dynamic references instead, e.g.
   `{{resolve:secretsmanager:lma/<env>/tavily-api-key}}`.
4. Add a Jenkins credential named `aws-lma-<env>` (Kind: AWS Credentials)
   using that deployer user's access key.
5. Push to the matching branch (`dev01` or `main`) - the Jenkinsfile
   already has the mapping; no pipeline code changes needed.

## 6. What gets published after every deploy

The `Export Outputs to SSM` stage runs
[`scripts/export_stack_resources_to_ssm.sh`](../../scripts/export_stack_resources_to_ssm.sh)
`<stack-name> <environment> <region> <artifact-bucket>`. It does two
distinct things:

### 6a. CloudFormation Outputs → individual SSM parameters

Every entry under the stack's `Outputs:` (root template only - nested
stack outputs have to be explicitly bubbled up to the root template to
appear here, which is exactly what several recent changes to
`lma-main.yaml` did) becomes one SSM `String` parameter at:

```
/lma/<environment>/outputs/<OutputKey>
```

This is the "curated, cheap-to-read" list meant for other systems to
consume directly - e.g. Synthera's Terraform via
`data "aws_ssm_parameter"`. As of the last `qa` deploy this is ~30
parameters covering the CloudFront URL, Cognito User Pool/Identity Pool
IDs and ARNs, the AppSync GraphQL URL, DynamoDB table names/stream ARNs,
the KMS key ARN, the Bedrock transcript Knowledge Base ID/ARN, MCP server
endpoints, and more. **See [SSM Parameter Reference](ssm-parameter-reference.md)
for the complete, current list with descriptions** - that file is the
authoritative source so this doc doesn't drift out of sync with it.

### 6b. Full resource inventory → one JSON file in S3, pointer in SSM

Every physical resource in the root stack **and every nested stack** -
Lambdas, DynamoDB tables, IAM roles, the AppSync API, ECS services,
everything - gets written to a single JSON file in S3:

```
s3://<artifact-bucket>/lma/<environment>/resource-inventory/<UTC-timestamp>/all_resources.json
```

with one SSM parameter pointing at the latest one:

```
/lma/<environment>/full-resource-inventory  →  s3://.../all_resources.json
```

This is deliberately **not** one SSM parameter per resource - a full LMA
deploy has 300+ resources across 11 nested stacks, which would blow past
SSM's per-parameter size limit (4 KB) and clutter Parameter Store with
hundreds of throwaway entries every single deploy, most of which nothing
ever looks up individually. The file itself is real CloudFormation
`describe-stack-resources` output, shaped as a JSON array with one entry
per stack (root + each nested stack):

```json
[
  {
    "nestedStackLogicalId": "__ROOT__",
    "stackName": "LMASA-qa",
    "resources": { "StackResources": [ { "LogicalResourceId": "AISTACK", "PhysicalResourceId": "arn:aws:cloudformation:...", "ResourceType": "AWS::CloudFormation::Stack", "ResourceStatus": "UPDATE_COMPLETE", ... }, ... ] }
  },
  {
    "nestedStackLogicalId": "AISTACK",
    "stackName": "arn:aws:cloudformation:us-east-1:528757797189:stack/LMASA-qa-AISTACK-.../...",
    "resources": { "StackResources": [ { "LogicalResourceId": "EventSourcingTable", "PhysicalResourceId": "LMASA-qa-AISTACK-...", "ResourceType": "AWS::DynamoDB::Table", ... }, ... ] }
  }
]
```

(confirmed on the live `qa` inventory: 314 resources total, ~214 KB file,
one array entry for the root stack plus one per nested stack.) This is
meant for humans auditing "what did this deploy actually create," not for
routine programmatic lookups - anything a downstream system needs to
reference regularly should be promoted to a real CloudFormation Output
(Section 6a) instead of being parsed out of this file.

## 7. Known gaps / not yet done

- No lint or test stage in the pipeline (see Section 3)
- `dev01` and `prod` environments are mapped but not provisioned (Section 5)
- Jenkins branch-discovery filter (`^(qa|dev01|main)$`) recommended but
  never applied - deprioritized as not urgent
- `TranscriptKnowledgeBaseId`/`Arn` outputs are conditional - only present
  if an environment was deployed with `TranscriptKnowledgeBase` set to
  create one (true for `qa` today)
