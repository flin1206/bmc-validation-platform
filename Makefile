SHELL := /bin/bash
PY      ?= python3
VENV    ?= .venv
BIN     := $(VENV)/bin
MACHINE ?= romulus
BUILD   ?= lastSuccessfulBuild
PROFILE ?= qemu-$(MACHINE)
IMAGES  := build/images/$(MACHINE)

.PHONY: help venv lint unit fetch boot test test-all stop scan cve-diff report gpu jenkins-up jenkins-down clean

help:  ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

venv:  ## Create virtualenv and install bmcval
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q -e '.[dev]'

lint:  ## ruff + shellcheck (if installed)
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests
	@command -v shellcheck >/dev/null && shellcheck scripts/*.sh infra/packer/scripts/*.sh || true

unit:  ## Unit tests (no BMC needed)
	$(BIN)/pytest tests/unit -q

fetch:  ## Download firmware (MACHINE=, BUILD=) from upstream OpenBMC CI
	$(BIN)/bmcval fetch --machine $(MACHINE) --build $(BUILD)

boot:  ## Boot the newest fetched image under QEMU and wait for Redfish
	$(BIN)/bmcval boot --profile $(PROFILE) --image-dir $(IMAGES)

test:  ## Non-destructive functional tests against the running BMC
	$(BIN)/pytest tests/functional --profile $(PROFILE)

test-all:  ## Functional tests including destructive ones
	$(BIN)/pytest tests/functional --profile $(PROFILE) --run-destructive

stop:  ## Stop QEMU
	$(BIN)/bmcval stop

scan:  ## SBOM + CVE scan of the newest fetched image
	PATH=$(BIN):$$PATH scripts/firmware-scan.sh $$(ls -dt $(IMAGES)/*/ | head -1) reports/security/candidate

cve-diff:  ## Gate candidate vs baseline (BASELINE=path/to/grype.json)
	$(BIN)/bmcval cve-diff --baseline $(BASELINE) --candidate reports/security/candidate/grype.json \
	  --policy security/policy.yaml --markdown reports/security/cve-delta.md --junit reports/security-cve-gate.xml

report:  ## Build the HTML dashboard from reports/
	$(BIN)/bmcval report

gpu:  ## Build the NVML health tool (needs CUDA toolkit >= 12.2)
	cmake -S gpu -B build/gpu -DCMAKE_BUILD_TYPE=Release && cmake --build build/gpu -j

jenkins-up:  ## Start the Jenkins lab on http://localhost:8080
	docker compose -f infra/jenkins/docker-compose.yml up -d --build

jenkins-down:
	docker compose -f infra/jenkins/docker-compose.yml down

clean:
	rm -rf build reports .pytest_cache
