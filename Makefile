# Always use an isolated venv built from a real Homebrew Python.
# Avoids the broken/polluted Anaconda base env (bad interpreter + conda
# dependency-resolver conflicts on bare `pip install`).

PY ?= /opt/homebrew/bin/python3.13
VENV := .venv
BIN := $(VENV)/bin

.PHONY: venv install run auth sync clean reinstall

$(BIN)/python:
	$(PY) -m venv $(VENV)
	$(BIN)/python -m pip install --quiet --upgrade pip

venv: $(BIN)/python ## create the venv

install: venv ## install the package (editable) into the venv
	$(BIN)/python -m pip install -e .
	@echo "OK — run with: make run ARGS='--help'  (or ./$(BIN)/seb-sync ...)"

run: ## run the CLI: make run ARGS='sync --dry-run'
	$(BIN)/seb-sync $(ARGS)

auth: ## one-time BankID consent
	$(BIN)/seb-sync auth

sync: ## dry-run sync preview
	$(BIN)/seb-sync sync --dry-run

reinstall: clean install ## nuke venv and reinstall

clean: ## remove the venv
	rm -rf $(VENV)
