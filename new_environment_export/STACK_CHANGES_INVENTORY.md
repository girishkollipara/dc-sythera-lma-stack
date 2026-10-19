# LMA Stack — Complete Change Inventory & New-Environment Export

> **New here? Start with [PORTING_GUIDE_0.3.10.md](PORTING_GUIDE_0.3.10.md)** — the step-by-step guide
> for applying these changes to LMA 0.3.10. This file is the detailed reference behind it.

_Exported live from account **528757797189** / **us-east-1** on **2026-07-28** by `aws` CLI + boto3._
_**Refreshed 2026-10-19** with `export_live.py` (read-only) — see §0a for everything that changed since._
_Source stack: **LMASA** (`CREATE_COMPLETE`, never updated by CloudFormation), LMA **0.3.2**._

**What this is.** Every change made to this stack outside CloudFormation, in one list, together with
the live artifacts needed to recreate each one in a new environment. Everything referenced here is in
this folder.

**Read alongside:** [history/NEW_ENVIRONMENT_PLAYBOOK.md](history/NEW_ENVIRONMENT_PLAYBOOK.md) (order of
operations, traps) and [history/MAKE_CHANGES_PERMANENT.md](history/MAKE_CHANGES_PERMANENT.md) (survives-vs-wiped).
`history/` also holds the design notes and the **before** snapshots (`appsync_current/`,
`vp_fix_backups/`) — see [history/README.md](history/README.md). Paths below written as `../X` now live at `history/X`,
except `permanent_patches/` and `apply_customizations.py`, which were not kept (superseded by this export; unsafe to run).

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

## 0a. Refresh 2026-10-19 — Changes Made Live Since 2026-07-28

Found by diffing a fresh export against the 07-28 one, cross-checked against CloudTrail write events
on `LMASA` resources (people only; CloudFormation and the VPScheduler's per-meeting schedules excluded).
All were applied **live via the AWS API**, not CloudFormation — a new environment has none of them.

| # | Change | When / who | Artifact |
|---|---|---|---|
| 15 | **VP container `name-fix-v7`** — auto-leave when only bots remain (`NO_HUMANS_GRACE_SECONDS`) or nobody has spoken (`IDLE_TIMEOUT_MINUTES`); Teams badge check no longer reads a blank/missing badge as "alone" | images v2–v7 08-18 → 09-29 (yogi9, niketh) | `../container_patch/` (now v7), `ecr/describe-images.json` |
| 16 | **ECS task def `:15`** — image `name-fix-v7` + 5 new env vars (`BOT_NAME_SUFFIX`, `BOT_NAME_PREFIX`, `NO_HUMANS_GRACE_SECONDS`, `IDLE_TIMEOUT_MINUTES`, `WATCHDOG_CHECK_SECONDS`). VPScheduler `TASK_DEFINITION_ARN`, both Step Function `RunTask` states, and service `LMASA-vp-service-FARGATE` all point at `:15` | 10-07 (niketh) | `ecs/taskdef_rev15.json`, `ecs/service.json` |
| 17 | **IAM `ECSTaskExecution`** — `ecs:RunTask` now lists `:3`–`:15` (not `:5`/`:9`); `SchedulerLambdaPolicy` also gained `:10` | 08-18 → 10-07 | `iam/…VPSchedulerExecutionRole…__ECSTaskExecution.json` |
| 18 | **VP sharing rewrite** — new table `LMASA-VirtualParticipantShareIndex` + datasource; `shareVirtualParticipant` / `unshareVirtualParticipant` / `listVirtualParticipants` turned into JS pipelines (6 new functions: `ShareVirtualParticipantTransact`, `UnshareVirtualParticipantTransact`, `VirtualParticipantGetOldSharedWith`, `VPGetSharedIds`, `VPBatchGetSharedAndMerge`, `VPOwnerQueryOrAdminScan`) | 08-10 (yogi9) | `appsync/`, `dynamodb/VirtualParticipantShareIndex.json` |
| 19 | **`VirtualParticipant` table GSIs** — `OwnerIndex` (Owner, createdAt) and `MeetingIdIndex` (meetingId) | 08-10, 10-06 (yogi9) | `dynamodb/VirtualParticipant.json` |
| 20 | **Date-range meeting queries** — `EventSourcingTable` GSI `TypeDateIndex` (ItemType, SK); `createCall` now writes `ItemType="call"`; new `Query.listCallsDateRange` + `Query.getCallCount` (functions `ListCallsDateRangeQuery`, `GetCallCountQuery`); schema type `CallCount` | 08-10, 10-06 (yogi9, niketh) | `appsync/`, `dynamodb/EventSourcingTable.json` |
| 21 | **IAM inline policies on 2 stack roles** — `VirtualParticipantDataSou…__DynamoDBAccess`, `AppSyncDynamoDbRole…__dynamoDB` (access to the new table / indexes) | 08-10 (yogi9) | `iam/LMASA-AISTACK-…` |
| 22 | **AppSync `AWS_IAM` auth** added alongside Cognito, so Synthera's backend can call LMA with its own role. `createVirtualParticipant` trusts `ownerUsername`/`ownerEmail` from input **only** for IAM callers; schema input gained `ownerEmail`, `ownerUsername` | 08-03 resolver (yogi9), 10-09 API (root) | `appsync/graphql-api.json`, `appsync/resolvers/Mutation.createVirtualParticipant.json` |
| 23 | **Synthera-side IAM grants** to call LMA AppSync (roles outside LMA, reference only) | 08-03, 10-15 (yogi9) | `iam/external/` |
| 24 | **Enrichment Lambda** — project lookup now matches **any** bot of the meeting (`lma_sessions.call_id`) instead of one `lma_call_id` | 10-06 (yogi9) | `lambda/enrichment_lambda_source/project_tagger.py` |
| 25 | **ProjectMembership datasource role** `LMASA-AppSyncProjectMembershipRole` and the **`SyntheraProjectChatbot`** Lambda datasource role — both hand-made, now exported | (before 07-28, missed then) | `iam/` |
| 26 | **Project chatbot** — `Mutation.sendProjectChatMessage` → Lambda datasource `SyntheraProjectChatbot`, input `SendProjectChatMessageInput`, `SendChatMessageOutput.Sources`. Found only by diffing against upstream 0.3.2; no write-up exists | before 07-08 (missed then) | §3.3a |
| 27 | **`Mutation.addTranscriptSegment`** → pipeline (`GetCallForAddSeg` → `PutSegment`), stamps `ProjectId` on segments — live since July but missing from the 07-28 list | July (missed then) | §3.3a |

Not LMA changes, ignored: the `LMASA-qa` stacks and their SSM parameters (CloudFormation, from the
Jenkins pipeline); two AppSync test functions `TestIsolate*` (created and deleted 08-10).

**Still not captured:** row data in any table, and the `SyntheraProjectChatbot` Lambda itself (it is
Synthera code outside this stack — only its AppSync datasource role is here).

---

## 0b. Everything Else vs Stock 0.3.2 — CloudFormation Drift Detection (2026-10-19)

CloudTrail only reaches back 90 days (to ~07-21), so earlier hand changes were found by running
CloudFormation drift detection on all 12 `LMASA` stacks (compares live resources with what the 0.3.2
template deployed, all `${…}` resolved) and by diffing Lambda code against upstream tag `v0.3.2`.
Raw report: `drift/cfn_drift_2026-10-19.json`. It independently confirms the 15 resolvers in §3.3a.

★ = **not recorded anywhere before this pass.**

**IAM — 5 stack roles changed** (hand-made roles are listed in §6 / `iam/`)

| Role | Change vs 0.3.2 |
|---|---|
| `AppSyncDynamoDbRole` | + `dynamodb:BatchGetItem`, + `EventSourcingTable/index/*` (TypeDateIndex) |
| `VirtualParticipantDataSourceRole` | + `BatchGetItem`, `TransactWriteItems`, + `VirtualParticipant/index/*`, + `LMASA-VirtualParticipantShareIndex` |
| ★ `MeetingControlsResolverFunctionExecutionRole` | + `cognito-idp:ListUsers` on the user pool |
| `VPSchedulerExecutionRole` | `ecs:RunTask` `:1` → `:3,:4,:6–:8,:10–:15` |
| `VirtualParticipantSchedulerRole` | `ecs:RunTask` `:1` → `:3,:10`; + inline policy `VPSchedulerCognitoLookup` (`cognito-idp:AdminGetUser`) |

**Step Function `LMAVirtualParticipantScheduler`** — + 4 states (`Identity Choice Schedule`,
`CreateScheduleWithIdentity`, `Identity Choice Run`, `RunTaskWithIdentity`); `Time Choice` re-routed
to them; `CreateSchedule` / `RunTask` parameters changed (task def `:1` → `:15`). Matches §5.

**Lambda**

| Function | Change vs 0.3.2 | In 0.3.10? |
|---|---|---|
| `LMASA-VPScheduler` | Code: 114 → 204 lines (Cognito name lookup, identity split). Env: `TASK_DEFINITION_ARN` → `:15`, ★ **`VP_LAUNCH_TYPE` `EC2` → `FARGATE`**, + `COGNITO_USER_POOL_ID`, `INTRO_MESSAGE_TEMPLATE` | No (0.3.10 adds a `MICROVM` launch type; choose the parameter deliberately) |
| ★ `LMASA-MeetingControlsLambda` | **Code:** share-by-email now resolves each email to the Cognito username (`resolve_usernames`, needed because Entra ID usernames are GUIDs). + env `COGNITO_USER_POOL_ID`. **Timeout 60 → 180 s** | No |
| ★ `CognitoUserPoolEmailDomainVerifyFunction` | `ALLOWED_SIGNUP_EMAIL_DOMAINS` + `snaddevelopers.com` (a stack parameter in stock — set it at deploy) | n/a |
| 34 functions | + tag `Application_Or_Project=Synthera` (account tagging, 09-22 — why most `LastModified` dates are 09-22) | n/a |
| `SyntheraProjectChatbot` (not in a stack) | Hand-made, behind `sendProjectChatMessage`. Exported: `lambda/SyntheraProjectChatbot*`, role in `iam/` | n/a |

**Cognito** ★ — none of this was recorded

| Resource | Change |
|---|---|
| Identity provider **`MicrosoftEntraID`** (SAML, 05-15, not in any stack) | Corporate SSO. Metadata URL + attribute mapping in `cognito/identity-provider-MicrosoftEntraID.json` |
| User pool | + custom attributes `custom:jobtitle`, `custom:department`, `custom:manager` (filled from Entra ID) |
| Web `UserPoolClient` | Identity provider `COGNITO` → **`MicrosoftEntraID` only**; + callback/logout URLs for `dev.`/`demo.`/`dcg.syntheraai.com`, a CloudFront app, `localhost`, and a Chrome-extension URL; explicit auth flows and read attributes removed |
| `MCPServerExternalAppClient` | + `MicrosoftEntraID`; password / refresh-token auth flows removed |

**Other drift**

| Resource | Change | Note |
|---|---|---|
| ★ `EventSourcingTable` | **TTL (`ExpiresAfter`) disabled** | Meetings no longer expire — keep it disabled in the new template if that is intended |
| ★ `RecordingsBucket` | Lifecycle expiry **90 → 36 500 days** | Recordings kept ~100 years |
| ★ 50 log groups | Retention → **3 days** (were 7/14/90) | Account-wide cost setting |
| ★ Both CloudFront distributions | Minimum TLS **`TLSv1.2_2021` → `TLSv1`** | ⚠️ Security downgrade — confirm it was intentional |
| ★ VNC ALB + target group | + tag `Application_Or_Project=Synthera` | Tagging |
| `VirtualParticipant` / `EventSourcing` tables | New GSIs (§0a #19, #20) | Known |
| ECR transcriber repo, API Gateway stage | Principal / ARN written in another format | Noise — no real change |

Not covered by drift detection (no CloudFormation owner): hand-made tables, roles, Lambdas,
AppSync data sources and the Entra ID provider — those are in this export directly.

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

⚠️ `DOCDB_PASS` is a **live plaintext credential** in this Lambda's environment. Since the 10-19
refresh it is redacted in `lambda/LMASA-MeetingEnrichment.config.json` too — but the 07-28 commit
pushed it, so it is **still in git history**. Rotate it and give the new environment a Secrets
Manager reference instead of copying it forward. Same for `MCPServerClientSecret` (was in
`cfn_root_outputs.json` / `cfn_root_stack.json`).

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

### 3.3a Complete AppSync diff vs stock LMA 0.3.2 (verified 2026-10-19)

§3.2/§3.3 above were written on 07-28 and are **incomplete**. This list comes from comparing the
live API against upstream tag `v0.3.2` (`lma-ai-stack.yaml` + `source/appsync/`) field by field —
kind, data source, pipeline, request/response templates, JS code — and cross-checking against the
07-08 snapshot in `history/appsync_current/`. 48 of 67 resolvers are byte-identical to stock
(`Mutation.shareMeetings` differs only by build-stripped comments).

**Resolvers — 15 modified, 3 added**

| Resolver | Change | Feature | When |
|---|---|---|---|
| `Query.getCall` | UNIT → PIPELINE | Project access | 07 |
| `Query.getTranscriptSegments` | UNIT → PIPELINE | Project access | 07 |
| `Query.getTranscriptSegmentsWithSentiment` | UNIT → PIPELINE | Project access | 07 |
| `Query.listCalls` | UNIT → PIPELINE | Project access | 07 |
| `Query.listCallsDateHour` | UNIT → PIPELINE | Project access | 07 |
| `Query.listCallsDateShard` | UNIT → PIPELINE | Project access | 07 |
| `Mutation.addTranscriptSegment` | UNIT → PIPELINE (`GetCallForAddSeg` → `PutSegment`) — stamps `ProjectId` on segments. **Missing from the 07-28 list** | Project access | 07 |
| `Subscription.onAddTranscriptSegment` | UNIT → PIPELINE (`GetCallForSub`) | Live-subscription fix | 07-21 |
| `Subscription.onUpdateCall` | UNIT → PIPELINE (`GetCallForSub`) | Live-subscription fix | 07-21 |
| `Subscription.onCreateCall` | UNIT → PIPELINE, owner/shared only | Live-subscription fix | 07-21 |
| `Mutation.createVirtualParticipant` | `ownerEmail` passthrough (07-20), then IAM-caller branch trusting `ownerUsername`/`ownerEmail` (08-03) | VP display name, Synthera backend | 07-20, 08-03 |
| `Mutation.createCall` | writes `ItemType = "call"` | Date-range list | 08-10 |
| `Mutation.shareVirtualParticipant` | VTL UNIT → JS PIPELINE | VP sharing | 08-10 |
| `Mutation.unshareVirtualParticipant` | VTL UNIT → JS PIPELINE | VP sharing | 08-10 |
| `Query.listVirtualParticipants` | VTL Scan → JS PIPELINE (owner index / share index) | VP sharing | 08-10 |
| `Mutation.sendProjectChatMessage` | **NEW** — invokes Lambda `SyntheraProjectChatbot`. **Undocumented anywhere; predates 07-08** | Project chatbot | < 07-08 |
| `Query.listCallsDateRange` | **NEW** (with a `projectId` filter upstream lacks) | Date-range list | 08-10, 10-06 |
| `Query.getCallCount` | **NEW** | Date-range list | 08-10 |

**Pipeline functions — 20, all new** (stock 0.3.2 has none)

| Feature | Functions |
|---|---|
| Project access (12, July) | `GetUserProjects`, `GetUserProjectsJS`, `GetCallAuth`, `GetCallForSeg`, `GetCallForAddSeg`, `GetCallForSub`, `ListCallsQuery`, `ListCallsDateHourQuery`, `ListCallsDateShardQuery`, `PutSegment`, `QuerySegments`, `QuerySegmentsSentiment` |
| Date-range list (2, 08-10) | `ListCallsDateRangeQuery` (updated 10-06), `GetCallCountQuery` |
| VP sharing (6, 08-10) | `ShareVirtualParticipantTransact`, `UnshareVirtualParticipantTransact`, `VirtualParticipantGetOldSharedWith`, `VPGetSharedIds`, `VPBatchGetSharedAndMerge`, `VPOwnerQueryOrAdminScan` |

**Data sources — 3 added:** `ProjectMembership` (DynamoDB), `VirtualParticipantShareIndex` (DynamoDB),
`SyntheraProjectChatbot` (Lambda, outside this stack).

**Schema — vs 0.3.2**

| Added | Feature |
|---|---|
| `Call` + `CallListItem`: `OwnerEmail`, `OwnerName`, `SharedWithEmails`, `SharedWithNames` | Display names |
| `CreateVirtualParticipantInput`: `ownerEmail`, `ownerUsername` | VP display name / Synthera backend |
| `type CallCount`; `Query.getCallCount`; `Query.listCallsDateRange(…, projectId)` | Date-range list |
| `input SendProjectChatMessageInput`; `Mutation.sendProjectChatMessage`; `SendChatMessageOutput.Sources` | Project chatbot |

**API setting:** `AWS_IAM` added as an additional auth provider (10-09).

**For the 0.3.10 port:** upstream 0.3.10 already ships `TypeDateIndex`, `listCallsDateRange`,
`getCallCount` and `AWS_IAM` auth — re-add only the `projectId` filter. Everything else above is ours.

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

> **10-19:** the live revision is now `:15` (`name-fix-v7`); `taskdef_rev4.json` was replaced by
> `taskdef_rev15.json`. Between `:3` and `:15` the image **and 5 auto-leave env vars** differ (§0a #16).
> Everything below about `rev4` applies to `rev15`.
>
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

## 9. Container Patch — Closed 2026-10-19

Recovered from the images into `../container_patch/` and updated to `name-fix-v7` (base, v1 and v7
pulled and diffed; v7 = v1 + `details.js`, `scribe.js`, `teams.js`). Each changed file has a `.orig`
(stock 14-May) and a `.patch`. `extract_container_patch.sh` now targets v7. The original notes follow.

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
