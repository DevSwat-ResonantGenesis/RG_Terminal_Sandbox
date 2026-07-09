# Per-terminal sandbox image. One container per terminal_id, kept alive with
# `sleep infinity`; the controller attaches a shell via `docker exec`.
# This image never talks to the Docker socket itself - it's the thing being sandboxed.
FROM node:20-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        openssh-client \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code \
    && groupadd -g 10001 sandboxuser \
    && useradd -m -u 10001 -g sandboxuser -s /bin/bash sandboxuser

WORKDIR /workspace
RUN chown -R sandboxuser:sandboxuser /workspace

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && chown sandboxuser:sandboxuser /usr/local/bin/entrypoint.sh

COPY terminal-banner.sh /etc/terminal-banner.sh
RUN echo '. /etc/terminal-banner.sh' >> /etc/bash.bashrc

USER sandboxuser
ENV HOME=/home/sandboxuser

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
