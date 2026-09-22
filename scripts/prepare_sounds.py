#!/usr/bin/env python3
"""Download and prepare audio listed in a two-column sound manifest.

Manifest format (one entry per line):
    <audio_file_id> <https://.../clip.wav>

Original downloads and normalized outputs live in the gitignored cache. The
original WAV files are retained there so each build normalizes from pristine
source data instead of applying gain repeatedly.
"""

import argparse
import hashlib
import math
import os
from pathlib import Path
import re
import struct
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import wave

SAMPLE_RATE = 16_000
TARGET_PEAK_DBFS = -1.0
TARGET_PEAK = round(32768 * 10 ** (TARGET_PEAK_DBFS / 20))
MAX_AUDIO_FILE_BYTES = 5 * 1024 * 1024
ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")


def load_manifest(path: Path) -> list[tuple[str, str]]:
    entries = []
    seen_ids = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.partition("#")[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) != 2:
            raise ValueError(f"{path}:{line_number}: expected '<id> <https-url>'")

        audio_id, url = fields
        if not ID_PATTERN.fullmatch(audio_id):
            raise ValueError(f"{path}:{line_number}: invalid audio id {audio_id!r}")
        if audio_id in seen_ids:
            raise ValueError(f"{path}:{line_number}: duplicate audio id {audio_id!r}")
        if urlsplit(url).scheme != "https":
            raise ValueError(f"{path}:{line_number}: only HTTPS source URLs are allowed")

        seen_ids.add(audio_id)
        entries.append((audio_id, url))

    if not entries:
        raise ValueError(f"{path}: no sound URLs found")
    return entries


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as tmp:
        temp_path = Path(tmp.name)
        tmp.write(data)
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def download_source(audio_id: str, url: str, download_dir: Path) -> Path:
    url_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    path = download_dir / f"{audio_id}-{url_key}.wav"
    if path.is_file():
        print(f"Using cached source: {path}")
        return path

    print(f"Downloading {audio_id} from {url}")
    request = Request(url, headers={"User-Agent": "motion-sound-build/1.0"})
    with urlopen(request, timeout=30) as response:
        data = response.read(MAX_AUDIO_FILE_BYTES + 1)
    if len(data) > MAX_AUDIO_FILE_BYTES:
        raise ValueError(f"{url}: exceeds the {MAX_AUDIO_FILE_BYTES}-byte ESPHome limit")
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"{url}: response is not a RIFF/WAVE file")

    atomic_write(path, data)
    return path


def normalize(source: Path, destination: Path) -> None:
    with wave.open(str(source), "rb") as wav:
        params = wav.getparams()
        if (
            params.nchannels != 1
            or params.sampwidth != 2
            or params.framerate != SAMPLE_RATE
            or params.comptype != "NONE"
        ):
            raise ValueError(
                f"{source}: expected mono 16-bit PCM at {SAMPLE_RATE} Hz; "
                f"got {params.nchannels} channels, {params.sampwidth * 8}-bit, "
                f"{params.framerate} Hz, compression={params.comptype}"
            )
        pcm = wav.readframes(params.nframes)

    if len(pcm) % 2:
        raise ValueError(f"{source}: truncated 16-bit PCM data ({len(pcm)} bytes)")

    samples = list(struct.unpack(f"<{len(pcm) // 2}h", pcm))
    peak = max((abs(sample) for sample in samples), default=0)
    gain = TARGET_PEAK / peak if peak else 1.0
    normalized = [
        max(-32768, min(32767, round(sample * gain))) for sample in samples
    ]

    # micro-WAV has crashed on odd counts of mono 16-bit samples. Keep the PCM
    # block 32-bit aligned by appending one silent frame when needed.
    padded = len(normalized) % 2 != 0
    if padded:
        normalized.append(0)

    output = struct.pack(f"<{len(normalized)}h", *normalized)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False
    ) as tmp:
        temp_path = Path(tmp.name)

    try:
        with wave.open(str(temp_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(output)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)

    input_peak_dbfs = 20 * math.log10(peak / 32768) if peak else float("-inf")
    gain_db = 20 * math.log10(gain) if gain else 0.0
    print(
        f"Prepared {destination}: {len(samples)} -> {len(normalized)} frames; "
        f"peak {input_peak_dbfs:.2f} dBFS, gain {gain_db:+.2f} dB, "
        f"metadata stripped, {'padded' if padded else 'already aligned'}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("sounds/sources.txt"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/sounds"))
    args = parser.parse_args()

    try:
        entries = load_manifest(args.manifest)
        download_dir = args.cache_dir / "downloads"
        for audio_id, url in entries:
            source = download_source(audio_id, url, download_dir)
            normalize(source, args.cache_dir / f"{audio_id}.wav")
    except (OSError, ValueError, wave.Error) as err:
        parser.exit(1, f"prepare_sounds.py: error: {err}\n")


if __name__ == "__main__":
    main()
