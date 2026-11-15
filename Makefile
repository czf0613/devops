.PHONY: setup env db-up db-down migrate config-init config-list server worker website cli deploy server-deploy gateway-deploy website-deploy cli-deploy http3-check check test

DEPLOY_HOST ?= kdevops
COMPONENTS ?= all
DEPLOY_ARGS ?=
HTTP3_ARGS ?=

setup: env
	uv sync --project server --locked
	uv sync --project runner --locked
	pnpm --dir website install --frozen-lockfile
	pnpm --dir cli install --frozen-lockfile
	$(MAKE) db-up
	$(MAKE) migrate
	$(MAKE) config-init

env:
	python3 scripts/init_env.py

db-up: env
	docker compose up -d --wait postgres redis

db-down:
	docker compose down

migrate:
	uv run --project server alembic -c server/alembic.ini upgrade head

config-init:
	uv run --project server python -m devops_server.config_cli init

config-list:
	uv run --project server python -m devops_server.config_cli list

server: env
	uv run --project server uvicorn devops_server.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers --reload --reload-dir server/src

worker: env
	uv run --project server python -m devops_server.collaboration_worker

website:
	pnpm --dir website dev

cli:
	pnpm --dir cli build

cli-deploy:
	./deploy.sh cli $(DEPLOY_ARGS)

deploy:
	./deploy.sh $(COMPONENTS) $(DEPLOY_ARGS)

server-deploy:
	./deploy.sh server $(DEPLOY_ARGS)

gateway-deploy:
	./deploy.sh gateway $(DEPLOY_ARGS)

website-deploy:
	uv run --project server python scripts/deploy_website.py publish --host $(DEPLOY_HOST)

http3-check:
	uv run --project server python scripts/verify_http3.py $(HTTP3_ARGS)

test:
	pnpm --dir cli test
	uv run --project server pytest server/tests
	uv run --project runner pytest runner/tests
	pnpm --dir website test

check:
	uv run --project server ruff check server scripts runner
	uv run --project server ruff format --check server scripts runner
	$(MAKE) test
	uv run --project server alembic -c server/alembic.ini check
	pnpm --dir website build
