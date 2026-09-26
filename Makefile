.DEFAULT_GOAL := compile

PYTHON ?= python3
ESPHOME ?= esphome
OTA_PYTHON ?= $(PYTHON)
OTA_HOST ?= gato-dorado-2e90.local
OTA_PORT ?= 3232
OTA_PASSWORD ?=
OTA_BINARY := .esphome/build/gato-dorado-2e90/build/firmware.ota.bin

.PHONY: sounds config compile flash ota logs clean

sounds:
	$(PYTHON) scripts/prepare_sounds.py --manifest sounds/sources.txt --cache-dir .cache/sounds

config: sounds
	$(ESPHOME) config motion-sound.yaml

compile: sounds
	$(ESPHOME) compile motion-sound.yaml

flash: compile
	@test -n "$(PORT)" || { echo "Set PORT to the ESP32 serial device, e.g. make flash PORT=/dev/cu.usbserial-XXXX" >&2; exit 2; }
	$(ESPHOME) upload motion-sound.yaml --device "$(PORT)"

ota: compile
	$(OTA_PYTHON) -c 'import logging, sys; from esphome.espota2 import run_ota; logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s"); sys.exit(run_ota(sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]))' \
		"$(OTA_HOST)" "$(OTA_PORT)" "$(OTA_PASSWORD)" "$(OTA_BINARY)"

logs: sounds
	@test -n "$(PORT)" || { echo "Set PORT to the ESP32 serial device, e.g. make logs PORT=/dev/cu.usbserial-XXXX" >&2; exit 2; }
	$(ESPHOME) logs motion-sound.yaml --device "$(PORT)"

# Remove downloaded/processed sounds and ESPHome build output. The next
# make config/compile/flash/logs will download and prepare the WAVs again.
clean:
	rm -rf .cache/sounds .esphome
	rmdir .cache 2>/dev/null || true
