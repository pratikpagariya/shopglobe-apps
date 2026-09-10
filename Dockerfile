# syntax=docker/dockerfile:1.7
# ONE Dockerfile for all 8 services. Which service runs is decided by CMD,
# which is exactly what ENTRYPOINT-vs-CMD is for.
ARG PY=3.12

# ---- builder: compilers and wheels live here and are thrown away ----
FROM python:${PY}-slim AS builder
WORKDIR /build
# requirements FIRST so this layer caches; a source edit must not reinstall deps
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- runtime ----
FROM python:${PY}-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
RUN useradd --uid 1000 --create-home app
COPY --from=builder /install /usr/local
WORKDIR /app
COPY libs ./libs
COPY services ./services
USER 1000
EXPOSE 8000

# EXEC form: python IS pid 1 and receives SIGTERM, so the drain in
# libs/common/app.py actually runs and `docker stop` returns in ~5s.
#
# DAY 1 EXPERIMENT: replace the two lines below with the shell form
#     CMD python -m services.edge_gateway.main
# then `docker stop` and time it. /bin/sh becomes pid 1, swallows SIGTERM,
# and Docker waits the full 10s before SIGKILL. Change it back.
ENTRYPOINT ["python", "-m"]
CMD ["services.edge_gateway.main"]
