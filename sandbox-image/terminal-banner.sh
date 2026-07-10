# Sourced from /etc/bash.bashrc on every interactive `docker exec` shell
# (can't live under ~/.bashrc - HOME is a tmpfs mount that's wiped at
# container start, see docker_manager.py's --tmpfs=/home/sandboxuser).
if [ -n "$PS1" ]; then
    if command -v claude >/dev/null 2>&1; then
        claude_version="$(claude --version 2>/dev/null || echo 'unknown')"
        echo "Claude Code CLI: $claude_version"
    fi
    if [ -z "$ANTHROPIC_API_KEY" ]; then
        echo "[notice] No Anthropic API key found for this session - add one under BYOK settings, then reopen this terminal, to use 'claude'."
    fi
    if [ -n "$RG_WORKSPACE_TOKEN" ]; then
        echo "Platform API access: \$RG_WORKSPACE_TOKEN is set (agents:*, builder:*) - e.g.:"
        echo "  curl -H \"Authorization: Bearer \$RG_WORKSPACE_TOKEN\" https://dev-swat.com/api/v1/agents/"
    fi
fi
