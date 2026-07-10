# Base image for per-session Squid egress sidecars (opt-in user SSH targets).
# Deliberately ships with NO baked-in squid.conf - docker_manager.py
# `docker cp`s a freshly rendered, per-user conf into each container after
# `docker create`, before starting it (this container's own filesystem is
# separate from the real Docker host's, so a bind-mount source path from
# this service wouldn't be reachable by the daemon - docker cp works over
# the API instead, same trick used for /workspace file sync).
FROM debian:12-slim

RUN apt-get update && apt-get install -y --no-install-recommends squid \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 3128
CMD ["squid", "-N", "-d", "1"]
