SERVICES := edge_gateway auth_svc catalog_svc cart_svc order_svc payment_worker notify_svc media_svc
REGISTRY ?= shopglobe
TAG      ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
COMPOSE  ?= $(shell if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; else echo "docker compose"; fi)

# Everything runs inside .venv. Ubuntu 23.04+ refuses system-wide pip installs
# (PEP 668 externally-managed-environment), and a venv is the right answer
# regardless -- it keeps this project's deps out of the system python.
VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/python -m pip

.PHONY: install lint fmt test up down logs build build-distroless podman buildah clean doctor

$(VENV)/pyvenv.cfg:
	python3 -m venv $(VENV) || (echo ">> run: sudo apt update && sudo apt install -y python3-venv"; exit 1)

install: $(VENV)/pyvenv.cfg
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt
	@echo ">> ready. activate with: source $(VENV)/bin/activate"

doctor:
	@python3 --version
	@$(PY) --version 2>/dev/null || echo "venv missing -- run: make install"
	@docker --version 2>/dev/null || echo "docker missing"
	@$(COMPOSE) version 2>/dev/null || echo "docker compose plugin missing"

lint: ; $(VENV)/bin/ruff check . && $(VENV)/bin/ruff format --check . && $(VENV)/bin/mypy libs services
fmt:  ; $(VENV)/bin/ruff format . && $(VENV)/bin/ruff check --fix .
test: ; $(PY) -m pytest

up:   ; $(COMPOSE) up -d --build && $(COMPOSE) ps
down: ; $(COMPOSE) down -v
logs: ; $(COMPOSE) logs -f --tail=50

# one image, eight tags -- CMD selects the service at runtime
build:            ; docker build -t $(REGISTRY)/app:$(TAG) .
build-distroless: ; docker build -f Dockerfile.distroless -t $(REGISTRY)/app:$(TAG)-distroless .

# Day 1 step 20: same Dockerfile, three build tools
podman:  ; podman build -t $(REGISTRY)/app:$(TAG)-podman .
buildah: ; buildah bud -t $(REGISTRY)/app:$(TAG)-buildah .

clean: ; find . -type d -name __pycache__ -prune -exec rm -rf {} + ; rm -rf .pytest_cache .ruff_cache .mypy_cache
