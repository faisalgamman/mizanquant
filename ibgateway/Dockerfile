FROM ghcr.io/gnzsnz/ib-gateway:10.46.1g

ENV TRADING_MODE=paper \
    TWS_USERID="" \
    TWS_PASSWORD="" \
    VNC_SERVER_PASSWORD=mizanquant

# The gnzsnz/ib-gateway image exposes the IB API on socat port
# 4004 (paper) / 4003 (live) — see https://github.com/gnzsnz/ib-gateway-docker
EXPOSE 4001 4002 4003 4004 5901

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD pgrep -x socat >/dev/null || exit 1
