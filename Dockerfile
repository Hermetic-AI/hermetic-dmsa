ARG PYTHON_IMAGE=python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a
FROM ${PYTHON_IMAGE} AS builder
WORKDIR /build
COPY vendor/ vendor/
COPY deploy/requirements.lock deploy/requirements.lock
RUN sha256sum -c vendor/SHA256SUMS \
    && pip wheel --no-cache-dir --require-hashes --find-links vendor \
       -r deploy/requirements.lock --wheel-dir /wheels
COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_LICENSES.md MANIFEST.in ./
COPY src/ src/
RUN pip wheel --no-cache-dir --no-deps . --wheel-dir /wheels

FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="DMSA Coding Memory" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/Hermetic-AI/hermetic-dmsa"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 dmsa && useradd --uid 10001 --gid 10001 --no-create-home dmsa \
    && mkdir /data /app && chown 10001:10001 /data /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels 'hermetic-dmsa[http,vector]' \
    && rm -r /wheels
COPY LICENSE NOTICE THIRD_PARTY_LICENSES.md /usr/share/licenses/hermetic-dmsa/
COPY vendor/README.md /usr/share/licenses/hermetic-dmsa/PMD-SOURCE.md
WORKDIR /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD ["dmsa-memory-smoke", "--health-only"]
ENTRYPOINT ["dmsa-memory-http"]
CMD ["--profile", "sparse-dev", "--host", "0.0.0.0", "--storage", "/data/coding.sqlite3"]
