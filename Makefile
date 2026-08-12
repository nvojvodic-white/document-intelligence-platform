.PHONY: up down logs seed test lint rebuild

# Two import roots, matching the Dockerfile and CI.
export PYTHONPATH := services/api:packages

# The one-command path from a clean clone: API, sync worker, LocalStack (with
# the corpus seeded into S3), and the web UI.
up:
	docker compose up --build

down:
	docker compose down -v

logs:
	docker compose logs -f api worker

# Re-seed the LocalStack bucket from corpus/tolkien without touching the rest
# of the stack.
seed:
	docker compose run --rm seed

test:
	python -m pytest tests/ -v --tb=short

lint:
	ruff check services/ packages/

# Rebuild the image and restart the two Python services so code changes go
# live. Without the rebuild, `docker compose restart` runs the old image and
# silently serves stale code.
rebuild:
	docker compose up -d --build api worker
