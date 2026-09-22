# Motion Sound

An example project for building a standalone ESPHome sound-effects (SFX) device. An ESP32-WROOM DevKit V1 reads a binary presence/motion sensor and randomly plays a short WAV sample stored in firmware flash through a MAX98357 I²S amplifier. Detection, random selection, and playback run locally on the device; Wi-Fi and Home Assistant are not required.

## Hardware and wiring

| Signal | ESP32 GPIO | Connect to |
| --- | ---: | --- |
| Motion sensor output | GPIO33 | Binary sensor output |
| I²S data out (DIN) | GPIO15 | MAX98357 DIN |
| I²S word select (LRCLK/WS) | GPIO22 | MAX98357 LRCLK |
| I²S bit clock (BCLK) | GPIO17 | MAX98357 BCLK |
| Amplifier power and ground | — | 2.5–5.5 V at VIN; share GND with ESP32 |
| Speaker output | — | Connect a 4 Ω+ speaker across the amplifier outputs; do not ground either output |

The config now includes the GPIO33 motion input and local WAV playback through the MAX98357. Confirm the presence sensor's output polarity and electrical level before connecting it; ESP32 GPIOs are not 5 V tolerant. GPIO15 is a boot-strapping pin; the MAX98357 DIN input should be high impedance, but avoid adding pulls to GPIO15 and move I²S DOUT to another suitable GPIO if the board has boot issues.

For the amplifier pinout, see [Adafruit's MAX98357 pinout guide](https://learn.adafruit.com/adafruit-max98357-i2s-class-d-mono-amp/pinouts). The MAX98357 takes digital I²S (not analog audio), and MCLK is not required. Its speaker outputs are bridge-tied/floating: use a 4 Ω or higher moving-coil speaker across the output pair, and do not connect either speaker terminal to ground. The amp supply range is 2.5–5.5 V; Adafruit recommends 5 V for higher output power, with a supply sized for the speaker load. ESP32 3.3 V logic is compatible with the I²S inputs. Follow the markings/specifications for the specific amplifier breakout in use.

## Current bring-up

- ESPHome CLI: `2026.7.3`
- Board profile: `esp32dev` (classic ESP32 / ESP32-WROOM)
- USB serial adapter detected by macOS as QinHeng CH340 (`1a86:7523`)
- ESPHome/esptool detected an ESP32-D0WD-V3 (revision 3.1), 40 MHz crystal, 4 MB flash
- USB serial device path varies by host; select the port for your board
- Baseline motion firmware was compiled/uploaded and the sensor was confirmed working by the user
- Four-sound random-playback firmware was previously uploaded successfully
- Rebuilt after WAV cleanup (~373 KB of a 1.8 MB app partition), flashed successfully, and verified by esptool

Validate, compile, and flash over USB:

```sh
esphome config motion-sound.yaml
esphome compile motion-sound.yaml
PORT=/dev/cu.YOUR_ESP32_SERIAL_PORT # macOS; Linux commonly uses /dev/ttyUSB0
esphome upload motion-sound.yaml --device "$PORT"
esphome logs motion-sound.yaml --device "$PORT"
```

If the first upload cannot enter bootloader mode automatically, hold the board's BOOT button while starting upload, then release it when writing begins.

## Sound effects

Four PCM WAV samples are embedded in flash using ESPHome's `audio_file` component; each is mono, 16-bit, 16 kHz and matches the configured I²S stream:

- [`youtube_yKb90ItHtn0_4.wav`](sounds/youtube_yKb90ItHtn0_4.wav)
- [`youtube_yKb90ItHtn0_27.wav`](sounds/youtube_yKb90ItHtn0_27.wav)
- [`kaggle_cat_36_0.wav`](sounds/kaggle_cat_36_0.wav)
- [`kaggle_cat_31_13.wav`](sounds/kaggle_cat_31_13.wav)

The samples are from [haydenroche5/meow_dataset](https://github.com/haydenroche5/meow_dataset). The source WAVs were already mono, 16-bit, 16 kHz, so downsampling was unnecessary. `scripts/prepare_sounds.py` rewrites them as simple `fmt`/`data` PCM WAVs, strips trailing metadata chunks, normalizes peaks to -1 dBFS, and pads odd sample counts by one silent frame. Two source clips had odd sample counts; the `kaggle_cat_31_13` clip was the one shown crashing in the supplied backtrace (selected index 3). These are likely decoder edge cases rather than a sample-rate mismatch.

Each motion rising edge selects one of the four clips with `random_uint32()` and plays it if the player is idle. The logged index `0`–`3` corresponds to the list order above. Every additional embedded clip increases firmware/flash usage.

To normalize newly added compatible 16 kHz mono 16-bit PCM clips in place before adding them to `audio_file`:

```sh
python3 scripts/prepare_sounds.py sounds/*.wav
```

To test or troubleshoot playback, set `PORT` as shown above, trigger motion, and watch the serial logs:

```sh
esphome logs motion-sound.yaml --device "$PORT"
```

The device logs the motion event and selected sound index, then plays the chosen sample through the connected speaker. Test all four selections across repeated motion events.

Note: the motion sensor is documented here: https://github.com/tarantula3/RCWL-0516
