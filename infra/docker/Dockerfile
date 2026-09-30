# Self-contained runner for the QEMU functional suite.
# Useful where qemu-system-arm isn't installable (no sudo, old distro, macOS/Windows):
#
#   make docker-test
#
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      qemu-system-arm ipmitool squashfs-tools python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
COPY pyproject.toml README.md ./
COPY src ./src
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install -q -e '.[test]'
ENV PATH=/opt/venv/bin:$PATH

COPY tests ./tests
COPY infra/docker/run-functional.sh /usr/local/bin/run-functional
ENTRYPOINT ["run-functional"]
