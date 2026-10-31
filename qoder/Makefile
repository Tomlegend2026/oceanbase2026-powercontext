# RecallCoach 常用命令（Linux/macOS）
# 用法：make install / make test / make run

PY ?= python3
APP := app
VENV := $(APP)/.venv
PORT ?= 8080

.PHONY: install test run clean

install:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/python -m pip install -q --upgrade pip
	$(VENV)/bin/python -m pip install -q -e $(APP)

test: install
	cd $(APP) && .venv/bin/python -m pytest -q

run: install
	PORT=$(PORT) sh run.sh

clean:
	rm -rf $(VENV) $(APP)/.pytest_cache
