.PHONY: install install_ml dev_db ingest ingest_quick refresh serve web test lint eval up down docker_ingest docker_refresh screenshots

PY ?= python3

install:            ## backend with dev tools (no torch)
	cd backend && $(PY) -m pip install -e ".[dev]"

install_ml:         ## backend with sentence-transformers and torch
	cd backend && $(PY) -m pip install -e ".[ml,dev]"

dev_db:             ## start PostgreSQL with pgvector only
	docker compose up -d db

ingest:             ## full corpus: all openFDA label partitions, shortages, Orange Book, RxClass
	cd backend && shortfall ingest-all

ingest_quick:       ## first two label partitions, for a fast local start
	cd backend && shortfall ingest-labels --max-partitions 2 && shortfall ingest-shortages \
	  && shortfall ingest-orange-book && shortfall enrich-classes

refresh:            ## shortages change daily; schedule this
	cd backend && shortfall ingest-shortages

serve:
	cd backend && shortfall serve --port 8000

web:
	cd frontend && npm install && npm run dev

test:
	cd backend && $(PY) -m pytest

lint:
	cd backend && ruff check shortfall_bridge tests
	cd frontend && npm run typecheck

eval:
	cd backend && shortfall evaluate --out-dir ../reports

up:
	docker compose up -d --build

down:
	docker compose down

docker_ingest:      ## full ingestion inside Docker
	docker compose --profile ingest run --rm ingest

docker_refresh:     ## daily shortage refresh inside Docker
	docker compose --profile ingest run --rm refresh

screenshots:        ## regenerate README screenshots from a running deployment
	$(PY) scripts/capture_screenshots.py --base http://localhost:8080 --out docs/screenshots
