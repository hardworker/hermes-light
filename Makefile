SHELL := /bin/bash
PYTHON ?= python3
NODE ?= node
.DEFAULT_GOAL := help
.PHONY: help test update release build notarize publish cask clean

help:
	@echo "Hermes Light client and Homebrew tap"
	@echo "make update TAG=vX.Y.Z [REVISION=N] | release | cask | clean | test"
	@echo "make build | notarize | publish"

test:
	@$(PYTHON) -m unittest discover -s tests -v
	@$(NODE) --test tests/*.test.mjs

update:
	@$(PYTHON) scripts/light_client_release.py update --tag "$(TAG)" $(if $(REVISION),--revision "$(REVISION)")

release:
	@$(PYTHON) scripts/light_client_release.py release

cask:
	@$(PYTHON) scripts/light_client_release.py cask

clean:
	@$(PYTHON) scripts/light_client_release.py clean

build:
	@$(PYTHON) scripts/light_client_release.py build

notarize:
	@$(PYTHON) scripts/light_client_release.py notarize

publish:
	@$(PYTHON) scripts/light_client_release.py publish
