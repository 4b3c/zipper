# The zipper runtime. (ttyd is the upstream static build: Debian 13 dropped the package.
# gh comes from GitHub's own apt repository: `gh` calls and HTTPS pushes through its
# credential helper need it.)
# The image holds tools, not code: the code is a git
# checkout on a volume (/zipper/code), because a zipper pulls its own updates
# and restarts itself -- which code baked into an image could not do. The copy
# in /opt/zipper-seed only seeds that checkout on first boot.
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git tmux nginx tini openssl curl ca-certificates procps nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /usr/local/bin/ttyd \
        "https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.$(uname -m)" \
    && chmod 755 /usr/local/bin/ttyd \
    && curl -fsSL -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
        https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /opt/zipper-seed
RUN install -m 755 /opt/zipper-seed/docker/entrypoint.sh /usr/local/bin/zipper-entrypoint \
    && printf '#!/bin/sh\nexec python3 -m zipper "$@"\n' > /usr/local/bin/zipper \
    && chmod 755 /usr/local/bin/zipper

# HOME is a volume: ~/.claude holds the Claude login and every transcript.
ENV HOME=/zipper/home \
    PYTHONPATH=/zipper/code \
    PYTHONUNBUFFERED=1 \
    ZIPPER_VAULT=/zipper/vault \
    ZIPPER_ENV_FILE=/zipper/config/.env \
    ZIPPER_NGINX=1 \
    ZIPPER_VAULT_DEFAULT=/zipper/vault \
    ZIPPER_BACKUP_DEFAULT=/zipper/backup

WORKDIR /zipper/code
# 8899: the dashboard and its terminals, behind basic auth.
# 8898: messages from other zippers, and nothing else.
EXPOSE 8899 8898
ENTRYPOINT ["tini", "--", "zipper-entrypoint"]
CMD ["run"]
