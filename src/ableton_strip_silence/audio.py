from __future__ import annotations

import logging
import re
import struct
import wave
from pathlib import Path
from typing import Optional

LOGGER = logging.getLogger(__name__)

TIMECODE_PATTERN = re.compile(r"^(?P<base>.+?)_tc_(?P<value>\d+(?:\.\d+)?)$", re.IGNORECASE)
SERIAL_PATTERN = re.compile(r"^(?P<index>\d{3,})_(?P<rest>.+)$")


class WaveMetadataError(ValueError):
    """Raised when a WAV/BWF file cannot provide timing metadata."""


def read_wave_metadata(path: Path) -> tuple[int, int]:
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getframerate(), handle.getnframes()
    except wave.Error:
        LOGGER.debug("wave module failed for %s, falling back to RIFF chunk parsing", path)
        return read_wave_metadata_fallback(path)


def read_wave_metadata_fallback(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise WaveMetadataError(f"'{path.name}' is not a valid RIFF/WAVE file.")

        sample_rate: int | None = None
        block_align: int | None = None
        data_size: int | None = None

        while True:
            chunk_header = handle.read(8)
            if len(chunk_header) < 8:
                break
            chunk_id = chunk_header[0:4]
            chunk_size = struct.unpack("<I", chunk_header[4:8])[0]
            chunk_data = handle.read(chunk_size)
            if len(chunk_data) < chunk_size:
                break
            if chunk_size % 2 == 1:
                handle.seek(1, 1)

            if chunk_id == b"fmt ":
                if len(chunk_data) < 16:
                    continue
                _format_tag, channels, sample_rate, _byte_rate, block_align = struct.unpack("<HHIIH", chunk_data[:14])
                if channels <= 0:
                    channels = 1
                if block_align <= 0 and len(chunk_data) >= 16:
                    bits_per_sample = struct.unpack("<H", chunk_data[14:16])[0]
                    block_align = max(1, channels * max(bits_per_sample // 8, 1))
            elif chunk_id == b"data":
                data_size = chunk_size

        if sample_rate is None or block_align in {None, 0} or data_size is None:
            raise WaveMetadataError(f"Could not read sample rate/frame count from '{path.name}'.")

        frame_count = data_size // block_align
        return int(sample_rate), int(frame_count)


def parse_timecode_from_name(path: Path) -> Optional[tuple[str, int]]:
    match = TIMECODE_PATTERN.match(path.stem)
    if not match:
        return None
    base_name = match.group("base")
    value = match.group("value")
    if "." in value:
        LOGGER.debug("Ignoring non-integer _tc_ value in filename: %s", path.name)
        return None
    return base_name, int(value)


def extract_serial_index(base_name: str) -> Optional[int]:
    match = SERIAL_PATTERN.match(base_name)
    if not match:
        return None
    return int(match.group("index"))


def read_bwf_time_reference_samples(path: Path) -> Optional[int]:
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            return None

        while True:
            chunk_header = handle.read(8)
            if len(chunk_header) < 8:
                break
            chunk_id = chunk_header[0:4]
            chunk_size = struct.unpack("<I", chunk_header[4:8])[0]
            if chunk_id == b"bext":
                if chunk_size < 338:
                    return None
                handle.seek(330, 1)
                time_ref = handle.read(8)
                if len(time_ref) < 8:
                    return None
                time_ref_low, time_ref_high = struct.unpack("<II", time_ref)
                return (time_ref_high << 32) | time_ref_low
            if chunk_size % 2 == 1:
                chunk_size += 1
            handle.seek(chunk_size, 1)
    return None


def resolve_render_start_samples(path: Path) -> tuple[str, int]:
    parsed = parse_timecode_from_name(path)
    if parsed is not None:
        return parsed

    bwf_samples = read_bwf_time_reference_samples(path)
    if bwf_samples is not None:
        return path.stem, bwf_samples

    raise WaveMetadataError(
        f"Could not determine start timecode for '{path.name}'. Use the '_tc_<samples>' suffix or a BWF time reference."
    )


def samples_to_beats(sample_count: int, sample_rate: int, bpm: float) -> float:
    if sample_rate <= 0:
        raise ValueError("Sample rate must be positive.")
    if bpm <= 0:
        raise ValueError("BPM must be positive.")
    return (sample_count / sample_rate) * (bpm / 60.0)
