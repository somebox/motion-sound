.DEFAULT_GOAL := compile

PYTHON ?= python3
ESPHOME ?= esphome

.PHONY: sounds config compile flash logs clean

sounds:
	$(PYTHON) scripts/prepare_sounds.py --manifest sounds/sources.txt --cache-dir .cache/sounds

config: sounds
	$(ESPHOME) config motion-sound.yaml

compile: sounds
	$(ESPHOME) compile motion-sound.yaml

flash: compile
	@test -n "$(PORT)" || { echo "Set PORT to the ESP32 serial device, e.g. make flash PORT=/dev/cu.usbserial-XXXX" >&2; exit 2; }
	$(ESPHOME) upload motion-sound.yaml --device "$(PORT)"

logs: sounds
	@test -n "$(PORT)" || { echo "Set PORT to the ESP32 serial device, e.g. make logs PORT=/dev/cu.usbserial-XXXX" >&2; exit 2; }
	$(ESPHOME) logs motion-sound.yaml --device "$(PORT)"

# Remove downloaded/processed sounds and ESPHome build output. The next
# make config/compile/flash/logs will download and prepare the WAVs again.
clean:
	rm -rf .cache/sounds .esphome
	rmdir .cache 2>/dev/null || true
