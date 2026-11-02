.PHONY: setup lint format test chat chat-trace scenarios docker-build docker-chat

setup:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest -q

chat:
	uv run python -m emporio chat

chat-trace:
	uv run python -m emporio chat --trace

scenarios:
	uv run python scripts/run_scenarios.py

docker-build:
	docker build -t mel .

docker-chat: docker-build
	docker run -it --rm --env-file .env mel
