# Porting Guide — Moving Our Live Changes onto LMA 0.3.10

**Who this is for:** a developer on the team who is new to this codebase and has to build a branch
that is "AWS's LMA 0.3.10 **plus** everything our team changed by hand on the live stack".

**What you will end up with:** one branch you can build with `publish.sh` and deploy to a fresh
environment, with every customization included — no hand-patching after deploy.

Read sections 1–4 once before touching code. Then work through section 6 one feature at a time.

---

## 1. The Situation in Plain Words

Three different "versions" of LMA exist. Keep them apart in your head:

```
 AWS upstream (GitHub)          our repo                     our live AWS account
 ─────────────────────          ────────                     ────────────────────
 v0.3.2  ──────────────────────────────────────────────────▶ stack "LMASA" deployed 2026-05-14
   │                                                           │
   │                                                           │  people changed things BY HAND
   │                                                           │  in the AWS console / CLI
   │                                                           ▼  (May → Oct 2026)
   │                                                         LIVE = 0.3.2 + our hand changes
   │                                                           │
   │                                    new_environment_export/ ◀── a full copy of those
   │                                    container_patch/            hand changes (2026-10-19)
   ▼
 v0.3.10  ─────────────▶  your new branch  ◀── apply our hand changes HERE, in source code
```

- **Stock 0.3.2** — what AWS shipped and what the live stack was built from.
- **Live** — 0.3.2 plus our hand changes. Nothing about those changes is in any template; a fresh
  deploy would not have them.
- **0.3.10** — AWS's newer version. Some of our hand changes are **already in it** (AWS built the
  same thing). The rest we must add.

## 2. Words You Will See

| Word | Meaning here |
|---|---|
| **Stack / template** | A CloudFormation YAML file that creates AWS resources. LMA is one main stack (`lma-main.yaml`) with nested stacks (`lma-ai-stack/`, `lma-virtual-participant-stack/`, …) |
| **AppSync** | The GraphQL API the web app talks to |
| **Schema** | The list of types and fields the API offers (`schema.graphql`) |
| **Resolver** | The code that runs when the API is asked for one field, e.g. `Query.getCall` |
| **UNIT vs PIPELINE resolver** | UNIT = one step. PIPELINE = several small steps ("functions") run in order |
| **Pipeline function** | One step of a pipeline resolver, e.g. `GetUserProjects` |
| **Data source** | What a resolver reads/writes: a DynamoDB table or a Lambda |
| **VTL / APPSYNC_JS** | The two languages resolvers are written in (`.vtl` templates or `.js`) |
| **VP (Virtual Participant)** | The bot that joins Zoom/Teams meetings. Runs as a container on ECS |
| **Task definition** | The ECS settings for the bot container: image + environment variables |
| **Step Function** | The workflow that schedules / starts the bot |
| **Drift** | A difference between what the template says and what is really in AWS |

## 3. Where Everything Is

```
new_environment_export/
├── PORTING_GUIDE_0.3.10.md        ← you are here
├── STACK_CHANGES_INVENTORY.md     ← the complete list of hand changes (reference)
├── tools/show_change.py           ← shows "stock vs live" for any changed item  ★ use this a lot
├── appsync/                       ← LIVE resolvers, functions, schema, data sources
├── lambda/                        ← LIVE code + settings of the 4 customized Lambdas
├── iam/                           ← LIVE permissions (iam/external/ = Synthera-side roles)
├── dynamodb/                      ← LIVE table definitions (keys, indexes)
├── stepfunction/                  ← LIVE bot scheduler workflow
├── ecs/                           ← LIVE bot task definition (:15) and service
├── cognito/                       ← LIVE login setup (Entra ID SSO, clients, user pool)
├── drift/                         ← raw "template vs live" report from AWS
└── history/                       ← design notes + "before" snapshots
    ├── README.md                  ← what each history file is
    ├── PROJECT_ACCESS_DESIGN.md   ← why project access works the way it does
    ├── BACKEND_INTEGRATION_SPEC.md← what Synthera's backend writes into LMA
    ├── appsync_current/           ← AppSync resolvers BEFORE our changes (= stock 0.3.2)
    ├── vp_fix_backups/            ← bot scheduler / Step Function BEFORE our changes
    └── stock_0.3.2/               ← stock schema + MeetingControls code
container_patch/                   ← the bot code changes (compiled JS) + .orig + .patch files
```

## 4. The Golden Rule

> **Never copy a live file over a 0.3.10 file.**

The live files contain old 0.3.2 code mixed with our changes. Copying them would silently throw
away AWS's fixes from 0.3.3 → 0.3.10. Instead, for every item:

1. **See exactly what we changed** — only our lines:
   ```bash
   python new_environment_export/tools/show_change.py --list          # what changed at all
   python new_environment_export/tools/show_change.py Query.getCall   # one item, stock vs live
   ```
   `-` lines = stock code we removed, `+` lines = what we added.
2. **Open the 0.3.10 version** of the same thing (paths are in each feature below).
3. **Add only our lines** to the 0.3.10 version, adapting them if 0.3.10 changed the surrounding code.
4. **Build, deploy to a dev stack, test** (section 7).

For the bot, the same idea: `container_patch/*.js.patch` shows our changes to the compiled bot;
apply the same logic to the TypeScript in `lma-virtual-participant-stack/backend/src/*.ts`.

---

## 5. Set Up the Branch

```bash
git fetch origin
git switch -c feature/lma-0.3.10-sync origin/qa      # qa already has part of our work + Jenkins CI/CD
git remote add upstream https://github.com/aws-samples/amazon-transcribe-live-meeting-assistant.git
git fetch upstream --tags
git merge v0.3.10                                     # AWS's 0.3.10 release tag (fixed; upstream/main keeps moving)
```

What each line does: (1) refresh our GitHub branches, (2) create the new branch from `qa` and switch
to it, (3) add AWS's repo as a second remote called `upstream` — once only, (4) download AWS's
branches and release tags, (5) merge the 0.3.10 release into the new branch.

Git will stop with **merge conflicts** in about 9 files. For each conflicted file: take 0.3.10's
version, then re-add the `qa` change on top. The `qa` changes are small and listed in
`git log origin/qa` — mainly the Jenkins pipeline (no conflict), Project Membership pieces in
`lma-ai-stack.yaml`, and `typeReliably` in the bot (`prejoin-actions.ts`, `teams.ts`, `zoom.ts`).

Commit the merge, then do section 6 as **one commit per feature** — small commits are easy to review
and easy to undo.

---

## 6. The Work List

Do the features in this order. Each one says whether 0.3.10 already has it, where the live version
is, and where it goes.

### 6.0 Already in 0.3.10 — do NOT port

| Live change | What to do |
|---|---|
| `TypeDateIndex` index, `listCallsDateRange`, `getCallCount`, `createCall` writing `ItemType` | Nothing — use 0.3.10's. Only add our `projectId` filter (see 6.5) |
| AppSync `AWS_IAM` login | Nothing — 0.3.10 has it |
| Bot: Teams "badge missing / blank" handling | Nothing — 0.3.10's watchdog is better (`decideAttendeeAction` in `teams.ts`) |

### 6.1 Deploy parameters (easiest — no code)

These are just values you set when deploying 0.3.10 (`lma-main.yaml` parameters, or
`deploy/params/lma-<env>.json` for the Jenkins pipeline):

| Parameter | Live value | Why |
|---|---|---|
| `VPLaunchType` | `FARGATE` | Live runs the bot on Fargate. 0.3.10 also offers `MICROVM` — keep `FARGATE` unless the team decides otherwise |
| `AllowedSignUpEmailDomain` | `dimensioncg.com,dimcon.com,dimconmail.com,mail.dimensioncg.com,snaddevelopers.com` | `snaddevelopers.com` was added by hand |
| `AudioRecordingExpirationInDays` | `36500` | Live keeps recordings ~100 years (stock: 90). **Ask the team** if that is intended |
| `MeetingRecordExpirationInDays` / `TranscriptionExpirationInDays` | very large | Live turned meeting expiry (TTL) **off**. **Ask the team** |
| `CloudWatchLogsExpirationInDays` | `3` | Live log retention |

⚠️ Live CloudFront allows old TLS (`TLSv1`); stock requires `TLSv1.2_2021`. **Do not copy this** —
confirm with the team first; it is a security downgrade.

### 6.2 Bot (Virtual Participant) code

- **Live:** `container_patch/` — compare with `*.js.patch`. Settings: `ecs/taskdef_rev15.json`.
- **0.3.10:** `lma-virtual-participant-stack/backend/src/`

| Change | Where in 0.3.10 | Notes |
|---|---|---|
| Retype the display name until it is correct (up to 6 tries) | `prejoin-actions.ts` (+ calls in `teams.ts`, `zoom.ts`) | Already written in TypeScript on `qa` (`typeReliably`). In Teams compare against `sanitizeTeamsDisplayName(...)`, not the raw name |
| Leave when only bots remain | `teams.ts` (after joining), settings in `details.ts` | See `container_patch/teams.js.patch` |
| Leave after 20 min of silence | `scribe.ts` (`lastSpeechAt`) + `teams.ts` watchdog | See `scribe.js.patch`, `teams.js.patch` |
| 5 settings: `BOT_NAME_SUFFIX`, `BOT_NAME_PREFIX`, `NO_HUMANS_GRACE_SECONDS`, `IDLE_TIMEOUT_MINUTES`, `WATCHDOG_CHECK_SECONDS` | `details.ts` + the task definition env in `lma-virtual-participant-stack/template.yaml` | Values in `ecs/taskdef_rev15.json`. **Add `LMA - ` to `BOT_NAME_PREFIX`** — 0.3.10 renames `LMA (x)` to `LMA - x` in Teams |

Test: `cd lma-virtual-participant-stack/backend && npm install && npm run build && npm test`.
Add a unit test for the bot-name check.

> **Status on `feature/lma-0.3.10-sync`:** the name retry is already in place — the 0.3.10 merge
> conflicts in `teams.ts` / `zoom.ts` were resolved by keeping 0.3.10's code and calling
> `typeReliably` (Teams types the sanitized name). Auto-leave and the 5 settings are still to do.

### 6.2a Decision point — how the bot joins Microsoft Teams

0.3.10 can join Teams in two ways. Everything in 6.2 for Teams applies to the **browser** method only.

| | Browser method — `teams.ts` (**default**) | Teams SDK method — `teams-sdk.ts` |
|---|---|---|
| How | Headless Chromium opens the Teams web page, clicks and types like a person | Joins through Microsoft's **Azure Communication Services (ACS)** calling SDK — no Teams web page |
| Pop-ups, slow page, name field | Must be handled (where the live bot gets stuck) | Not involved — the name is passed through the API |
| Needs | Nothing extra | An Azure Communication Services resource + its connection string |
| Live bot (0.3.2) | ✅ uses this | ❌ did not exist in 0.3.2 |

**How the bot chooses** (`resolveJoinMethod()` in `details.ts`): if `ACS_CONNECTION_STRING` is set
→ SDK, otherwise → browser. Turn it on with the deploy parameter **`AcsConnectionString`** in
`lma-main.yaml` (blank by default; the template passes it to the bot as `ACS_CONNECTION_STRING`).
The code also honours `MEETING_TEAMS_METHOD` = `sdk` / `dom` as an override, but nothing in the
template or UI sets it today.

**What changes if Teams moves to the SDK**

- The "stuck on the audio/video pop-up" problem disappears for Teams.
- Name typing (`typeReliably`) is no longer used for Teams. **Zoom still uses the browser**, so the
  Zoom name retry still matters.
- **Auto-leave** (bots-only / silence, 6.2) must be implemented in `teams-sdk.ts` instead of
  `teams.ts`, using the SDK's roster events.

**Before switching, check with the team**

1. Can the company create an **Azure Communication Services** resource? It is an Azure subscription
   item, billed per minute of calling.
2. **Teams tenant policy:** the SDK bot joins as an *external / anonymous* participant. The Teams
   admin must allow that, and the bot may wait in the **lobby** until someone admits it.

**Recommended order**

1. Finish the port with the **browser method** (default) — it is what runs today.
2. On a **dev stack**, deploy with `AcsConnectionString` set and join several real Teams meetings.
3. If the SDK is more reliable, make it the Teams default for your environments and port
   auto-leave to `teams-sdk.ts`. Record the decision in this guide.

### 6.3 Bot scheduler + Step Function (bot's display name)

- **Why:** `history/VP_DISPLAY_NAME_FIX.md`, `history/VP_CHANGES_APPLIED.md`
- **See the change:** `show_change.py vpscheduler` and `show_change.py stepfunction`
- **0.3.10:** `lma-virtual-participant-stack/template.yaml` — `VirtualParticipantSchedulerFunction`
  (inline Python) and `LMAVirtualParticipantSchedulerStateMachine`

| Change | Notes |
|---|---|
| VPScheduler looks up the user's real name in Cognito and builds the bot identity + intro message | New env vars `COGNITO_USER_POOL_ID`, `INTRO_MESSAGE_TEMPLATE` (copy the template text from `lambda/LMASA-VPScheduler.config.json`) |
| Permission `cognito-idp:AdminGetUser` for the scheduler role | `iam/…VirtualParticipantSchedul…__VPSchedulerCognitoLookup.json` |
| 4 Step Function states that pass `LMA_IDENTITY` | 0.3.10's Step Function is **restructured** — re-create the same idea, don't paste the old JSON. Keep the `IsPresent` checks |

Do **not** port the `ecs:RunTask` revision lists (`:3`, `:4`, … `:15`) — in a template the task
definition is referenced directly, so this problem disappears.

### 6.4 Owner / shared-with display names

- **Why:** `history/MEETING_DISPLAY_NAMES_APPLIED.md`, `history/FRONTEND_CHANGES_REQUIRED.md`
- **See:** `show_change.py schema` (the `Owner*` / `SharedWith*` fields)

| Change | Where in 0.3.10 |
|---|---|
| Schema: `OwnerEmail`, `OwnerName`, `SharedWithEmails`, `SharedWithNames` on `Call` and `CallListItem` | `lma-ai-stack/source/appsync/schema.graphql` |
| Enrichment Lambda (fills those fields; also tags `ProjectId`) | New function in `lma-ai-stack.yaml`; copy `lambda/enrichment_lambda_source/` (3 `.py` files + `requirements.txt`) into a new folder such as `lma-ai-stack/source/lambda_functions/meeting_enrichment/`. This one is ours entirely, so copying is fine |
| Its trigger: EventSourcing table stream **with filters** (`INSERT` and `PK` starts with `c#` or `cls#`) | `lambda/enrichment_event_source_mappings.json`. Without the filters it is invoked for every transcript line |
| Its role | `iam/LMASA-MeetingEnrichmentRole__EnrichmentPolicy.json` |
| MongoDB connection | Env vars in `lambda/LMASA-MeetingEnrichment.config.json`. Put the password in **Secrets Manager**, never in the template |

### 6.5 Project-based meeting access (biggest feature)

- **Why:** `history/PROJECT_ACCESS_DESIGN.md` (read first), `history/PROJECT_ACCESS_STATUS.md`,
  `history/LIVE_SUBSCRIPTION_FIX.md`, `history/BACKEND_INTEGRATION_SPEC.md`
- **`qa` already has:** the `ProjectMembershipTableArn` parameter (feature is OFF when empty), the
  data source + role, 9 functions (`GetUserProjects`, `GetUserProjectsJS`, `GetCallAuth`,
  `GetCallForSeg`, `GetCallForAddSeg`, `GetCallForSub`, `PutSegment`, `QuerySegments`,
  `QuerySegmentsSentiment`) and 5 resolvers (`getCall`, `getTranscriptSegments`,
  `getTranscriptSegmentsWithSentiment`, `onUpdateCall`, `onAddTranscriptSegment`). Check each
  survived the merge, then compare with live using `show_change.py <name>`.

Still to do:

| Change | Notes |
|---|---|
| `ProjectMembership` table itself | Live table: `dynamodb/ProjectMembership.json` (key `PK` only). Either create it in the template or keep passing its ARN as a parameter — decide with the team |
| `Mutation.addTranscriptSegment` → pipeline (`GetCallForAddSeg` → `PutSegment`) | Stamps `ProjectId` on each transcript line. Check whether `qa` already converted it |
| `Subscription.onCreateCall` → owner/shared only | `show_change.py Subscription.onCreateCall` |
| **Meeting list** | ⚠️ 0.3.10 **removed** `listCalls`, `listCallsDateHour`, `listCallsDateShard`. The list now comes from the Lambda `lma-ai-stack/source/lambda_functions/list_calls_gsi_resolver/`. Add the project-membership check **inside that Lambda** (read `ProjectMembership` for the caller, include meetings whose `ProjectId` is in their list and they are not in `ExcludedUsers`) and add the `projectId` argument to `listCallsDateRange`. The live JS version is `show_change.py ListCallsDateRangeQuery` — use it as the reference for the rules |

### 6.6 Bot sharing (VP list shared between users)

- **See:** `show_change.py Mutation.shareVirtualParticipant`, `…unshareVirtualParticipant`,
  `Query.listVirtualParticipants`, and the 6 functions `ShareVirtualParticipantTransact`,
  `UnshareVirtualParticipantTransact`, `VirtualParticipantGetOldSharedWith`, `VPGetSharedIds`,
  `VPBatchGetSharedAndMerge`, `VPOwnerQueryOrAdminScan`
- **Not in 0.3.10.**

| Change | Where |
|---|---|
| New table `VirtualParticipantShareIndex` + AppSync data source | `dynamodb/VirtualParticipantShareIndex.json` → `lma-ai-stack.yaml` |
| Indexes `OwnerIndex`, `MeetingIdIndex` on the VirtualParticipant table | `dynamodb/VirtualParticipant.json` |
| 3 resolvers become JS pipelines + 6 functions | Put the JS in `lma-ai-stack/source/appsync/`, reference with `CodeS3Location` like other JS resolvers |
| Data source role gets `BatchGetItem`, `TransactWriteItems`, index + share-table access | `iam/…VirtualParticipantDataSou…__DynamoDBAccess.json` |

### 6.7 Synthera backend creating bots

- **See:** `show_change.py Mutation.createVirtualParticipant`
- `createVirtualParticipant` trusts `ownerUsername` / `ownerEmail` **only** when the caller signs
  with AWS IAM (Synthera's backend). Add `ownerEmail`, `ownerUsername` to
  `CreateVirtualParticipantInput` in the schema.
- Synthera's roles that call LMA are in `iam/external/` — they live outside LMA but must be pointed
  at the new API.

### 6.8 Share a meeting by email

- **See:** `show_change.py meetingcontrols`
- **0.3.10:** `lma-ai-stack/source/lambda_functions/meeting_controls_resolver/index.py`
- Add `resolve_usernames()` (email → Cognito username; needed because Entra ID usernames are GUIDs),
  env `COGNITO_USER_POOL_ID`, permission `cognito-idp:ListUsers`, timeout 180 s.

### 6.9 Project chatbot

- **See:** `show_change.py Mutation.sendProjectChatMessage`, `show_change.py schema`
- Schema: `SendProjectChatMessageInput`, `Mutation.sendProjectChatMessage`, `SendChatMessageOutput.Sources`.
- Resolver invokes the Lambda **`SyntheraProjectChatbot`** — code in
  `lambda/SyntheraProjectChatbot_src/`, role in `iam/SyntheraProjectChatbot-role-*`. It needs a
  Bedrock Knowledge Base ID and model ID (`lambda/SyntheraProjectChatbot.config.json`).
- **Decide with the team:** bring this Lambda into the LMA template, or keep it outside and pass its
  ARN as a parameter.

### 6.10 Login: Microsoft Entra ID (SSO)

- **Live:** `cognito/identity-provider-MicrosoftEntraID.json`, `cognito/user-pool.json`,
  `cognito/client-*.json`
- **0.3.10:** `lma-cognito-stack/deployment/lma-cognito-stack.yaml`

| Change | Notes |
|---|---|
| SAML identity provider `MicrosoftEntraID` | Metadata URL + attribute mapping in the JSON. New environments may need their own Entra app registration |
| Custom attributes `custom:jobtitle`, `custom:department`, `custom:manager` | User pool schema. **Custom attributes cannot be removed later** — add them deliberately |
| Web client uses `MicrosoftEntraID` only, with callback/logout URLs for `*.syntheraai.com` etc. | `UserPoolClient` → `SupportedIdentityProviders`, `CallbackURLs`, `LogoutURLs` |
| MCP client also allows `MicrosoftEntraID` | `MCPServerExternalAppClient` in `lma-ai-stack.yaml` |

---

## 7. How to Test

Deploy to a **new dev stack** (never over the live `LMASA` stack), then:

| # | Test | Pass when |
|---|---|---|
| 1 | Sign in through Entra ID | Lands in the app; your email shows top-right |
| 2 | Bot joins a Teams meeting, then a Zoom meeting | Name is `<First>'s AI Assistant`, not truncated; container log shows `Display name entered correctly` |
| 3 | Leave the bot alone with another bot | It leaves after ~60 s (`Auto-leave config:` line in the log) |
| 4 | Meetings list | Owner names shown, no `microsoftentraid_…` IDs |
| 5 | Two users in the same project | Each sees the project's meetings, live transcript streams to both |
| 6 | User **not** in the project | Cannot see the meeting (list, open, or live) |
| 7 | Share a meeting by email | Recipient sees it |
| 8 | Share a bot with another user | It appears in their VP list |
| 9 | Project chat | Gets an answer with sources |
| 10 | Create a bot from Synthera's backend | Owner is the right user |

`history/appsync_changes/ctx_*.json` are ready-made test inputs (member, stranger, owner…) for the
project-access resolvers.

## 8. Common Mistakes

- **Copying a live file over a 0.3.10 file.** Always port only the `+` lines.
- **Re-applying the old July change set.** Older docs in `history/` mention `apply_customizations.py`
  and `permanent_patches/`; they were left out on purpose because they restore the July state and
  would break the bot and Synthera's backend. Today's export is the source of truth.
- **Pasting ARNs from the export into templates.** They contain this account's IDs
  (`528757797189`, `…4VKHCQQY3CJR…`). Use `!Ref` / `!GetAtt` / `!Sub` instead.
- **Committing secrets.** `DOCDB_PASS`, client secrets and API keys are redacted in the export —
  get them from Secrets Manager.
- **Forgetting the stream filters** on the Enrichment trigger (6.4).
- **Forgetting data.** A new environment starts empty. For existing meetings, run
  `history/backfill_display_names.py` and recreate `ProjectMembership` rows (see
  `history/BACKEND_INTEGRATION_SPEC.md` §3).

## 9. Open Questions for the Team

1. Keep recordings ~100 years and meetings forever (6.1)?
2. Was lowering CloudFront TLS to `TLSv1` intentional (6.1)?
3. `ProjectMembership` table and `SyntheraProjectChatbot` Lambda: inside the template or outside (6.5, 6.9)?
4. Is there TypeScript source for the `name-fix-v7` bot changes (ask niketh)? Otherwise port from `container_patch/*.patch`.
5. Teams join method: stay on the browser method, or move to the Teams SDK (needs Azure Communication Services and a Teams tenant that allows external participants)? See 6.2a.
