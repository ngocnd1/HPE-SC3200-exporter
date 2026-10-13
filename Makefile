.PHONY: install-dev test run build

install-dev:
	python3 -m pip install -e '.[dev]'

test:
	python3 -m pytest -q

run:
	redfish-exporter --config config.yml

build:
	python3 -m build
