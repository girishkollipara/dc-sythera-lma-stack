# Build environment for the LMA Jenkins pipeline.
#
# Referenced by the Jenkinsfile's top-level `agent { dockerfile { ... } } }`
# block, using Jenkins' Docker Pipeline plugin - Jenkins builds this image
# itself (cached by content hash, rebuilt automatically when this file
# changes) and runs every pipeline stage inside it. Nothing needs to be
# installed by hand on the Jenkins server itself.
#
# Reusable by design: any other job/pipeline that needs this same build
# toolchain can point at this same file, on this server or any other.
#
# Matches the prerequisites listed in CLAUDE.md: bash, node v18/20/22, npm,
# docker (running), zip, python3, pip3, virtualenv, aws cli, sam cli.
#
# Built on the Python image, not the Node one, and Node is added via its own
# official package repo rather than copied in from elsewhere. An earlier
# version of this file tried to copy a compiled Python straight from
# python:3.12-slim into node:20-bookworm - that broke, because the two
# images turned out to be built against different glibc versions despite
# both being "bookworm" (GLIBC_2.38 not found). Debian's own "python3" apt
# package was the original reason for needing this at all: it's pinned at
# 3.11.2, which predates a Python security feature (PEP 706, safe
# tarfile-extraction filters) that AWS SAM CLI now requires - it refuses to
# run with a version-check error otherwise. Starting from a real, current
# Python image and adding everything else on top avoids both problems at
# once.
FROM python:3.12-slim-bookworm

# Jenkins' Docker Pipeline plugin runs this container as its own numeric
# user ID (to keep workspace file ownership consistent with the host), not
# as a user that exists in this image's /etc/passwd. With no matching
# passwd entry, $HOME falls back to "/" - and npm/pip/aws-cli all try to
# write caches and config under $HOME, which only real root can write to
# at the filesystem root. Pointing HOME at /tmp (writable by any user,
# always) fixes this for every tool at once instead of chasing it
# tool-by-tool.
ENV HOME=/tmp

RUN apt-get update && apt-get install -y --no-install-recommends \
        make \
        zip \
        unzip \
        curl \
        gnupg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Node.js 20 (matches this repo's pinned version) via NodeSource's official
# repo - not a cross-image binary copy, so no glibc-compatibility risk.
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI only (no daemon - we talk to the host's daemon over the mounted
# socket). Debian's own "docker.io" package lags far behind and speaks an
# old API version that a current Docker Engine refuses to talk to
# ("client version 1.41 is too old"), so this pulls the CLI from Docker's
# own official repository instead, which stays current.
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# AWS CLI v2 (Debian's apt package is v1 / not reliably current, so this
# uses AWS's own installer, same as CLAUDE.md's stated prerequisite).
RUN curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install \
    && rm -rf /tmp/awscliv2.zip /tmp/aws

# virtualenv + AWS SAM CLI. --break-system-packages: this image is a
# disposable, single-purpose build environment, not a general-purpose
# machine, so Debian's PEP 668 protection doesn't apply here the way it
# would on a real workstation (and this base's own Python isn't
# Debian-patched to enforce it anyway - kept for safety regardless).
RUN pip3 install --break-system-packages --no-cache-dir virtualenv aws-sam-cli

# Sanity check baked into the image build itself - if any tool is missing,
# the image fails to build rather than failing silently at pipeline time.
RUN node --version && npm --version && make --version && zip --version \
    && python3 --version && pip3 --version && virtualenv --version \
    && docker --version && aws --version && sam --version
