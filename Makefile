VENV := .venv
PYTHON := $(VENV)/bin/python

# Override on the command line if 2000 is taken:  make run PORT=8000
PORT ?= 2000

.PHONY: install run demo seed report report-json halt resume status test

install:
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

run:
	@echo "Dashboard: http://127.0.0.1:$(PORT)"
	$(PYTHON) -m uvicorn dashboard.main:app --reload --port $(PORT)

# One command for a cold start: fresh batch, printed report, dashboard up.
demo: seed report
	@echo ""
	@echo "Dashboard: http://127.0.0.1:$(PORT)  (ctrl-c to stop)"
	@$(PYTHON) -m uvicorn dashboard.main:app --port $(PORT)

seed:
	$(PYTHON) -m scripts.seed

report:
	$(PYTHON) -m scripts.report

report-json:
	$(PYTHON) -m scripts.report --json

halt:
	@$(PYTHON) -m scripts.halt

resume:
	@$(PYTHON) -m scripts.halt --resume

status:
	@$(PYTHON) -m scripts.halt --status || true

test:
	@$(PYTHON) -m pytest; status=$$?; \
	if [ $$status -ne 0 ] && [ $$status -ne 5 ]; then exit $$status; fi
