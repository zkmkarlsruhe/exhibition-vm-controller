# Conservation toolkit — everything that would otherwise "bleed" onto the host
# lives here. Build context is the conservation/ directory.
#   docker build -f docker/Dockerfile -t cons-toolkit .
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends tcpdump iproute2 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir mitmproxy

# Drop-in virsh: forwards to the host virsh-broker over a socket. No libvirt in
# the container, so existing scripts run unchanged.
COPY docker/virsh /usr/local/bin/virsh
RUN chmod +x /usr/local/bin/virsh

COPY . /opt/conservation
WORKDIR /opt/conservation
RUN chmod +x docker/entrypoint.sh

ENV CONS_VIRSH_SOCK=/run/cons/virsh.sock \
    CONS_ARCHIVE=/archive \
    CONS_CONFDIR=/cadir

ENTRYPOINT ["/opt/conservation/docker/entrypoint.sh"]
CMD ["proxy"]
