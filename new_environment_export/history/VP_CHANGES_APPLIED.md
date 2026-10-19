# Virtual Participant — Resources Changed

_Applied: 2026-07-16 · Account 528757797189 · us-east-1_

**What this document is:** an inventory of every AWS resource changed, why, and how to undo it.
For *why the bugs existed*, see [VP_DISPLAY_NAME_FIX.md](VP_DISPLAY_NAME_FIX.md).

**Scope:** the **bot's own display name** inside a meeting. Related but separate workstreams:
- People's names (owner / shared-with) in the web UI →
  [MEETING_DISPLAY_NAMES_APPLIED.md](MEETING_DISPLAY_NAMES_APPLIED.md)
- Who may *see* a meeting → [PROJECT_ACCESS_STATUS.md](PROJECT_ACCESS_STATUS.md)

> **All of these are live CLI patches, not source changes.** Every one of them is reverted by the
> next CloudFormation deploy of the owning stack, and none of them exist in a new environment.
> See §4.

---

## 1. Summary — What Was Wrong, What Changed

Two independent bugs, stacked:

| # | Bug | Fixed by |
|---|---|---|
| 1 | Bot joined as `microsoftentraid_<GUID>` instead of a person | VPScheduler now looks the user up in Cognito (§2.2) |
| 2 | Teams silently ate characters from the typed name (`BI Assistant`) | Patched container image verifies what it types (§2.6) |

Bug 2 was hidden by bug 1's `LMA (` prefix, which was accidentally absorbing the lost characters.

**End state:**

| Path | Launcher | Task def | Image | Typing fix | Display name |
|---|---|---|---|---|---|
| Scheduled meeting | `LMASA-VPScheduler` | `:4` | `name-fix-v1` | yes | `Bala's AI Assistant` |
| Join now | Step Function | `:4` | `name-fix-v1` | yes | `LMA (email)` |

Both correct and untruncated. Name *formats* differ — see §5.

---

## 2. Resources Changed (8)

### 2.1 AppSync resolver — `Mutation.createVirtualParticipant`

| | |
|---|---|
| **Resource** | API `lo3gpjswjjdqrh6xn5xkgipeme`, resolver `Mutation.createVirtualParticipant` |
| **Change** | Added an `ownerEmail` attribute, written only when the token carries an `email` claim |
| **Applied via** | `aws appsync update-resolver` |
| **Backup** | `vp_fix_backups/resolver-backup.json` |
| **Repo copy** | `appsync_updated/resolvers/Mutation.createVirtualParticipant.json` (a dump — editing it does not deploy) |

```vtl
#if($ctx.identity.claims && !$util.isNullOrBlank($ctx.identity.claims.get("email")))
  $util.qr($input.put("ownerEmail", $ctx.identity.claims.get("email")))
#end
```

`owner` / `Owner` are **untouched** — they are load-bearing for authorization
(`Query.listVirtualParticipants` compares `Owner == $context.identity.username`).

> **This change currently does nothing.** The frontend authenticates with a Cognito **access token**,
> which never contains an `email` claim, so the `#if` is always false. It is future-proofing: if the
> frontend ever switches to ID tokens, `ownerEmail` starts populating and VPScheduler skips its
> Cognito call. **Safe to revert with no impact.**

### 2.2 Lambda `LMASA-VPScheduler` — code

| | |
|---|---|
| **Change** | Added `resolve_vp_identity()`; line 66 now uses its output |
| **Applied via** | `aws lambda update-function-code` |
| **Backup** | `vp_fix_backups/vpsched-backup.zip` (original) |
| **Current source** | `vp_fix_backups/VPScheduler_index_UPDATED.py` |

Resolves two values per launch by calling `cognito-idp:AdminGetUser` with the owner GUID:

```
LMA_USER     = email                    -> intro message ("invited by ...")
LMA_IDENTITY = "<First>'s AI Assistant" -> participant list
```

Display-name formula, in order:

1. Cognito `given_name`, **first word only** (Entra stores middle names too: `Bala Yogeswara Singh` → `Bala`)
2. else email local part before the first `.` (`ashiesh.jaitly@…` → `Ashiesh`)
3. capitalise first letter only (`vamsikrishna` → `Vamsikrishna`; keeps `McDonald` intact)
4. if nothing resolves → `LMA (<user>)`, the original format

Verified against all 37 pool users: **0 blanks**. 30 resolve via `given_name`, 7 via the email fallback.

> **The Lambda has no repo.** Its Python lives **inline in the CloudFormation template** as
> `Code: ZipFile:` (logical ID `VirtualParticipantSchedulerFunction`, template line 2183).

### 2.3 Lambda `LMASA-VPScheduler` — configuration

| | |
|---|---|
| **Change** | `COGNITO_USER_POOL_ID = us-east-1_pOCRCo1Wx` **(new)** · `TASK_DEFINITION_ARN` `:3` → `:4` |
| **Applied via** | `aws lambda update-function-configuration` |
| **Backup** | `vp_fix_backups/vpsched-env-backup.json` (all 12 original vars) |

Without the pool ID, the Cognito lookup silently no-ops and the GUID returns.

### 2.4 IAM role — `LMASA-VIRTUALPARTICIPANTS-VirtualParticipantSchedul-7TrZcy7KmFtq`

| | |
|---|---|
| **Change** | **New** inline policy `VPSchedulerCognitoLookup` |
| **Grants** | `cognito-idp:AdminGetUser` on `…userpool/us-east-1_pOCRCo1Wx` only |
| **Undo** | `aws iam delete-role-policy --policy-name VPSchedulerCognitoLookup` |

This is VPScheduler's own execution role. Without it, the lookup throws.

### 2.5 IAM role — `LMASA-VIRTUALPARTICIPANTST-VPSchedulerExecutionRole-X4Ig7X8qtTtT`

| | |
|---|---|
| **Change** | Added `:4` to `ecs:RunTask` resources in inline policy `ECSTaskExecution` |
| **Now allows** | `:3`, `:3:*`, `:4`, `:4:*` |
| **Backup** | `vp_fix_backups/scheduler-role-policy-backup.json` |

This is the **EventBridge Scheduler** role (different from 2.4). Its `ecs:RunTask` was hardcoded to
`:3`.

> **This caused a real outage during testing.** A test meeting silently got **no bot**: the schedule
> fired, tried to run `:4`, was denied, and then deleted itself. **No error surfaced anywhere.**
> If you ever add a task-definition revision, this policy must be updated or launches fail silently.

### 2.6 ECR — `lmasa-virtualparticipantstack-1iavizt1439r2-imagerepo-l3yrnekcbuxx`

| Tag | Digest | What |
|---|---|---|
| `pre-name-fix-20260514` | `sha256:32cf092c…` | **Original 14-May image**, preserved for rollback |
| `name-fix-v1` + `latest` | `sha256:58a82af8…` | **Patched image** |

Built by hand — pulled `:latest`, replaced two compiled files, pushed. **Not** rebuilt from source,
deliberately: `apk add chromium` and `FROM node:20-alpine` are unpinned, so a rebuild would have
re-rolled Chromium and Node. Verified identical: **Chromium 148.0.7778.96 / Node v20.20.2**.

Patch source: `vp_fix_backups/container_patch/` (`name-entry.js`, patched + `.orig` of both handlers,
Dockerfile pinned to the base digest).

```
teams.js : 2 lines changed (1 import, 1 call)
zoom.js  : 2 lines changed (1 import, 1 call)
name-entry.js : new file
```

The fix — type, read the field back, retype if it doesn't match, up to 6 attempts:

```js
await element.evaluate((el) => { el.focus(); el.value = ''; });
await element.type(value, { delay: 30 });
const actual = await element.evaluate((el) => el.value || '');
if (actual === value) return true;      // verified
```

If all 6 attempts fail it logs and **joins anyway** — a wrong name beats a lost recording.

`latest` was **retagged** to the patched image so anything on `:3` inherits the fix.

### 2.7 ECS task definition — revision `:4`

| | |
|---|---|
| **Family** | `LMASA-VIRTUALPARTICIPANTSTACK-1IAVIZT1439R2VirtualParticipantTaskDef` |
| **Change** | **New** revision `:4`, cloned from `:3`, image → `name-fix-v1` |
| **Backup** | `vp_fix_backups/taskdef3-backup.json` |

Verified clone: **only the image field differs**. All 50 env vars, CPU, memory, roles, networking
byte-identical to `:3`.

**Why pinned to `name-fix-v1` and not `latest`:** `:3` says `latest`, which resolves at task start to
whatever carries that tag. The CodeBuild project writes `latest` on every run — so if it ever runs
again, it overwrites `latest` with a fresh unpatched build from Amazon's source and **`:3` silently
reverts to the truncating bot with no error and no trace**. `name-fix-v1` is a tag only we write, so
`:4` cannot be changed underneath us.

### 2.8 Step Functions — `LMASA-LMAVirtualParticipantScheduler`

| | |
|---|---|
| **Change** | Two `:3` → `:4` references |
| **Applied via** | `aws stepfunctions update-state-machine` |
| **Backup** | `vp_fix_backups/sfn-definition-backup.json` |

```
States.RunTask.Parameters.TaskDefinition                                → :4   (join now)
States.CreateSchedule.Parameters.Target.EcsParameters.TaskDefinitionArn → :4
```

Deep-diffed against the backup: **exactly 2 fields changed**. No IAM change needed — this role's
`ecs:RunTask` is already `*`.

This is the **join-now / browser-extension** path (API Gateway driven, `$.apiInfo.httpMethod`).
It takes its name from `$.data.userName`, which the **frontend already sends as the email** — which
is why join-now never showed the GUID, and why only scheduled meetings were affected.

### 2.8b Step Functions — `LMA_IDENTITY` for the Join Now path (added 2026-07-20)

| | |
|---|---|
| **Change** | New guarded branches so Join Now can set the bot's display name |
| **Backup** | `vp_fix_backups/sfn-definition-backup.json` |

Join Now sets only `LMA_USER` and never `LMA_IDENTITY`, so the container falls back to its default
template `LMA ({LMA_USER})` — producing `LMA (user@email)` instead of `Bala's AI Assistant`.

Four states added:

```
Time Choice
├── scheduled  -> Identity Choice Schedule
│                   ├── userDisplayName present -> CreateScheduleWithIdentity   (new)
│                   └── absent                  -> CreateSchedule               (original)
└── join now   -> Identity Choice Run
                    ├── userDisplayName present -> RunTaskWithIdentity          (new)
                    └── absent                  -> RunTask                      (original)
```

The `*WithIdentity` states add `{"Name": "LMA_IDENTITY", "Value.$": "$.data.userDisplayName"}`.

> **Why guarded, not a direct mapping.** The state machine uses **JSONPath** mode, where referencing
> a missing path throws `States.Runtime` and **fails the execution**. A plain
> `"Value.$": "$.data.userDisplayName"` would have broken Join Now entirely — no bot, no recording —
> until the frontend shipped the field. The `IsPresent` guard makes it backwards compatible: today
> every request takes the original branch, byte-identical to before.

**Frontend must send `userDisplayName`** for this to take effect — see
[FRONTEND_CHANGES_REQUIRED.md](FRONTEND_CHANGES_REQUIRED.md) Change 2. No rollout coordination
needed; ship any time.

### 2.9 EventBridge Scheduler — 14 pending schedules, group `LMASA-vp-schedules`

| | |
|---|---|
| **Change** | `LMA_USER` rewritten from GUID → email in each schedule's frozen `Target.Input` |
| **Backup** | `vp_fix_backups/schedules-backup.json` (all 14 originals) |

**Why this was needed:** VPScheduler freezes the whole `ecs:RunTask` request — including `LMA_USER`
— into the EventBridge schedule **at creation time**. EventBridge replays that JSON verbatim. So
fixing the code does **not** fix meetings already booked.

`scheduler:update_schedule` is a **full replace, not a patch** — every field must be resent or it is
dropped, which would stop the schedule firing and cost a recording. Verified afterwards by diffing
all 14 field-by-field:

```
schedules checked      : 14
unexpected field diffs : 0
still showing GUID     : 0
```

They still carry `TaskDefinition: :3` — harmless, since `:3` → `latest` → the patched image (§2.6).

---

## 3. Rollback

| To undo | Action |
|---|---|
| Container fix (everything) | Retag `:latest` → `pre-name-fix-20260514`, set VPScheduler `TASK_DEFINITION_ARN` → `:3`, restore `sfn-definition-backup.json` |
| Scheduled name only | VPScheduler `TASK_DEFINITION_ARN` → `:3` |
| VPScheduler code | Deploy `vpsched-backup.zip` |
| VPScheduler env | Restore `vpsched-env-backup.json` |
| Cognito lookup grant | Delete inline policy `VPSchedulerCognitoLookup` |
| Scheduler `:4` grant | Restore `scheduler-role-policy-backup.json` |
| AppSync resolver | Restore `resolver-backup.json` (no functional impact either way) |
| Step Function | Restore `sfn-definition-backup.json` |
| 14 schedules | Restore from `schedules-backup.json` |

Nothing was overwritten except the `latest` tag, and the image it pointed at is preserved as
`pre-name-fix-20260514`.

---

## 4. Drift — Read This Before Any Deploy

**Every change above is a live CLI patch. None is in source.**

| Trigger | Consequence |
|---|---|
| CloudFormation deploy of `LMASA-VIRTUALPARTICIPANTSTACK-1IAVIZT1439R2` | VPScheduler code + env, both IAM roles, the Step Function definition all revert |
| CloudFormation deploy of `AISTACK` | The AppSync resolver reverts |
| **The CodeBuild project runs again** | Overwrites `:latest` with an unpatched build. `:3` reverts **silently**; `:4` survives because it is pinned |
| **New environment** | Gets stock LMA 0.3.2. None of this exists. The GUID and the truncation both return |

The most dangerous is the CodeBuild one — nothing in the account records that `latest` is a
hand-built image, so a rebuild reverts the typing fix with no error and no trace.

**The durable fix is to fork.** The repo is designed for it:

```bash
./publish.sh <your_bucket> <prefix> <region>
```

Fork `aws-samples/amazon-transcribe-live-meeting-assistant` at the **0.3.2 tag** (matching what runs
today), port in these changes plus your existing customizations, publish to your own bucket, and
deploy from your artifacts. Then a new environment includes everything by default.

Upstream has **already fixed the typing bug** in `main` — but `main` migrated Puppeteer → Playwright,
so their file cannot be dropped into 0.3.2. Fork at 0.3.2 first, upgrade separately.

---

## 5. Known Gaps

**Name formats differ between the two paths.** Scheduled shows `Bala's AI Assistant`; join-now shows
`LMA (email)`. Unifying them needs the frontend to send a first name, or a resolver step in the Step
Function — deliberately deferred to the fork rather than adding an eighth live patch.

**`webex.ts` and `chime.ts` still type blind.** Same bug, unpatched. Out of scope — Teams and Zoom
only.

**Display names are not unique.** Two Girish accounts (`@dimcon.com`, `@dimensioncg.com`) both
resolve to `Girish's AI Assistant`. The email was unambiguous; the first name is not.

**Directory names may not match what people are called.** `given_name` for `yogeswarsingh.bondili@`
is `Bala Yogeswara Singh`, so the display name is `Bala's AI Assistant`. Entra rewrites these
attributes on every federated login, so editing Cognito will not stick — fix it in Entra/AD.

**Dormant services** `LMASA-vp-service-FARGATE` and `-EC2` point at task defs `:2` and `:1`. Both
scaled to 0. They would pick up the fix via `latest` if ever started.

---

## 6. Related Observation (Not Fixed)

`scheduledFor` is always `NULL` in the VP table:

```
"meetingTime":{"N":1784215200}, "scheduledFor":{"NULL":null}
```

`$util.math.multiply($ctx.args.input.meetingTime, 1000)` overflows 32-bit int on epoch seconds, so
`epochMilliSecondsToISO8601` receives null. Pre-existing and unrelated. Harmless for launching —
VPScheduler uses `meetingTime`, not `scheduledFor`.

---

## 7. Evidence

The typing bug, reproduced live and corrected in the same meeting:

```
+40.2s  Entering name.
+47.0s  Display name mismatch (expected "Bala's AI Assistant", got "BI Assistant") - retrying 1/6.
+48.1s  Display name entered correctly on attempt 2.
```

Four observed truncations — the loss is a *duration*, not a character count, which is why no fixed
prefix could ever have absorbed it:

| Typed | Teams kept | Lost |
|---|---|---|
| `LMA (yogeswarsingh…com)` | `Lyogeswarsingh…com)` | 4 |
| `yogeswarsingh…com` | `y` | 36 |
| `Bala's AI Assistant` | `BI Assistant` | 7 |

Tested: **Teams** (retry fired, corrected on attempt 2) and **Zoom** (clean on attempt 1 — no
hydration shroud).
