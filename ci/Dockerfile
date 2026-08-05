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

FROM node:20-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        make \
        zip \
        unzip \
        python3 \
        python3-pip \
        python3-venv \
        docker.io \
        curl \
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
# would on a real workstation.
RUN pip3 install --break-system-packages --no-cache-dir virtualenv aws-sam-cli

# Sanity check baked into the image build itself - if any tool is missing,
# the image fails to build rather than failing silently at pipeline time.
RUN node --version && npm --version && make --version && zip --version \
    && python3 --version && pip3 --version && virtualenv --version \
    && docker --version && aws --version && sam --version
