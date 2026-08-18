# Zodiac Bank AI Security Lab — one-command workflow
#
#   make setup   # copy .env and generate strong local secrets
#   make up      # start the core lab (detects an inference provider)
#   make down    # stop services
#   make verify  # offline security evaluation + progression + UI typecheck
#   make test    # offline evaluation, progression, and scenario validation
#   make clean   # stop and remove containers/networks/volumes (keeps models)

SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help setup env up down logs verify test clean
.PHONY: core lite full bootstrap-secrets

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: env bootstrap-secrets ## One-time setup: .env + strong local secrets

env: ## Copy .env.example to .env if it does not exist
	@if [ -f .env ]; then echo "make: .env already exists (left untouched)"; \
	else cp .env.example .env && echo "make: created .env from .env.example"; fi

bootstrap-secrets: ## Generate/persist strong local secrets in .env
	@./scripts/bootstrap_secrets.sh

up: ## Start the core lab (detects inference provider)
	@RUNTIME=1 ./scripts/start_all.sh

core: ## Start core profile
	@RUNTIME=1 LAB_MODE=core ./scripts/start_all.sh

lite: ## Start core + protocol services
	@RUNTIME=1 LAB_MODE=lite ./scripts/start_all.sh

full: ## Start the complete lab
	@RUNTIME=1 LAB_MODE=full ./scripts/start_all.sh

down: ## Stop all services
	@RUNTIME=1 ./scripts/stop_all.sh

logs: ## Tail container logs
	@docker compose logs --tail=100 -f

verify: ## Offline security evaluation + progression + UI typecheck
	@python3 scripts/zodiac_bank_eval.py
	@python3 scripts/zodiac_bank_progression_test.py
	@node scripts/check_ui_types.mjs

test: ## Offline evaluation, scenario validation, and progression
	@python3 scripts/zodiac_bank_eval.py
	@python3 scripts/validate_zodiac_bank.py
	@python3 scripts/zodiac_bank_progression_test.py

clean: ## Stop and remove containers, networks, and volumes (keeps models)
	@RUNTIME=1 CONFIRM_CLEAN=1 ./scripts/clean_all.sh
