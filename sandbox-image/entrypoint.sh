#!/bin/bash
# Secrets are bind-mounted read-only at /run/secrets.env by docker_manager.py
# (Phase 6). We copy to a private in-container tmpfs path, source it, then
# shred the copy so no plaintext secret file lingers inside the container
# either - only ever briefly in the shell's own environment.
set -e

if [ -f /run/secrets.env ]; then
    cp /run/secrets.env /tmp/.secrets_env
    chmod 600 /tmp/.secrets_env
    set -a
    # shellcheck disable=SC1091
    source /tmp/.secrets_env
    set +a
    shred -u /tmp/.secrets_env 2>/dev/null || rm -f /tmp/.secrets_env

    if [ -n "$GITHUB_TOKEN" ]; then
        git config --global credential.helper store
        echo "https://${GITHUB_TOKEN}@github.com" > "$HOME/.git-credentials"
        chmod 600 "$HOME/.git-credentials"
    fi
fi

exec "$@"
