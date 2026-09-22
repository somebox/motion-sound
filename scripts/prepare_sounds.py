#!/usr/bin/env python3
"""Normalize ESPHome sound clips to simple, decoder-friendly PCM WAV files.

Usage: python3 scripts/prepare_sounds.py sounds/*.wav

Inputs must already be mono, 16-bit PCM at 16 kHz (as are the meow_dataset
clips). Outputs are rewritten in place with only canonical RIFF fmt/data chunks,
peak-normalized to -1 dBFS, and padded by one silent frame if needed so the
number of 16-bit samples is even.
"""

import argparse
import math
import os
from pathlib import Path
import struct
import tempfile
import wave

SAMPLE_RATE = 16_000
TARGET_PEAK_DBFS = -1.0
TARGET_PEAK = round(32768 * 10 ** (TARGET_PEAK_DBFS / 20))


def normalize(path: Path) -> None:
    with wave.open(str(path), "rb") as wav:
        params = wav.getparams()
        if (
            params.nchannels != 1
            or params.sampwidth != 2
            or params.framerate != SAMPLE_RATE
            or params.comptype != "NONE"
        ):
            raise ValueError(
                f"{path}: expected mono 16-bit PCM at {SAMPLE_RATE} Hz; "
                f"got {params.nchannels} channels, {params.sampwidth * 8}-bit, "
                f"{params.framerate} Hz, compression={params.comptype}"
            )
        pcm = wav.readframes(params.nframes)

    if len(pcm) % 2:
        raise ValueError(f"{path}: truncated 16-bit PCM data ({len(pcm)} bytes)")

    samples = list(struct.unpack(f"<{len(pcm) // 2}h", pcm))
    peak = max((abs(sample) for sample in samples), default=0)
    gain = TARGET_PEAK / peak if peak else 1.0
    normalized = [
        max(-32768, min(32767, round(sample * gain))) for sample in samples
    ]

    # The ESPHome micro-WAV decoder has crashed on an odd count of mono 16-bit
    # samples. Keep the PCM block 32-bit aligned by appending a silent frame.
    padded = len(normalized) % 2 != 0
    if padded:
        normalized.append(0)

    output = struct.pack(f"<{len(normalized)}h", *normalized)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as tmp:
        temp_path = Path(tmp.name)

    try:
        with wave.open(str(temp_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(output)
        os.chmod(temp_path, path.stat().st_mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)

    input_peak_dbfs = 20 * math.log10(peak / 32768) if peak else float("-inf")
    gain_db = 20 * math.log10(gain) if gain else 0.0
    print(
        f"{path}: {len(samples)} -> {len(normalized)} frames; "
        f"peak {input_peak_dbfs:.2f} dBFS, gain {gain_db:+.2f} dB, "
        f"metadata stripped, {'padded' if padded else 'already aligned'}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="WAV files to rewrite")
    args = parser.parse_args()
    for path in args.files:
        normalize(path)


if __name__ == "__main__":
    main()
