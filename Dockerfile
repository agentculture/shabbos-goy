# Container image for `shabbos-goy listen` — the ambient loop the Docker
# Compose service runs (see docker-compose.yml for how, and its own
# top comment for the networking decision).
#
# Base: python:3.12-slim, which today (verified live at build time while
# writing this task: `docker run --rm python:3.12-slim cat /etc/os-release`)
# is Debian 13 "trixie" — multi-arch (amd64/arm64), matching the workspace's
# house style (see ../climate-cli/Dockerfile).
#
# PipeWire client tools: audio is the HOST PipeWire session (decision c42,
# see CLAUDE.md's "Speech stack" section) — this image installs only the
# CLIENT binaries the runtime shells out to (`pw-record`, `pw-play`,
# `wpctl`), never a PipeWire *server*, and mounts the host's PipeWire socket
# at runtime (see docker-compose.yml) instead of `/dev/snd`.
#
# Verified live, while writing this task, against python:3.12-slim itself
# (not merely looked up): `pipewire-bin` (candidate 1.4.2-1 on trixie)
# provides /usr/bin/pw-record and /usr/bin/pw-play, and `wireplumber`
# (candidate 0.5.8-2) provides /usr/bin/wpctl — confirmed by running
# `apt-get install -y --no-install-recommends pipewire-bin wireplumber`
# inside a throwaway python:3.12-slim container and then `which pw-record
# pw-play wpctl`, all three resolved. No image was ever run with real audio
# or a real key as part of that check.
FROM python:3.12-slim

# Same uid/gid as the host user that owns the PipeWire socket — pass
# --build-arg UID=$(id -u) --build-arg GID=$(id -g) if they differ from the
# default. docker-compose.yml's `user:` pins the *running* uid the same way;
# both must agree with the host user for the bind-mounted PipeWire socket at
# runtime to be usable.
ARG UID=1000
ARG GID=1000

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        pipewire-bin \
        wireplumber \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "${GID}" shabbos-goy \
    && useradd --uid "${UID}" --gid "${GID}" --shell /usr/sbin/nologin --create-home shabbos-goy

WORKDIR /app

# Copy only what's needed to resolve and install the package first, so
# dependency layers cache independently of application source changes
# (house style, see ../climate-cli/Dockerfile).
COPY pyproject.toml README.md ./
COPY shabbos_goy ./shabbos_goy

# This package itself declares zero runtime dependencies (`dependencies = []`
# in pyproject.toml) — sensibo-cli is the one thing installed alongside it,
# pinned to an EXACT version (never a range, never `latest`) so the `sensibo`
# console script lands on PATH deterministically. Bump this line deliberately
# (and note it in CHANGELOG.md) to pick up a new sensibo-cli release.
RUN pip install --no-cache-dir . \
    && pip install --no-cache-dir sensibo-cli==0.8.1

# Read-only user config and the PipeWire socket are bind-mounted at runtime
# (see docker-compose.yml); nothing default is baked into the image.
RUN mkdir -p /app/config \
    && chown -R shabbos-goy:shabbos-goy /app

USER shabbos-goy

# Default: dry-run. Actuating (`--apply`) is an explicit operator choice —
# see the commented `command:` override in docker-compose.yml. Never make
# `--apply` the default here.
CMD ["shabbos-goy", "listen"]
