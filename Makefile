SHELL := /bin/bash
PYTHON ?= python3
.DEFAULT_GOAL := help
.PHONY: help test build signing-request import-certificate notarize publish

help:
	@echo "Hermes Light client and Homebrew tap"
	@echo "make test | build | signing-request | import-certificate | notarize | publish"

test:
	@$(PYTHON) -m unittest discover -s tests -v

build:
	@$(PYTHON) scripts/light_client_release.py build

signing-request:
	@$(PYTHON) scripts/light_client_release.py request

import-certificate:
	@$(PYTHON) scripts/light_client_release.py import-certificate

notarize:
	@$(PYTHON) scripts/light_client_release.py notarize

publish:
	@$(PYTHON) scripts/light_client_release.py publish
