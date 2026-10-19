# New Environment — Playbook

_Written: 2026-07-27 · Source environment: account 528757797189 · us-east-1 · LMA 0.3.2_

**What this document is:** what to check, in what order, when standing up a **new** LMA environment,
and how to make this environment's customizations exist in it.

**When you do _not_ need this document:** a redeploy of the *same* stack. Physical resource IDs are
unchanged there — deploy, then run `python apply_customizations.py --apply`. Done. See
[MAKE_CHANGES_PERMANENT.md](MAKE_CHANGES_PERMANENT.md).

> **The one-line summary:** nothing carries over to a new environment automatically. A stock deploy
> produces stock LMA 0.3.2 — the GUID bug and the name truncation both return. Either you adapt the
> re-apply script to the new IDs (§5, fast, keeps the drift problem) or you fork and bake the changes
> into source (§6, slower once, permanent forever).

---

## 1. How an Environment Is Actually Built

Understanding this answers most of the questions below.

```
GitHub  aws-samples/amazon-transcribe-live-meeting-assistant
   │
   │  ./publish.sh <bucket> <prefix> <region>      ← a human runs this, once
   ▼
S3 artifact bucket    lma-main.yaml + nested templates + zipped source
   │
   │  you hand CloudFormation the template URL
   ▼
CloudFormation creates the stack
   ├── Lambdas           VPScheduler's Python is INLINE in the template (`Code: ZipFile:`)
   ├── AppSync API       → new API id
   ├── Cognito pool      → new pool id
   ├── DynamoDB tables   → new names, random suffixes
   ├── ECR repo          → new repo, EMPTY
   └── CodeBuild project → pulls the VP source zip from that S3 bucket,
                           builds the container, pushes it as `:latest`
```

**CloudFormation never talks to GitHub.** Its input is always S3. A stock deploy points at AWS's
public artifact bucket, so CodeBuild builds the VP container from **Amazon's unmodified 0.3.2
source** — which contains neither the identity resolution nor the name-typing fix.

Two consequences that drive everything else:

| | |
|---|---|
| **New env = stock LMA** | Every ❌ row in [MAKE_CHANGES_PERMANENT.md §1](MAKE_CHANGES_PERMANENT.md) is absent, not reverted — absent |
| **A fork changes the source of truth** | Point CloudFormation at *your* bucket and the customizations are created **by** the stack |

---

## 2. Choose the Approach Before You Deploy Anything

| | **A — Stock + adapted script** | **B — Fork and bake** |
|---|---|---|
| Effort | ~half a day per environment | 2–4 days once, then ~0 |
| New env comes up correct? | No — deploy, then run the script | **Yes** |
| Container typing fix | Copy the image between ECR repos (§5.4) | Built from your source |
| Survives CodeBuild re-runs | ❌ silently reverts `:latest` | ✅ your source *is* the build |
| Survives the next CFN deploy | ❌ re-run the script every time | ✅ |
| Drift from CloudFormation | Yes, permanently | None |
| Works across accounts/regions | Only after editing every ARN | Yes, by parameter |

**Recommendation — same as [MAKE_CHANGES_PERMANENT.md §4](MAKE_CHANGES_PERMANENT.md): do A to get the
new environment live, then do B and retire the script.** If the new environment is meant to be
long-lived or is one of several, go straight to B — A's cost is per-environment and recurring.

---

## 3. Pre-Flight — Do This **Before** Touching the New Environment

These items can only be captured from the **currently live** environment. If it is torn down first,
they are gone.

| # | Check | Command / where | Why it matters |
|---|---|---|---|
| 1 | **Export the Enrichment Lambda source** | `aws lambda get-function --function-name LMASA-MeetingEnrichment --query Code.Location` → download → unzip into `permanent_patches/lambda/enrichment_lambda/` | ⚠️ **This directory does not exist in the repo.** [apply_customizations.py:346](apply_customizations.py#L346) tells you to recreate the Lambda from it. The code exists **only inside the live Lambda** today |
| 2 | **Confirm the patched image is still in ECR** | `aws ecr describe-images --repository-name lmasa-virtualparticipantstack-1iavizt1439r2-imagerepo-l3yrnekcbuxx --image-ids imageTag=name-fix-v1` | Must match `sha256:58a82af8…` (`permanent_patches/ecs/ecr_digests.json`). If it is gone, rebuild from `vp_fix_backups/container_patch/` — and expect a Chromium/Node re-roll ([VP_CHANGES_APPLIED.md §2.6](VP_CHANGES_APPLIED.md)) |
| 3 | **Re-export live AppSync state** | boto3 dump of resolvers + functions + schema | Confirms `permanent_patches/appsync/` still matches live; any patch applied after 2026-07-22 is not in there |
| 4 | **Confirm the current LMA version** | stack parameters / `publish.sh` version | The fork must start at the **same tag (0.3.2)**. Upstream `main` migrated Puppeteer → Playwright — its fix cannot be dropped into 0.3.2 |
| 5 | **Record MongoDB/DocumentDB reachability** | `permanent_patches/lambda/enrichment_config.json` → `DOCDB_HOST` | The new environment's VPC must reach it, or the enrichment Lambda fails silently |
| 6 | **Rotate the DocumentDB credential** | same file — `DOCDB_PASS` is stored in plaintext | It is a real credential in a repo file. Give the new environment a Secrets Manager reference instead of copying it forward |
| 7 | **Dry-run the script against the live env** | `python apply_customizations.py` | Should report everything "already correct". If it wants to change something, the artifacts are stale — fix that first, or you will port stale state |

---

## 4. What Carries Over, and What Does Not

| Category | Into a **redeploy** | Into a **new environment** | Note |
|---|---|---|---|
| DynamoDB data (names, tags, memberships, Owner repair) | ✅ untouched | ❌ **empty tables** | Backfill must be re-run: [backfill_display_names.py](backfill_display_names.py) |
| `LMASA-ProjectMembership` table + rows | ✅ standalone | ❌ | Table, rows, AppSync datasource and its IAM role all need creating |
| AppSync resolvers / functions / schema | ❌ wiped | ❌ absent | **Artifacts are portable** — see §5.3 |
| VPScheduler code + env | ❌ wiped | ❌ absent | Code is inline in the template |
| IAM inline policies | ❌ wiped | ❌ absent | ARNs inside must be rewritten |
| Step Function `LMA_IDENTITY` branches | ❌ wiped | ❌ absent | ARNs inside must be rewritten |
| ECR patched image | ✅ persists | ❌ **new repo is empty** | Copy it across (§5.4) |
| ECS task def `:4` | ✅ persists (may be unreferenced) | ❌ absent | **Do not register the saved JSON** — §7 trap 1 |
| Enrichment Lambda + role + stream mapping | ✅ standalone | ❌ absent | Blocked on pre-flight item 1 |
| Frontend `userDisplayName` change | n/a | n/a | [FRONTEND_CHANGES_REQUIRED.md](FRONTEND_CHANGES_REQUIRED.md) — deploy per environment |

---

## 5. Approach A — Stock Deploy + Adapted Script

### 5.1 Deploy stock, then collect the new IDs

Deploy the stock template and wait for `CREATE_COMPLETE` on **all** nested stacks. Then collect:

| Constant | Where it is hardcoded | How to get the new value |
|---|---|---|
| `REGION` | [apply_customizations.py:31](apply_customizations.py#L31) | your choice |
| `API_ID` | [:32](apply_customizations.py#L32) | `aws appsync list-graphql-apis` |
| `POOL_ID` | [:33](apply_customizations.py#L33) | `aws cognito-idp list-user-pools --max-results 20` |
| `ECR_REPO` | [:34](apply_customizations.py#L34) | `aws ecr describe-repositories` |
| `TASKDEF_FAMILY` | [:35](apply_customizations.py#L35) | `aws ecs list-task-definition-families` |
| `EVENTSOURCING_TABLE` | [:36](apply_customizations.py#L36) | AISTACK resources, logical id `EventSourcingTable` |
| `SFN_ARN` | [:37](apply_customizations.py#L37) | `aws stepfunctions list-state-machines` |

Also check the **Lambda names**: the script calls `LMASA-VPScheduler` and
`LMASA-MeetingControlsLambda` by literal name. These are `${AWS::StackName}-…`, so they match **only
if the new stack is also named `LMASA`**. Name it differently and every Lambda call fails —
either keep the name or parameterise it.

### 5.2 Rewrite the artifacts that embed ARNs

Account `528757797189` appears in these seven files. Every one needs editing:

| File | What to change |
|---|---|
| `iam/VPSchedulerCognitoLookup.json` | user-pool ARN → new account + new pool |
| `iam/SchedulerExec_ECSTaskExecution.json` | `ecs:RunTask` resources → new account + new task-def family + the revision you actually register |
| `iam/MeetingEnrichmentPolicy.json` | table ARN + stream ARN |
| `lambda/vpscheduler_env_overrides.json` | `COGNITO_USER_POOL_ID`, `TASK_DEFINITION_ARN`. `INTRO_MESSAGE_TEMPLATE` is env-agnostic — keep it |
| `lambda/enrichment_config.json` | `TABLE_NAME`, `USER_POOL_ID`, `mapping.EventSourceArn` (**the stream ARN contains a timestamp — must be read from the new table**), DocDB credentials |
| `ecs/taskdef.json` | **do not edit — regenerate.** §7 trap 1 |
| `stepfunction/LMAVirtualParticipantScheduler.json` | **do not edit — regenerate.** §5.5 |

### 5.3 AppSync — the portable half

Verified by grep: `permanent_patches/appsync/` contains **no** account IDs, API IDs, table names or
ARNs. Resolvers and functions reference only logical datasource names — `CallEventSourcing`,
`VirtualParticipantTable`, `ProjectMembership`. They port unchanged.

**One prerequisite:** the `ProjectMembership` datasource does not exist in a stock deploy. Before
running the script, create — in this order:

1. DynamoDB table `LMASA-ProjectMembership` (`PK = u#<cognitoUsername>`, attribute `ProjectIds`)
2. An AppSync datasource named exactly `ProjectMembership` pointing at it
3. The datasource's service role with `dynamodb:GetItem` on that table

Otherwise `create_function` fails on the first project-access function.

### 5.4 Container image — copy, do not rebuild

The new ECR repo is empty; `apply_ecs_ecr()` only *re-tags* what is already there, so it will warn
and the typing fix will simply be missing.

```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <old-acct>.dkr.ecr.us-east-1.amazonaws.com
docker pull <old-repo>:name-fix-v1
docker tag  <old-repo>:name-fix-v1 <new-repo>:name-fix-v1
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <new-acct>.dkr.ecr.us-east-1.amazonaws.com
docker push <new-repo>:name-fix-v1
```

Copy — do not rebuild. A rebuild re-rolls Chromium and Node (`apk add chromium`, `FROM
node:20-alpine` are unpinned), which is exactly what [VP_CHANGES_APPLIED.md §2.6](VP_CHANGES_APPLIED.md)
went out of its way to avoid. Verified good: **Chromium 148.0.7778.96 / Node v20.20.2**.

Cross-account also needs a repository policy on the source repo allowing the destination principal
to pull.

### 5.5 Step Function — regenerate, do not copy

The saved definition is this environment's full state machine with this environment's ARNs. Instead:

1. `aws stepfunctions describe-state-machine` on the **new** environment → take its stock definition
2. Re-apply the four states from [VP_CHANGES_APPLIED.md §2.8b](VP_CHANGES_APPLIED.md): `Identity
   Choice Schedule`, `CreateScheduleWithIdentity`, `Identity Choice Run`, `RunTaskWithIdentity`
3. Point `TaskDefinition` / `TaskDefinitionArn` at the revision you registered in §5.4

Keep the `IsPresent` guards. The machine is in **JSONPath** mode, where a missing path throws
`States.Runtime` and **fails the execution** — an unguarded `"Value.$": "$.data.userDisplayName"`
kills Join Now entirely until the frontend ships the field.

### 5.6 Order of operations

```
 1. stock CloudFormation deploy, all nested stacks CREATE_COMPLETE
 2. create ProjectMembership table + datasource + role          (§5.3)
 3. copy the patched image into the new ECR repo                (§5.4)
 4. register a task def: new env's :1 + image swapped ONLY      (§7 trap 1)
 5. edit the script constants + the IAM/env artifacts           (§5.1, §5.2)
 6. python apply_customizations.py            → DRY RUN, read every line
 7. python apply_customizations.py --apply
 8. regenerate + apply the Step Function                        (§5.5)
 9. recreate the Enrichment Lambda + role + filtered mapping    (pre-flight 1)
10. run backfill_display_names.py if data was migrated
11. deploy the frontend with userDisplayName                    (FRONTEND_CHANGES_REQUIRED.md)
12. verification                                                (§8)
```

Steps 2–4 must precede step 7. The script does not create datasources, does not copy images, and
will happily register a cross-environment task definition if you let it.

---

## 6. Approach B — Fork and Bake (the durable answer)

```bash
# 1. fork aws-samples/amazon-transcribe-live-meeting-assistant at tag 0.3.2
# 2. bake the changes into source (table below)
# 3. ./publish.sh <your-bucket> lma <region>
# 4. deploy any number of environments from YOUR template URL
```

Where each change goes:

| Change | Destination in the fork |
|---|---|
| VPScheduler identity resolution | the inline `Code: ZipFile:` under `VirtualParticipantSchedulerFunction` (template ~line 2183) |
| `COGNITO_USER_POOL_ID`, `INTRO_MESSAGE_TEMPLATE` | that function's `Environment.Variables`, pool as a `!Ref` |
| `VPSchedulerCognitoLookup` | a `Policies:` entry on the function's role, pool ARN via `!GetAtt` |
| Scheduler `ecs:RunTask` grant | the scheduler execution role — **reference the task def by `!Ref`, never a pinned revision** |
| `LMA_IDENTITY` branches | the Step Function definition in the template |
| `name-entry.js` + `teams.js` / `zoom.js` calls | the VP container source, so CodeBuild builds it |
| Pin the container base | pin `node:20-alpine` by digest and pin the Chromium version |
| AppSync schema display fields | the `.graphql` schema in source |
| Resolvers + pipeline functions (10 + 12) | the AppSync resources in the template |
| `ProjectMembership` table + datasource + role | new template resources |
| Enrichment Lambda + role + filtered mapping | new template resources (needs pre-flight item 1) |

Two things the fork fixes that the script cannot:

- **CodeBuild stops being a hazard.** Today a re-run overwrites `:latest` with an unpatched build and
  `:3` silently reverts, with no error and no trace. After the fork, a re-run rebuilds *your* patched
  source.
- **The revision-pinning trap disappears.** No more `:3` → `:4` IAM edits (§7 trap 2).

---

## 7. Known Traps

**Trap 1 — `ecs/taskdef.json` is not portable, and failure is silent.** It carries ~50 environment
variables from *this* environment: the ECR image URI, `VNC_TARGET_GROUP_ARN`, Kinesis streams, table
names, endpoints. Register it in a new environment and the container will faithfully write
transcripts back into **this** one. `apply_ecs_ecr()` has no guard for this. Always start from the
new environment's own `:1` and change **only** the `image` field.

**Trap 2 — the scheduler's `ecs:RunTask` is pinned to a revision number.** A test meeting once got
**no bot at all**: the schedule fired, tried to run a revision the policy did not list, was denied,
and deleted itself. No error surfaced anywhere. Every time you register a new revision, update
`SchedulerExec_ECSTaskExecution.json`.

**Trap 3 — CodeBuild silently reverting `latest`.** Nothing in the account records that `latest` is a
hand-built image. Task def `:4` is pinned to `name-fix-v1` specifically to survive this; anything
riding `latest` does not.

**Trap 4 — JSONPath state machines fail closed.** Keep the `IsPresent` guards (§5.5).

**Trap 5 — EventBridge schedules freeze their payload.** VPScheduler bakes the whole `ecs:RunTask`
request into the schedule at **creation** time. Fixing code does not fix already-booked meetings, and
`scheduler:update_schedule` is a **full replace, not a patch** — resend every field or the schedule
stops firing and you lose a recording.

**Trap 6 — the stream ARN has a timestamp in it.** `mapping.EventSourceArn` ends in
`/stream/2026-05-14T12:00:02.797`. It cannot be constructed by hand; read it from the new table.

**Trap 7 — display names are not unique and come from Entra.** Two Girish accounts both resolve to
`Girish's AI Assistant`. Entra rewrites `given_name` on every federated login, so Cognito edits do
not stick — fix in Entra/AD.

---

## 8. Verification — Prove the New Environment Is Correct

Do not declare done on a green `apply` summary. Run these:

| # | Test | Pass condition |
|---|---|---|
| 1 | `python apply_customizations.py` (dry run, after applying) | everything "already correct" except the VPScheduler code re-write, which always runs |
| 2 | Schedule a meeting → let the bot join **Teams** | participant list shows `<First>'s AI Assistant`, **untruncated** |
| 3 | Same on **Zoom** | same |
| 4 | Check the container log for the retry line | `Display name entered correctly on attempt N` present — proves the patched image is running |
| 5 | **Join Now** from the extension | bot joins; with the new frontend the name matches test 2 |
| 6 | Meetings list in the web UI | `OwnerName` / `OwnerEmail` populated, no `microsoftentraid_…` GUIDs |
| 7 | Create a meeting, watch the table | Enrichment Lambda writes display fields within seconds; **no** invocation storm from transcript segments |
| 8 | Live transcript subscription | segments stream in ([LIVE_SUBSCRIPTION_FIX.md](LIVE_SUBSCRIPTION_FIX.md)) |
| 9 | Project access with two users | each sees only their projects' meetings |
| 10 | `describe-task-definition` on the registered revision | image is `name-fix-v1`; **every other field matches the new environment**, not the old one |
| 11 | Simulate a redeploy: deploy the stack again, then re-run the script | environment returns to correct — proves the safety net works |

Test 10 is the one that catches trap 1, and it is the most expensive mistake to discover later.

---

## 9. Gaps to Close Before This Is Repeatable

| Gap | Impact | Fix |
|---|---|---|
| `permanent_patches/lambda/enrichment_lambda/` **missing** | Enrichment Lambda cannot be recreated from the repo | Pre-flight item 1 — export it now, while the live environment exists |
| Script constants hardcoded | Every new environment needs a manual edit pass | Discover from CloudFormation outputs, or fork |
| `ecs/taskdef.json` saved whole | Cross-environment contamination risk | Store only the image override; build the rest from live |
| `DOCDB_PASS` in plaintext in the repo | Credential exposure | Secrets Manager reference |
| No `publish.sh` tooling set up | Approach B is blocked until it is | Docker + the `lma` CLI, then one test publish |
| `webex.ts` / `chime.ts` still type blind | Same truncation bug, unpatched | Out of scope today; include in the fork |

---

## 10. Bottom Line

- CloudFormation reads **S3, never GitHub**. A stock deploy = stock LMA = none of this exists.
- A new environment regenerates **every** physical ID, so the re-apply script is a starting point,
  not a solution — and one of its steps is actively unsafe across environments (§7 trap 1).
- **Approach A** gets a new environment live in about half a day, and you own the drift forever.
- **Approach B** is the only thing that makes "create a new environment" a one-step operation.
- Whichever you choose, **do the §3 pre-flight first** — item 1 is currently a single point of
  failure that only exists inside a live Lambda.
