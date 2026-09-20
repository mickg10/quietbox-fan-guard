FROM python:3.13-alpine@sha256:1a63a53928ce53d2b0baf08092a703f4840ac5dfbd61fd48802dbf48e08c801e
ARG REVISION=development
LABEL org.opencontainers.image.source="https://github.com/mickg10/quietbox-fan-guard" \
      org.opencontainers.image.revision=$REVISION
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY fan_guard.py ./
HEALTHCHECK --interval=5s --timeout=4s --start-period=10s --retries=1 \
  CMD ["python", "/app/fan_guard.py", "watchdog"]
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "/app/fan_guard.py"]
CMD ["run"]
