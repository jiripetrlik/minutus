# syntax=docker/dockerfile:1

FROM python:3.14-slim AS builder

ENV UV_VERSION=0.9.26 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /build

# Copy dependency metadata separately so dependency installation remains cached
# when only application source changes.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.14-slim AS runtime

RUN apt-get update \
    && apt-get install --no-install-recommends -y bash ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system minutus \
    && useradd --system --gid minutus --create-home minutus \
    && mkdir -p /workspace \
    && chown minutus:minutus /workspace

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /workspace
USER minutus

ENTRYPOINT ["minutus"]
CMD ["--help"]
