.PHONY: help install check-github gateway fix-binary camera-off camera-on jaeger jaeger-stop up down verify demo preflight models clean

help:
	@echo "NOTE: ./imagine is the friendlier front door -- ./imagine start"
	@echo "      does 'up' then the Director in one command. Same scripts."
	@echo ""
	@echo "make install     - create venv, install deps"
	@echo "make gateway     - install the pinned agentgateway into ./bin"
	@echo "make fix-binary  - fix a macOS binary that dies with 'killed'"
	@echo "make up          - start vision-mcp, scout, dp, agentgateway"
	@echo "make jaeger      - start Jaeger for the trace view (docker)"
	@echo "make check-github- pinpoint where GitHub auth breaks"
	@echo "make verify      - smoke-test every leg through the gateway"
	@echo "make demo        - run the Director REPL"
	@echo "make models      - list models your keys can actually use"
	@echo "make camera-off  - release the camera (green light off)"
	@echo "make camera-on   - pick it back up"
	@echo "make preflight   - day-of checks"
	@echo "make down        - stop everything"

VENV=.venv
PY=$(VENV)/bin/python
PIP=$(VENV)/bin/pip

install:
	python3 -m venv $(VENV)
	$(PIP) install -q -U pip
	$(PIP) install -q -r servers/requirements.txt -r agent/requirements.txt
	$(PIP) install -q -r ../mcp-server/requirements.txt
	@echo "ok. now: cp .env.example .env && edit it"

check-github:
	@bash scripts/check-github.sh

gateway:
	@bash scripts/install-gateway.sh

fix-binary:
	@bash scripts/fix-macos-binary.sh $(BIN)

camera-off:
	@curl -s "http://localhost:$${PREVIEW_PORT:-8888}/release" && echo

camera-on:
	@curl -s "http://localhost:$${PREVIEW_PORT:-8888}/resume" && echo

jaeger:
	@bash scripts/jaeger.sh up

jaeger-stop:
	@bash scripts/jaeger.sh down

up:
	@bash scripts/up.sh

down:
	@bash scripts/down.sh

verify:
	@bash scripts/verify.sh

demo:
	@set -a; . ./.env; set +a; \
	env -u GEMINI_API_KEY -u GOOGLE_API_KEY -u OPENAI_API_KEY \
	  $(PY) -m agent.director || true

preflight:
	@bash scripts/preflight.sh

models:
	@set -a; . ./.env; set +a; \
	echo "GEMINI:"; \
	curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=$$GEMINI_API_KEY" \
	  | $(PY) -c 'import json,sys; [print("  ", m["name"].replace("models/","")) for m in json.load(sys.stdin).get("models",[])]' \
	  2>/dev/null || echo "  (failed -- check GEMINI_API_KEY)"; \
	if [ -n "$$OPENAI_API_KEY" ]; then echo "OPENAI:"; \
	  curl -s https://api.openai.com/v1/models -H "Authorization: Bearer $$OPENAI_API_KEY" \
	  | $(PY) -c 'import json,sys; [print("  ", m["id"]) for m in sorted(json.load(sys.stdin).get("data",[]), key=lambda x: x["id"])]' \
	  2>/dev/null || echo "  (failed)"; \
	else echo "OPENAI: no key set"; fi

clean:
	rm -rf outputs/* data.db .pids
