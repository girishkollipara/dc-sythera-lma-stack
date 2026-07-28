# Name-entry reliability patch on top of the 14-May image.
# Base is pinned by digest so this cannot silently pick up a different image.
FROM 528757797189.dkr.ecr.us-east-1.amazonaws.com/lmasa-virtualparticipantstack-1iavizt1439r2-imagerepo-l3yrnekcbuxx@sha256:32cf092cb0b864564d2dc5b9f8cc8328a8a3a6f0129f0e03ad6464e3574fa463
USER root
COPY --chown=appuser:appuser name-entry.js teams.js zoom.js /srv/dist/
USER appuser
