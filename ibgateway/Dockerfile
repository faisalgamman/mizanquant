FROM ghcr.io/unusualalpha/ib-gateway:latest

ENV TRADING_MODE=paper \
    TWS_USERID="" \
    TWS_PASSWORD="" \
    VNC_SERVER_PASSWORD=mizanquant

EXPOSE 4001 4002 5901

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:4002/healthz || exit 1
