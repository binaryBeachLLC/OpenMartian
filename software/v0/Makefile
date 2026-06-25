# OpenMartian dev — one place to run everything that works together.
#   make up        ensure ollama is running, then serve the web console + API (VPN-accessible)
#   make help      list all targets

SHELL      := /bin/bash
PORT       ?= 8000
HOST       ?= 0.0.0.0
PY         ?= python3
OLLAMA_URL ?= http://localhost:11434

# NetBird VPN address of this host (for access from other machines on the mesh).
VPN_IP := $(shell ip -4 addr show wt0 2>/dev/null | grep -oP 'inet \K[0-9.]+' | head -1)

.DEFAULT_GOAL := help

.PHONY: help install up serve dev check ollama models pull-models capture list process url clean clean-data

help: ## Show this help + access URLs
	@echo "OpenMartian dev console — targets:"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-13s\033[0m %s\n",$$1,$$2}'
	@echo
	@$(MAKE) --no-print-directory url

install: ## Install Python deps (fastapi, uvicorn, requests, dotenv)
	$(PY) -m pip install -r requirements.txt

up: check ## Run EVERYTHING: ensure ollama is up, then serve the console (VPN-accessible)
	@$(MAKE) --no-print-directory ollama
	@$(MAKE) --no-print-directory serve

serve: ## Serve the web UI + API, bound to $(HOST):$(PORT) (reachable over the VPN)
	@$(MAKE) --no-print-directory url
	@echo
	$(PY) -m uvicorn webapp.app:app --host $(HOST) --port $(PORT)

dev: ## Serve with auto-reload (local development)
	$(PY) -m uvicorn webapp.app:app --host $(HOST) --port $(PORT) --reload

check: ## Verify ffmpeg, ollama, and the Claude key are ready
	@command -v ffmpeg >/dev/null && echo "ffmpeg     : ok" || echo "ffmpeg     : MISSING"
	@curl -s -m2 $(OLLAMA_URL)/api/version >/dev/null && echo "ollama     : up" || echo "ollama     : down (run 'make ollama')"
	@(test -f .env && grep -q 'ANTHROPIC_API_KEY=.' .env && echo "claude key : set (.env)") || echo "claude key : not set (.env)"

ollama: ## Start 'ollama serve' in the background if it isn't already running
	@curl -s -m2 $(OLLAMA_URL)/api/version >/dev/null && echo "ollama already running" || \
	  ( nohup ollama serve >/tmp/ollama.log 2>&1 & sleep 1; echo "started 'ollama serve' (log: /tmp/ollama.log)" )

models: ## List local Ollama models
	@ollama list

pull-models: ## Pull the vision models the recipes use
	ollama pull gemma3:4b
	ollama pull llama3.2-vision:11b
	ollama pull llava

capture: ## Capture an episode from the webcam   (e.g. make capture ARGS="--count 3")
	$(PY) -m openmartian capture $(ARGS)

list: ## List saved episodes
	$(PY) -m openmartian list

process: ## Process the latest episode            (e.g. make process ARGS="--backend claude")
	$(PY) -m openmartian process --latest $(ARGS)

url: ## Print the local + VPN URLs for the console
	@echo "  Local : http://localhost:$(PORT)/         (API docs: http://localhost:$(PORT)/docs)"
	@if [ -n "$(VPN_IP)" ]; then \
	  echo "  VPN   : http://$(VPN_IP):$(PORT)/    (API docs: http://$(VPN_IP):$(PORT)/docs)"; \
	else \
	  echo "  VPN   : NetBird interface 'wt0' not found — is netbird up?"; \
	fi

clean: ## Remove Python __pycache__ dirs
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

clean-data: ## DELETE all episodes + run history + app state (destructive!)
	rm -rf episodes data
	@echo "removed episodes/ and data/"
