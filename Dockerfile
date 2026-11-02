# Minimal image to chat with the Mel without installing Python (D-25).
# Build: docker build -t mel .
# Chat:  docker run -it --rm --env-file .env mel     (or: make docker-chat)
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so code changes do not reinstall them
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

COPY . .
RUN uv sync --locked

ENV PATH="/app/.venv/bin:$PATH"

# The API key comes only from --env-file / -e at run time; .env is never copied (.dockerignore)
CMD ["python", "-m", "emporio", "chat"]
