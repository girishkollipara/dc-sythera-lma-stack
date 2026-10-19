# Reproduces the live VP image `name-fix-v7` (task definition :15) on top of the
# stock 14-May image. Base is pinned by digest so this cannot silently pick up a
# different image.
#
# The live image was built in two layers; the files here are the final result of both:
#   name-fix-v1 (2026-07-16)  name-entry.js, teams.js, zoom.js   - reliable display-name entry
#   name-fix-v7 (2026-09-29)  details.js, scribe.js, teams.js    - auto-leave (no humans / idle)
#
# Auto-leave is off unless the task definition sets these (live :15 values shown):
#   BOT_NAME_SUFFIX="'s AI Assistant, Otter.ai, Fireflies, Notetaker"
#   BOT_NAME_PREFIX="LMA ("
#   NO_HUMANS_GRACE_SECONDS=60  IDLE_TIMEOUT_MINUTES=20  WATCHDOG_CHECK_SECONDS=10
FROM 528757797189.dkr.ecr.us-east-1.amazonaws.com/lmasa-virtualparticipantstack-1iavizt1439r2-imagerepo-l3yrnekcbuxx@sha256:32cf092cb0b864564d2dc5b9f8cc8328a8a3a6f0129f0e03ad6464e3574fa463
USER root
COPY --chown=appuser:appuser name-entry.js teams.js zoom.js details.js scribe.js /srv/dist/
USER appuser
