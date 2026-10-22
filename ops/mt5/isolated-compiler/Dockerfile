# Standalone Wine compiler environment. Never mount or modify a live MT5 Wine prefix.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && install -d -m 0755 /etc/apt/keyrings \
 && curl -fsSL https://dl.winehq.org/wine-builds/winehq.key \
       | gpg --dearmor -o /etc/apt/keyrings/winehq-archive.key \
 && curl -fsSL https://dl.winehq.org/wine-builds/ubuntu/dists/noble/winehq-noble.sources \
       -o /etc/apt/sources.list.d/winehq-noble.sources \
 && apt-get update \
 && apt-get install -y --install-recommends winehq-staging xvfb x11vnc x11-utils fonts-liberation \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 11050 --shell /bin/bash compiler \
 && mkdir -p /workspace \
 && chown compiler:compiler /workspace
COPY --chmod=0755 entrypoint.sh /usr/local/bin/dkcapital-compiler-entrypoint
USER compiler
ENV HOME=/home/compiler WINEPREFIX=/home/compiler/.wine DISPLAY=:99
EXPOSE 5903
ENTRYPOINT ["/usr/local/bin/dkcapital-compiler-entrypoint"]
