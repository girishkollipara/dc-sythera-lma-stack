# LMA Stack — Complete Change Inventory & New-Environment Export

_Exported live from account **528757797189** / **us-east-1** on **2026-07-28** by `aws` CLI + boto3._
_Source stack: **LMASA** (`CREATE_COMPLETE`), LMA **0.3.2**._

**What this is.** Every change made to this stack outside CloudFormation, in one list, together with
the live artifacts needed to recreate each one in a new environment. Everything referenced here is in
this folder.

**Read alongside:** [../NEW_ENVIRONMENT_PLAYBOOK.md](../NEW_ENVIRONMENT_PLAYBOOK.md) (order of
operations, traps) and [../MAKE_CHANGES_PERMANENT.md](../MAKE_CHANGES_PERMANENT.md) (survives-vs-wiped).

> **The rule that governs all of it:** every change below was applied **live via the AWS API**, never
> through CloudFormation. A stock deploy of a new environment contains **none** of them — they are
> absent, not reverted.

---

## 0. Export Health — What This Run Verified

Checks run against live during the export. All four passed:

| Check | Result |
|---|---|
| VPScheduler live code vs `permanent_patches/lambda/vpscheduler_index.py` | **identical** — no drift |
| Live AppSync schema vs saved `schema.graphql` | **identical** (ignoring whitespace) |
| 10 saved resolvers + 12 saved functions still present live | **all present**, none missing |
| Patched ECR image `sha256:58a82af8…` with tags `name-fix-v1` + `latest` | **present** |

The saved artifacts in `permanent_patches/` were accurate as of this export. Two gaps were **closed**
by this run (§8), one remains **open** (§9).

---

## 1. The Change List

Fourteen changes. ❌ = a new environment will not have it; ✅ = data, recreated by backfill/migration.

| # | Change | Type | Artifact in this export | New env? |
|---|---|---|---|---|
| 1 | **VPScheduler code** — Cognito identity resolution, 3-variable name split | Lambda | `lambda/LMASA-VPScheduler_src/index.py` (204 ln) | ❌ |
| 2 | **VPScheduler env** — `COGNITO_USER_POOL_ID`, `INTRO_MESSAGE_TEMPLATE`, `TASK_DEFINITION_ARN`→`:4` | Lambda cfg | `lambda/LMASA-VPScheduler.config.json` | ❌ |
| 3 | **IAM `VPSchedulerCognitoLookup`** — inline policy on the scheduler Lambda role | IAM | `iam/…VirtualParticipantSchedul-7TrZcy7KmFtq__VPSchedulerCognitoLookup.json` | ❌ |
| 4 | **IAM `ECSTaskExecution`** — `ecs:RunTask` grant extended to revision `:4` | IAM | `iam/…VPSchedulerExecutionRole-X4Ig7X8qtTtT__ECSTaskExecution.json` | ❌ |
| 5 | **ECR patched image** — name-typing fix; `latest` retagged to it | Container | `ecr/describe-images.json` + `extract_container_patch.sh` | ❌ (new repo empty) |
| 6 | **ECS task def `:4`** — cloned from `:3`, image → `name-fix-v1` | ECS | `ecs/taskdef_rev4.json` (and `rev3` for diff) | ❌ |
| 7 | **Step Function `LMA_IDENTITY`** — 4 added states | Step Fn | `stepfunction/definition_only.json` | ❌ |
| 8 | **`Mutation.createVirtualParticipant`** — `ownerEmail` passthrough | AppSync | `appsync/resolvers/Mutation.createVirtualParticipant.json` | ❌ |
| 9 | **Display-name backfill + Owner repair** (450 rows / 18 rows) | Data | `../backfill_display_names.py` | ✅ re-run |
| 10 | **AppSync schema** — 4 display fields on 2 types | AppSync | `appsync/schema.graphql` | ❌ |
| 11 | **Enrichment Lambda** + role + **filtered** stream mapping | Lambda | `lambda/enrichment_lambda_source/` (355 ln) | ❌ |
| 12 | **Project-access** — 10 resolvers + pipeline functions | AppSync | `appsync/resolvers/` + `appsync/functions/` | ❌ |
| 13 | **Live-subscription fix** + `GetCallForSub` | AppSync | `appsync/functions/GetCallForSub.json` | ❌ |
| 14 | **`ProjectId` tags + membership rows** (30 rows) | Data | `dynamodb/ProjectMembership.json` | ✅ migrate |

---

## 2. Lambdas (3 changed)

| Function | Role | Runtime | Code |
|---|---|---|---|
| `LMASA-VPScheduler` | `LMASA-VIRTUALPARTICIPANTS-VirtualParticipantSchedul-7TrZcy7KmFtq` | python3.12 | 3.5 KB — **stack-owned, inline in template** |
| `LMASA-MeetingEnrichment` | `LMASA-MeetingEnrichmentRole` | python3.12 | 3.9 MB — **standalone, no stack owns it** |
| `LMASA-MeetingControlsLambda` | `LMASA-AISTACK-4VKHCQQY3CJ-MeetingControlsResolverFu-PvrlHcZGV5g5` | python3.12 | 4.5 MB (vendored `cryptography`) |

Each has `<name>.config.json` (full configuration), `<name>.zip` (deployment package) and
`<name>_src/` (unzipped) in `lambda/`.

### 2.1 VPScheduler environment — which values are environment-specific

Copy the **template**; re-derive the rest. From `lambda/LMASA-VPScheduler.config.json`:

| Variable | New environment |
|---|---|
| `COGNITO_USER_POOL_ID` = `us-east-1_pOCRCo1Wx` | **rewrite** — new pool |
| `TASK_DEFINITION_ARN` = `…VirtualParticipantTaskDef:4` | **rewrite** — new family + the revision you register |
| `SCHEDULER_ROLE_ARN`, `CLUSTER_ARN`, `SUBNETS`, `SECURITY_GROUPS`, `RECORDINGS_BUCKET_NAME`, `CALL_DATA_STREAM_NAME`, `GRAPHQL_ENDPOINT`, `VP_TASK_REGISTRY_TABLE_NAME` | **rewrite** — all environment-specific |
| `INTRO_MESSAGE_TEMPLATE` ("Dimcon AI Meeting Assistant… `{LMA_USER}`") | **keep verbatim** — the only env-agnostic customization |
| `SCHEDULE_GROUP_NAME`, `CONTAINER_NAME`, `VP_LAUNCH_TYPE` | stock values |

### 2.2 Enrichment Lambda — recovered this run

Three hand-written files (the rest of the zip is vendored `pymongo`/`bson`/`dns`/`gridfs`):

```
lambda/enrichment_lambda_source/index.py           174 lines
lambda/enrichment_lambda_source/mongo_client.py     57 lines
lambda/enrichment_lambda_source/project_tagger.py  124 lines
```

**Its event-source mapping is the important part** — `enrichment_event_source_mappings.json`:

```
EventSourceArn : …/EventSourcingTable-V49M8Y0JQODE/stream/2026-05-14T12:00:02.797
FilterCriteria : eventName=INSERT AND PK prefix "c#"
                 eventName=INSERT AND PK prefix "cls#"
```

Those two filters are what stop the invocation storm from transcript segments
(verification test 7). Recreate the mapping **with** them.

⚠️ The stream ARN ends in a creation timestamp — it cannot be constructed by hand. Read it from the
new table (playbook trap 6).

⚠️ `DOCDB_PASS` is a **live plaintext credential** in this Lambda's environment. It is redacted from
this document but present in `lambda/LMASA-MeetingEnrichment.config.json`. Rotate it and give the new
environment a Secrets Manager reference instead of copying it forward.

---

## 3. AppSync — API `lo3gpjswjjdqrh6xn5xkgipeme`

Full live dump: **65 resolvers**, **12 pipeline functions**, **21 data sources**, schema.

```
appsync/schema.graphql            the live SDL
appsync/data-sources.json         21 datasources
appsync/functions/*.json          12 pipeline functions (code embedded)
appsync/resolvers/*.json          65 resolvers (code embedded)
appsync/_functions_index.json     name → functionId, datasource, runtime
appsync/_resolvers_index.json     field → kind, datasource, pipeline order, runtime
```

### 3.1 Schema change — 4 fields, on 2 types

```graphql
OwnerEmail: String
OwnerName: String
SharedWithEmails: [String]
SharedWithNames: [String]
```

### 3.2 The 12 pipeline functions

`GetCallAuth`, `GetCallForAddSeg`, `GetCallForSeg`, **`GetCallForSub`**, `GetUserProjects`,
`GetUserProjectsJS`, `ListCallsQuery`, `ListCallsDateHourQuery`, `ListCallsDateShardQuery`,
`PutSegment`, `QuerySegments`, `QuerySegmentsSentiment`.

### 3.3 The 10 modified resolvers

`Mutation.createVirtualParticipant`, `Query.getCall`, `Query.listCalls`, `Query.listCallsDateHour`,
`Query.listCallsDateShard`, `Query.getTranscriptSegments`, `Query.getTranscriptSegmentsWithSentiment`,
`Subscription.onAddTranscriptSegment`, `Subscription.onCreateCall`, `Subscription.onUpdateCall`.

### 3.4 Why this half is easy

These artifacts contain **no account IDs, API IDs, table names or ARNs** — resolvers reference only
logical datasource names (`CallEventSourcing`, `VirtualParticipantTable`, `ProjectMembership`). They
port to a new environment **unchanged**.

**One prerequisite.** The `ProjectMembership` datasource does not exist in a stock deploy. Create, in
this order, *before* applying any resolver:

1. DynamoDB table `LMASA-ProjectMembership` — `PK` only (see `dynamodb/ProjectMembership.json`)
2. AppSync datasource named exactly **`ProjectMembership`**
3. Its service role, with `dynamodb:GetItem` on that table

Otherwise `create_function` fails on the first project-access function.

---

## 4. ECS + ECR

| Revision | Image tag | Env vars |
|---|---|---|
| `:3` | `latest` | 50 |
| `:4` | `name-fix-v1` | 50 |

ECR repo `lmasa-virtualparticipantstack-1iavizt1439r2-imagerepo-l3yrnekcbuxx`, 6 images:

| Digest | Tags |
|---|---|
| `sha256:32cf092c…` | `pre-name-fix-20260514` — rollback point |
| `sha256:58a82af8…` | `name-fix-v1`, `latest` — **the patched image** |
| 4 others | untagged |

> ### ⚠️ Do not register `ecs/taskdef_rev4.json` in a new environment
>
> It carries **50 environment variables from this environment** — ECR image URI,
> `VNC_TARGET_GROUP_ARN`, Kinesis stream names, table names, endpoints. Registered elsewhere, the
> container faithfully writes transcripts back into **this** environment. Failure is silent.
>
> **Correct procedure:** start from the *new* environment's own `:1` and change **only** the `image`
> field. `rev3` and `rev4` are here to **diff** (`image` is the sole difference) — not to replay.

### 4.1 Copy the image, do not rebuild

```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <old-acct>.dkr.ecr.us-east-1.amazonaws.com
docker pull <old-repo>:name-fix-v1
docker tag  <old-repo>:name-fix-v1 <new-repo>:name-fix-v1
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <new-acct>.dkr.ecr.us-east-1.amazonaws.com
docker push <new-repo>:name-fix-v1
```

A rebuild re-rolls Chromium and Node (`apk add chromium`, `FROM node:20-alpine` are unpinned).
Verified-good pair: **Chromium 148.0.7778.96 / Node v20.20.2**. Cross-account also needs a repository
policy on the source repo allowing the destination principal to pull.

---

## 5. Step Function — `LMASA-LMAVirtualParticipantScheduler`

12 states live. The 4 added by this project, all present and verified:

`Identity Choice Schedule` · `CreateScheduleWithIdentity` · `Identity Choice Run` · `RunTaskWithIdentity`

`stepfunction/definition_only.json` is the extracted ASL; `LMAVirtualParticipantScheduler.json` is
the full `describe-state-machine` response.

> **Regenerate, do not copy.** The saved definition is this environment's full machine with this
> environment's ARNs. Take the *new* environment's stock definition, re-apply the four states, and
> point `TaskDefinition` / `TaskDefinitionArn` at the revision you registered.

**Keep the `IsPresent` guards.** The machine runs in **JSONPath** mode, where a missing path throws
`States.Runtime` and **fails the execution**. An unguarded `"Value.$": "$.data.userDisplayName"`
kills Join Now entirely until the frontend ships that field.

---

## 6. IAM — 3 roles, 4 inline policies

| Role | Inline policies | Rewrite for a new environment |
|---|---|---|
| `…VirtualParticipantSchedul-7TrZcy7KmFtq` | `SchedulerLambdaPolicy` (stock), **`VPSchedulerCognitoLookup`** (added) | user-pool ARN → new account + pool |
| `…VPSchedulerExecutionRole-X4Ig7X8qtTtT` | **`ECSTaskExecution`** (modified) | `ecs:RunTask` resources → new account + family + revision |
| `LMASA-MeetingEnrichmentRole` | **`EnrichmentPolicy`** (added) | table ARN + stream ARN |

Managed policies on the scheduler role: `AWSLambdaBasicExecutionRole`, `AWSXRayDaemonWriteAccess`.

### 6.1 Trap — the `RunTask` grant is pinned to revision numbers

Currently granted: `…TaskDef:3`, `:3:*`, `:4`, `:4:*` — consistent with `TASK_DEFINITION_ARN` being
`:4`, so this environment is correct today.

But the grant is **per-revision**. A test meeting once produced **no bot at all**: the schedule fired,
tried to run a revision the policy did not list, was denied, and deleted itself — no error surfaced
anywhere. **Every time you register a revision, update this policy.**

---

## 7. Data — DynamoDB

| Table | Keys | Items | Note |
|---|---|---|---|
| `LMASA-AISTACK-4VKHCQQY3CJR-EventSourcingTable-V49M8Y0JQODE` | `PK`, `SK` | 43,484 | stream ARN in `dynamodb/EventSourcingTable.json` |
| `LMASA-ProjectMembership` | `PK` | 30 | **standalone** — no stack owns it |

CloudFormation never touches table *contents*, so this data survives a redeploy. A **new environment
starts with empty tables** — re-run `../backfill_display_names.py` and re-create membership rows.

Table *definitions* are exported here; **row data is not**. If the new environment needs the existing
meetings, do a separate export/import (or DynamoDB point-in-time restore to the new account).

---

## 8. Gaps Closed by This Export

| Gap (was open in the playbook §9) | Status |
|---|---|
| `permanent_patches/lambda/enrichment_lambda/` missing — source existed **only inside the live Lambda** | ✅ **Closed.** Recovered to `lambda/enrichment_lambda_source/` |
| Enrichment stream-mapping filters undocumented | ✅ **Closed.** `enrichment_event_source_mappings.json` |
| `permanent_patches/` possibly stale vs live | ✅ **Verified current** (§0) |

---

## 9. Gap Still Open — Container Patch

`vp_fix_backups/container_patch/` is referenced by three documents but **does not exist in the repo**.
`name-entry.js` and the patched `teams.js` / `zoom.js` survive **only inside the ECR image**.

`extract_container_patch.sh` in this folder recovers them — **it requires Docker Desktop to be
running**, which it was not at export time. Run:

```bash
./extract_container_patch.sh
```

It pulls `name-fix-v1` and `pre-name-fix-20260514`, extracts the three files plus `.orig`
counterparts, and writes unified diffs. Expected result, per VP_CHANGES_APPLIED.md §2.6:

```
teams.js      2 lines changed (1 import, 1 call)
zoom.js       2 lines changed (1 import, 1 call)
name-entry.js new file
```

The fix retypes the display name until it reads back correctly, up to 6 attempts, then joins anyway —
a wrong name beats a lost recording.

**Until this runs, the image is the only copy.** If ECR is ever cleaned, the fix is unrecoverable
without re-deriving it.

Remaining from playbook §9: `DOCDB_PASS` in plaintext (§2.2), script constants hardcoded, no
`publish.sh` tooling set up, `webex.ts` / `chime.ts` still type blind.

---

## 10. Constants to Replace

`../apply_customizations.py` [lines 31–37](../apply_customizations.py#L31-L37):

| Constant | This environment | Where to get the new value |
|---|---|---|
| `REGION` | `us-east-1` | your choice |
| `API_ID` | `lo3gpjswjjdqrh6xn5xkgipeme` | `aws appsync list-graphql-apis` |
| `POOL_ID` | `us-east-1_pOCRCo1Wx` | `aws cognito-idp list-user-pools --max-results 20` |
| `ECR_REPO` | `lmasa-virtualparticipantstack-1iavizt1439r2-imagerepo-l3yrnekcbuxx` | `aws ecr describe-repositories` |
| `TASKDEF_FAMILY` | `LMASA-VIRTUALPARTICIPANTSTACK-1IAVIZT1439R2VirtualParticipantTaskDef` | `aws ecs list-task-definition-families` |
| `EVENTSOURCING_TABLE` | `LMASA-AISTACK-4VKHCQQY3CJR-EventSourcingTable-V49M8Y0JQODE` | AISTACK resource `EventSourcingTable` |
| `SFN_ARN` | `arn:aws:states:us-east-1:528757797189:stateMachine:LMASA-LMAVirtualParticipantScheduler` | `aws stepfunctions list-state-machines` |

**Also:** the script calls `LMASA-VPScheduler` and `LMASA-MeetingControlsLambda` **by literal name**.
These are `${AWS::StackName}-…`, so they resolve **only if the new stack is also named `LMASA`**.
Name it differently and every Lambda call fails — keep the name or parameterise it.

Account `528757797189` also appears throughout `iam/`, `ecs/`, `stepfunction/` and
`lambda/*.config.json` in this export.

---

## 11. Order of Operations

```
 1. stock CloudFormation deploy — all nested stacks CREATE_COMPLETE
 2. create ProjectMembership table + datasource + role            (§3.4)
 3. copy the patched image into the new ECR repo                  (§4.1)
 4. register a task def: new env's :1 + image swapped ONLY        (§4 warning)
 5. rewrite the script constants + IAM/env artifacts              (§10, §6)
 6. python apply_customizations.py          → DRY RUN, read every line
 7. python apply_customizations.py --apply
 8. regenerate + apply the Step Function                          (§5)
 9. recreate the Enrichment Lambda + role + FILTERED mapping      (§2.2)
10. run backfill_display_names.py if data was migrated            (§7)
11. deploy the frontend with userDisplayName                      (../FRONTEND_CHANGES_REQUIRED.md)
12. verification                                                  (§12)
```

Steps 2–4 **must** precede step 7. The script does not create datasources, does not copy images, and
will happily register a cross-environment task definition if you let it.

---

## 12. Verification

Do not declare done on a green `apply` summary.

| # | Test | Pass condition |
|---|---|---|
| 1 | `apply_customizations.py` dry run, after applying | all "already correct" except the VPScheduler code re-write, which always runs |
| 2 | Bot joins a **Teams** meeting | participant list shows `<First>'s AI Assistant`, **untruncated** |
| 3 | Same on **Zoom** | same |
| 4 | Container log | `Display name entered correctly on attempt N` — proves the patched image is running |
| 5 | **Join Now** from the extension | bot joins; name matches test 2 |
| 6 | Meetings list in the web UI | `OwnerName` / `OwnerEmail` populated, no `microsoftentraid_…` GUIDs |
| 7 | Create a meeting, watch the table | enrichment writes display fields in seconds; **no** invocation storm from transcript segments |
| 8 | Live transcript subscription | segments stream in |
| 9 | Project access, two users | each sees only their projects' meetings |
| 10 | `describe-task-definition` on the registered revision | image is `name-fix-v1`; **every other field matches the new environment** |
| 11 | Redeploy the stack, re-run the script | environment returns to correct |

**Test 10 is the one that catches the cross-environment task-definition trap**, and it is the most
expensive mistake to find late.

---

## 13. Folder Manifest

```
new_environment_export/                    882 files · 34 MB
├── STACK_CHANGES_INVENTORY.md             this file
├── extract_container_patch.sh             recovers the container fix (needs Docker running)
├── cfn_root_stack.json                    stack params + status
├── cfn_root_outputs.json                  27 stack outputs
├── lambda/
│   ├── LMASA-VPScheduler.{config.json,zip}      + _src/index.py           204 ln
│   ├── LMASA-MeetingEnrichment.{config.json,zip} + _src/
│   ├── LMASA-MeetingControlsLambda.{config.json,zip} + _src/
│   ├── enrichment_lambda_source/          index.py, mongo_client.py, project_tagger.py
│   └── enrichment_event_source_mappings.json     ← the 2 stream filters
├── appsync/
│   ├── schema.graphql · data-sources.json (21)
│   ├── functions/ (12) · resolvers/ (65)
│   └── _functions_index.json · _resolvers_index.json
├── ecs/         taskdef_rev3.json · taskdef_rev4.json      ⚠️ reference only
├── ecr/         describe-images.json (6 images)
├── stepfunction/ LMAVirtualParticipantScheduler.json · definition_only.json
├── iam/         3 roles + 4 inline policies
└── dynamodb/    EventSourcingTable.json · ProjectMembership.json   (schema only, no rows)
```

---

## 14. Bottom Line

- CloudFormation reads **S3, never GitHub**. A stock deploy = stock LMA 0.3.2 = none of §1 exists.
- The AppSync half (§3) **ports unchanged**. Everything else embeds account `528757797189` and needs
  rewriting.
- Two artifacts are **reference-only, never replay**: `ecs/taskdef_rev4.json` (§4) and the Step
  Function definition (§5).
- One gap remains open: the container patch (§9) — run `extract_container_patch.sh` with Docker up.
- Replaying this export gets a new environment live but leaves you owning the drift forever. Forking
  LMA at tag 0.3.2 and baking these changes into the templates is the only thing that makes "create a
  new environment" a one-step operation — this document is the checklist for that fork.
