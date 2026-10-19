# Making the Changes Survive CloudFormation

_Last updated: 2026-07-21_

**The problem:** every change in this session was applied **live via CLI** (AWS calls), not through
CloudFormation. LMA is CloudFormation-managed, so the **next stack deploy reverts the ones it owns**.
This document says exactly what survives, what doesn't, and the two ways to make it permanent.

**Standing up a brand-new environment rather than redeploying this one?** Start with
[NEW_ENVIRONMENT_PLAYBOOK.md](NEW_ENVIRONMENT_PLAYBOOK.md) — the checks, order of operations, and
traps specific to fresh physical resource IDs.

---

## 1. Not Everything Is At Risk — Data vs Infrastructure

CloudFormation manages **infrastructure** (Lambdas, resolvers, schema, IAM, task defs). It does
**not** touch **data** inside DynamoDB. So the changes split cleanly:

### ✅ SURVIVES a redeploy (data — CloudFormation never touches it)

| Change | Where |
|---|---|
| `OwnerName` / `OwnerEmail` / `SharedWithNames` / `SharedWithEmails` backfill (450 rows) | EventSourcing table |
| Owner repair — 18 rows email→username | EventSourcing table |
| `ProjectId` tags on meetings | EventSourcing table |
| `LMASA-ProjectMembership` rows | membership table |

These need **no re-apply**. They're data.

### ✅ SURVIVES — standalone resources created fresh (not part of any stack)

| Resource | Note |
|---|---|
| Lambda `LMASA-MeetingEnrichment` + role + stream mapping | I created it standalone; no stack owns it |
| ECS task definition `…TaskDef:4` | a new revision; persists |
| ECR tags `name-fix-v1`, `pre-name-fix-20260514` | images persist in the repo |

**But:** a redeploy may **re-point** things away from these (e.g. VPScheduler's `TASK_DEFINITION_ARN`
reverts to `:3`), so they survive but become **unreferenced** until re-pointed.

### ❌ WIPED by a redeploy (modifications to stack-owned resources)

| Change | Owning stack |
|---|---|
| VPScheduler Lambda code + env vars (`COGNITO_USER_POOL_ID`, `INTRO_MESSAGE_TEMPLATE`, `TASK_DEFINITION_ARN`) | VIRTUALPARTICIPANTSTACK |
| Step Function `LMA_IDENTITY` branches | VIRTUALPARTICIPANTSTACK |
| IAM: `VPSchedulerCognitoLookup`, scheduler-exec `:4` grant | VIRTUALPARTICIPANTSTACK |
| AppSync schema — the 4 display fields | AISTACK |
| AppSync resolver `createVirtualParticipant` (`ownerEmail`) | AISTACK |
| **Project-access resolvers + functions** (10 resolvers, `GetUserProjects`, `GetCallForSeg`, `GetCallForSub`, subscription fixes) | AISTACK |
| ECR `:latest` retag → patched image | may be rebuilt by CodeBuild on deploy |

**This is the list that a redeploy breaks.** Everything else is safe.

### ⚡ EPHEMERAL — don't bother

The 14 EventBridge schedule backfills are for meetings that have since run; irrelevant going forward.

---

## 2. The Catch That Decides Everything: Physical IDs

There are **two** kinds of "run CloudFormation," and they behave very differently:

| | Physical resource IDs | A replay-script needs to… |
|---|---|---|
| **A. Redeploy / update the SAME stack** | **unchanged** (CFN keeps logical→physical mapping) | just replay the same CLI commands |
| **B. Deploy a NEW environment** | **all new** (`lo3gpjs…` API id, `…7TrZcy7KmFtq` role, table names — all regenerated) | **discover** the new IDs first, then apply |

Every CLI patch we made references **current physical IDs** (API `lo3gpjswjjdqrh6xn5xkgipeme`, role
suffixes, table `…4VKHCQQY3CJR…`). Those are stable for **A**, but a **fresh environment (B)** gets
brand-new ones — so a hardcoded replay script would target resources that don't exist.

This is the core reason the two strategies below differ.

---

## 3. Two Strategies

### Option A — Re-apply Script  *(what you asked: "run basic stack, then apply changes on it")*

A single **idempotent script** that replays every ❌ change from §1, in order, after a stock deploy.

```
1. deploy stock LMA  (CloudFormation)
2. run  apply_customizations.py
        → re-applies VPScheduler code/env, Step Function, IAM,
          schema fields, all resolvers/functions, image retag
3. done — customizations restored
```

- ✅ **Directly answers your question** — stock + one script.
- ✅ Buildable now; I already have every change + backup in `vp_fix_backups/`.
- ✅ Works for **redeploys of the same stack** (Option A above) as-is.
- ⚠️ For a **new environment**, the script must first **discover** the new resource IDs from
  CloudFormation outputs, then apply. Doable, but more code and more fragile.
- ❌ Still **drift** — CloudFormation doesn't know these changes exist, so it can fight them
  (e.g. try to remove an inline IAM policy it didn't create). You must remember to re-run it after
  *every* deploy, forever.

### Option B — Fork & Bake Into Source  *(the durable answer)*

Put the changes **into the LMA source**, so CloudFormation itself creates them.

```
1. fork  aws-samples/amazon-transcribe-live-meeting-assistant  at your version (0.3.2)
2. apply changes IN the source:
     - templates (VPScheduler code, IAM, Step Function, schema, resolvers)
     - container patch (teams.js/zoom.js name-entry)
     - new resources (enrichment Lambda, GetCallForSub, display-field resolvers)
3. ./publish.sh <your-bucket> lma <region>   → packages to YOUR S3
4. deploy the stack from YOUR template
```

- ✅ Changes are **in the stack** — a redeploy re-applies them, a new environment includes them.
- ✅ No drift, no re-run-after-deploy, no ID discovery.
- ✅ The repo is **built for this** (`publish.sh` exists for exactly this purpose).
- ⚠️ One-time effort: reconcile ~15 changes into the templates, set up the publish tooling
  (Docker + the `lma` CLI), and test-deploy once.
- Note: upstream `main` already fixes the container typing bug but migrated Puppeteer→Playwright, so
  fork at **0.3.2** (your version) and port changes in; upgrade separately later.

---

## 4. Recommendation

**Do both, in order:**

1. **Now — build the re-apply script (Option A).** It's your safety net. If someone redeploys
   tomorrow, you run one script and you're back. Low effort, immediate protection. I have all the
   pieces.
2. **Then — fork properly (Option B)** when there's time. It's the only thing that makes this
   maintainable and reproducible across environments, and it retires the script.

The re-apply script also **doubles as the checklist** for the fork — it enumerates, in dependency
order, exactly what has to go into the templates.

---

## 5. Full Change Inventory (source of truth for either path)

Each links to its detailed doc.

| # | Change | Doc | Survives? |
|---|---|---|---|
| 1 | VPScheduler code (identity resolution, 3-var split) | [VP_CHANGES_APPLIED.md](VP_CHANGES_APPLIED.md) §2.2 | ❌ |
| 2 | VPScheduler env (`COGNITO_USER_POOL_ID`, `INTRO_MESSAGE_TEMPLATE`, `TASK_DEFINITION_ARN`) | §2.3 | ❌ |
| 3 | IAM `VPSchedulerCognitoLookup` | §2.4 | ❌ |
| 4 | IAM scheduler-exec `:4` grant | §2.5 | ❌ |
| 5 | ECR patched image + `:latest` retag | §2.6 | ⚠️ image persists; retag may be rebuilt |
| 6 | ECS task def `:4` | §2.7 | ✅ persists (may be unreferenced) |
| 7 | Step Function `LMA_IDENTITY` branches | §2.8b | ❌ |
| 8 | `createVirtualParticipant` `ownerEmail` | §2.1 | ❌ |
| 9 | Display-name backfill + Owner repair (data) | [MEETING_DISPLAY_NAMES_APPLIED.md](MEETING_DISPLAY_NAMES_APPLIED.md) | ✅ data |
| 10 | AppSync schema — 4 display fields | §2.2 | ❌ |
| 11 | Enrichment Lambda + role + stream mapping | §2.3–2.4 | ✅ standalone |
| 12 | Project-access resolvers + functions (pre-session) | [PROJECT_ACCESS_STATUS.md](PROJECT_ACCESS_STATUS.md) | ❌ |
| 13 | Subscription fix + `GetCallForSub` | [LIVE_SUBSCRIPTION_FIX.md](LIVE_SUBSCRIPTION_FIX.md) | ❌ |
| 14 | `ProjectId` tags + membership rows (data) | [BACKEND_INTEGRATION_SPEC.md](BACKEND_INTEGRATION_SPEC.md) | ✅ data |

Backups for the ❌/⚠️ rows are in `vp_fix_backups/`.

---

## 6. Bottom Line

- **Your data is safe** — a redeploy won't lose names, tags, memberships, or repairs.
- **The infrastructure changes are the exposure** — §1's ❌ list.
- **For a same-stack redeploy:** a re-apply script (Option A) restores everything.
- **For a new environment or true durability:** fork and bake into source (Option B).
- Start with the script for safety; move to the fork for permanence.
