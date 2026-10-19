# history/ — Working Notes, Baselines and Backups for the Live Changes (Jul–Oct 2026)

The original working folder used while the live `LMASA` stack was being changed by hand
(downloaded as `LMASA-MeetingControlsLambda`). Copied here on 2026-10-19 so the reasoning and the
"before" snapshots sit next to the current export in `..`.

**The current live state is in `..` (refreshed 2026-10-19), not here.** Everything in this folder
describes the state up to 2026-07-28 unless a file says otherwise.

## What is here

| Path | What it is | Use it for |
|---|---|---|
| `*.md`, `PROJECT_ACCESS_DIAGRAM.drawio` | Design, steps, status and diagnosis for each change | Why a change exists and how it was verified |
| `appsync_current/` | AppSync resolvers **before** any change (2026-07-08, stock LMA 0.3.2) | Baseline: `diff` against `../appsync/` to isolate our edits |
| `appsync_changes/` | Test contexts for the project-access resolvers (`ctx_member`, `ctx_stranger`, `ctx_owner`, …) | Test inputs when re-implementing |
| `vp_fix_backups/` | Pre-change backups: stock VPScheduler code (`vpsched-backup_src/`), Step Function, task def `:3`, `createVirtualParticipant`, scheduler IAM, schema | Baseline for the Virtual Participant changes |
| `stock_0.3.2/` | Stock schema, MeetingControls code and resolver list from upstream tag `v0.3.2` | Baseline used by `../tools/show_change.py` |
| `backfill_display_names.py` | Data repair: owner display names + Owner fields on existing meetings | Data migration for a new environment |
| `full_code/` | VP container build files at the `name-fix-v1` stage (`Dockerfile`, `entrypoint.sh`, `local-test.sh`) | How the patched image was built |

## Read in this order

1. `PROJECT_ACCESS_DESIGN.md` → `PROJECT_ACCESS_STATUS.md` — project-based meeting access
2. `VP_DISPLAY_NAME_FIX.md` → `VP_CHANGES_APPLIED.md` — bot display name + typing race
3. `MEETING_DISPLAY_NAMES_APPLIED.md`, `LIVE_SUBSCRIPTION_FIX.md`
4. `BACKEND_INTEGRATION_SPEC.md`, `FRONTEND_CHANGES_REQUIRED.md` — what Synthera's backend/frontend must do
5. `PROJECT_TAGGING_RACE_ISSUE.md` (2026-10-05) — explains the 10-06 Enrichment Lambda change
6. `MAKE_CHANGES_PERMANENT.md`, `NEW_ENVIRONMENT_PLAYBOOK.md`

## Not covered by these notes

Changes made after 2026-07-28 have no write-up here — see `../STACK_CHANGES_INVENTORY.md` §0a:
VP sharing rewrite and indexes (08-10), AppSync `AWS_IAM` path for Synthera's backend, and the
`name-fix-v7` auto-leave container (September; compiled files in `../../container_patch/`).

## ⚠️ Warnings

- The docs mention `permanent_patches/` and `apply_customizations.py`. They were **deliberately not
  kept**: they held the 07-22 state, which today's export supersedes, and running the script against
  the live stack would restore task definition `:4` / image `name-fix-v1` / the old
  `createVirtualParticipant` — removing auto-leave and breaking bot creation from Synthera's backend.
  The original copy is in the author's Downloads folder if ever needed.
- Secrets were redacted on copy (13 meeting passcodes in `vp_fix_backups/schedules-backup.json`).
  Not copied: `appsync_updated/`, the draft templates in `appsync_changes/` and `full_code/dist/`
  (in-between states; `../appsync/` and `../../container_patch/` are final), vendored libraries,
  the stock MeetingControls package, the superseded enrichment zip, `streams.json`, `.claude/`, and
  the older `new_environment_export/` copy.
