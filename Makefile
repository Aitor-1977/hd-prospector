# Conectores de ingesta Capa 0 (leen credenciales de .env).
PYTHON ?= python3
DATASET ?=
URL ?=
ORG ?=
LANG ?= es

.PHONY: help install test apify youtube
help:
	@echo "make install        # instala requirements"
	@echo "make test           # corre las pruebas"
	@echo "make apify DATASET=<id>"
	@echo "make youtube URL=<video> ORG=\"Acme\" LANG=es"

install:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m pytest -q

apify:
	$(PYTHON) -m hd_scraper.ingesta apify --dataset "$(DATASET)"

youtube:
	$(PYTHON) -m hd_scraper.ingesta youtube --url "$(URL)" --org "$(ORG)" --lang "$(LANG)"
