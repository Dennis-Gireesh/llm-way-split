SHELL := /bin/bash
.DEFAULT_GOAL := help

UV ?= uv
COMPOSE ?= docker compose

.PHONY: help setup check test build run up up-ollama down logs sbom scan release-check

help: ## Show the available commands.
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target>\n\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Create the locked development environment.
	$(UV) sync --frozen --all-extras

check: ## Verify the lock, formatting, lint, and strict typing.
	$(UV) lock --check
	$(UV) run ruff format --check .
	$(UV) run ruff check .
	$(UV) run mypy

test: ## Run the complete test suite.
	$(UV) run pytest

build: ## Build wheel and source distributions from the lock state.
	$(UV) lock --check
	$(UV) build

run: ## Run WaySplit locally on 127.0.0.1:9876.
	$(UV) run waysplit serve

up: ## Build and start WaySplit in the background.
	$(COMPOSE) up --build --detach waysplit

up-ollama: ## Start WaySplit with the optional bundled Ollama service.
	$(COMPOSE) --profile ollama up --build --detach

down: ## Stop containers without deleting either persistent volume.
	$(COMPOSE) --profile ollama down --remove-orphans

logs: ## Follow WaySplit service logs.
	$(COMPOSE) logs --follow waysplit

sbom: ## Generate CycloneDX and SPDX SBOMs for the local image.
	./scripts/sbom.sh

scan: ## Scan locked dependencies, source, configuration, secrets, and image.
	./scripts/scan.sh

release-check: ## Confirm package, module, and optional tag versions agree.
	./scripts/check-version.sh "$(TAG)"
