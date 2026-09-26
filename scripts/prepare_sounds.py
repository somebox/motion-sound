#!/usr/bin/env python3
"""Download and prepare audio listed in a two-column sound manifest.

Manifest format (one entry per line):
    <audio_file_id> <https://.../clip.wav>

Original downloads and normalized outputs live in the gitignored cache. The
original WAV files are retained there so each build normalizes from pristine
source data instead of applying gain repeatedly. Each normalized WAV is then
encoded to a mono MP3, which is what the firmware embeds.
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
MP3_BITRATE_KBPS = 32
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


def extended80_to_float(raw: bytes) -> float:
    """Decode an 80-bit AIFF sample-rate value."""
    sign = raw[0] >> 7
    exponent = ((raw[0] & 0x7F) << 8 | raw[1]) - 16383
    mantissa = int.from_bytes(raw[2:10], "big")
    if mantissa == 0:
        return 0.0
    value = (mantissa / 2**63) * 2**exponent
    return -value if sign else value


def mix_to_mono(samples: list[int], channels: int) -> list[int]:
    if channels == 1:
        return samples
    mixed = []
    for index in range(0, len(samples) - channels + 1, channels):
        frame = samples[index : index + channels]
        mixed.append(int(round(sum(frame) / channels)))
    return mixed


def read_wav_pcm(path: Path) -> tuple[list[int], int]:
    with wave.open(str(path), "rb") as wav:
        params = wav.getparams()
        if params.sampwidth != 2 or params.comptype != "NONE":
            raise ValueError(
                f"{path}: expected 16-bit PCM; got {params.sampwidth * 8}-bit, "
                f"compression={params.comptype}"
            )
        raw = wav.readframes(params.nframes)
    samples = list(struct.unpack(f"<{len(raw) // 2}h", raw))
    return mix_to_mono(samples, params.nchannels), params.framerate


def read_aiff_pcm(path: Path) -> tuple[list[int], int]:
    data = path.read_bytes()
    if data[:4] != b"FORM" or data[8:12] != b"AIFF":
        raise ValueError(f"{path}: not an AIFF file")

    channels = frames = bits = rate = None
    pcm = None
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        chunk_size = struct.unpack(">I", data[pos + 4 : pos + 8])[0]
        body = data[pos + 8 : pos + 8 + chunk_size]
        if chunk_id == b"COMM" and len(body) >= 18:
            channels, frames, bits = struct.unpack(">HIH", body[:8])
            rate = int(round(extended80_to_float(body[8:18])))
        elif chunk_id == b"SSND" and len(body) >= 8:
            offset = struct.unpack(">I", body[:4])[0]
            pcm = body[8 + offset :]
        pos += 8 + chunk_size + (chunk_size & 1)

    if None in (channels, frames, bits, rate) or pcm is None:
        raise ValueError(f"{path}: AIFF is missing COMM or SSND")
    if bits != 16:
        raise ValueError(f"{path}: expected 16-bit AIFF samples, got {bits}-bit")

    count = frames * channels
    samples = list(struct.unpack(f">{count}h", pcm[: count * 2]))
    return mix_to_mono(samples, channels), rate


def read_pcm(path: Path) -> tuple[list[int], int]:
    header = path.read_bytes()[:12]
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return read_wav_pcm(path)
    if header[:4] == b"FORM" and header[8:12] == b"AIFF":
        return read_aiff_pcm(path)
    raise ValueError(f"{path}: expected a RIFF/WAVE or AIFF file")


def resample(samples: list[int], source_rate: int, target_rate: int) -> list[int]:
    if source_rate == target_rate or len(samples) <= 1:
        return samples
    target_length = max(1, int(round(len(samples) * target_rate / source_rate)))
    if target_length == 1:
        return [samples[0]]
    scale = (len(samples) - 1) / (target_length - 1)
    output = []
    for index in range(target_length):
        position = index * scale
        left = int(position)
        fraction = position - left
        right = min(left + 1, len(samples) - 1)
        output.append(
            int(round(samples[left] * (1.0 - fraction) + samples[right] * fraction))
        )
    return output


SFX_SOURCES = (
    ("beep", "beep.wav"),
    ("three_beeps", "3beeps.wav"),
    ("growl", "growl.wav"),
    ("purring", "purring.wav"),
    ("win_startup", "win-startup.wav"),
)


def prepare_sfx(sfx_dir: Path, cache_dir: Path) -> None:
    """Convert local cue clips to normalized mono 16 kHz WAVs."""
    for audio_id, filename in SFX_SOURCES:
        source = sfx_dir / filename
        samples, rate = read_pcm(source)
        converted = resample(samples, rate, SAMPLE_RATE)
        with tempfile.NamedTemporaryFile(
            prefix=f".{audio_id}.", suffix=".wav", dir=cache_dir, delete=False
        ) as tmp:
            temp_path = Path(tmp.name)
        try:
            with wave.open(str(temp_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                if converted:
                    wav.writeframes(struct.pack(f"<{len(converted)}h", *converted))
            normalize(temp_path, cache_dir / f"{audio_id}.wav")
        finally:
            temp_path.unlink(missing_ok=True)


def encode_mp3(wav_path: Path) -> None:
    """Encode a normalized mono 16 kHz WAV next to it as <name>.mp3."""
    import lameenc

    with wave.open(str(wav_path), "rb") as wav:
        pcm = wav.readframes(wav.getnframes())
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(MP3_BITRATE_KBPS)
    encoder.set_in_sample_rate(SAMPLE_RATE)
    encoder.set_channels(1)
    encoder.set_quality(2)
    data = bytes(encoder.encode(pcm) + encoder.flush())
    mp3_path = wav_path.with_suffix(".mp3")
    atomic_write(mp3_path, data)
    print(f"Encoded {mp3_path}: {len(data)} bytes ({MP3_BITRATE_KBPS} kbps)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("sounds/sources.txt"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/sounds"))
    parser.add_argument("--sfx-dir", type=Path, default=Path("media/sfx"))
    args = parser.parse_args()

    try:
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        entries = load_manifest(args.manifest)
        download_dir = args.cache_dir / "downloads"
        for audio_id, url in entries:
            source = download_source(audio_id, url, download_dir)
            normalize(source, args.cache_dir / f"{audio_id}.wav")
        prepare_sfx(args.sfx_dir, args.cache_dir)
        for audio_id in [entry[0] for entry in entries] + [s[0] for s in SFX_SOURCES]:
            encode_mp3(args.cache_dir / f"{audio_id}.wav")
    except (OSError, ValueError, wave.Error) as err:
        parser.exit(1, f"prepare_sounds.py: error: {err}\n")


if __name__ == "__main__":
    main()
