.DEFAULT_GOAL := compile

BOOTSTRAP_PYTHON ?= $(shell for python in python3.14 python3.13 python3.12 python3; do \
	if command -v "$$python" >/dev/null 2>&1 && \
		"$$python" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' >/dev/null 2>&1; then \
		command -v "$$python"; break; \
	fi; \
done)
OTA_BOOTSTRAP_PYTHON ?= $(if $(wildcard /usr/bin/python3),/usr/bin/python3,$(BOOTSTRAP_PYTHON))
VENV_DIR := .venv
OTA_VENV_DIR := .venv-ota
ESPHOME := $(VENV_DIR)/bin/esphome
OTA_PYTHON := $(OTA_VENV_DIR)/bin/python
ESPHOME_STAMP := $(VENV_DIR)/.installed
OTA_ESPHOME_STAMP := $(OTA_VENV_DIR)/.installed
OTA_HOST ?= gato-dorado-2e90.local
OTA_PORT ?= 3232
OTA_PASSWORD ?=
OTA_BINARY := .esphome/build/gato-dorado-2e90/build/firmware.ota.bin

.PHONY: tools ota-tools secrets sounds config compile flash ota logs clean

tools: $(ESPHOME_STAMP)

ota-tools: $(OTA_ESPHOME_STAMP)

$(ESPHOME_STAMP): requirements.txt
	@test -n "$(BOOTSTRAP_PYTHON)" || { echo "Python 3.12 or newer is required to install ESPHome." >&2; exit 2; }
	$(BOOTSTRAP_PYTHON) -m venv "$(VENV_DIR)"
	$(VENV_DIR)/bin/python -m pip install --upgrade pip
	$(VENV_DIR)/bin/python -m pip install --requirement requirements.txt
	@touch "$@"

$(OTA_ESPHOME_STAMP): requirements-ota.txt
	$(OTA_BOOTSTRAP_PYTHON) -m venv "$(OTA_VENV_DIR)"
	$(OTA_VENV_DIR)/bin/python -m pip install --upgrade pip
	$(OTA_VENV_DIR)/bin/python -m pip install --requirement requirements-ota.txt
	@touch "$@"

secrets:
	@test -f secrets.yaml || { echo "secrets.yaml is missing. Copy secrets.example.yaml to secrets.yaml and set fallback_ap_password." >&2; exit 2; }

sounds: tools
	$(VENV_DIR)/bin/python scripts/prepare_sounds.py --manifest sounds/sources.txt --cache-dir .cache/sounds

config: tools secrets sounds
	$(ESPHOME) config motion-sound.yaml

compile: tools secrets sounds
	$(ESPHOME) compile motion-sound.yaml

flash: compile
	@test -n "$(PORT)" || { echo "Set PORT to the ESP32 serial device, e.g. make flash PORT=/dev/cu.usbserial-XXXX" >&2; exit 2; }
	$(ESPHOME) upload motion-sound.yaml --device "$(PORT)"

ota: compile ota-tools
	$(OTA_PYTHON) -c 'import logging, sys; from esphome.espota2 import run_ota; logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s"); sys.exit(run_ota(sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]))' \
		"$(OTA_HOST)" "$(OTA_PORT)" "$(OTA_PASSWORD)" "$(OTA_BINARY)"

logs: tools secrets sounds
	@test -n "$(PORT)" || { echo "Set PORT to the ESP32 serial device, e.g. make logs PORT=/dev/cu.usbserial-XXXX" >&2; exit 2; }
	$(ESPHOME) logs motion-sound.yaml --device "$(PORT)"

# Remove downloaded/processed sounds and ESPHome build output. The next
# make config/compile/flash/logs will download and prepare the WAVs again.
clean:
	rm -rf .cache/sounds .esphome
	rmdir .cache 2>/dev/null || true
