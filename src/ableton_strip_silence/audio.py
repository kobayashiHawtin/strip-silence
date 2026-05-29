from __future__ import annotations

import logging
import re
import struct
import wave
from dataclasses import dataclass
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


BEZIER_SUBDIVISIONS = 100


@dataclass(slots=True)
class BeatMapEntry:
    wall_seconds: float = 0.0
    beats: float = 0.0
    bpm: float = 120.0


def _cubic_bezier(p0: float, p1: float, p2: float, p3: float, u: float) -> float:
    omu = 1.0 - u
    omu2 = omu * omu
    omu3 = omu2 * omu
    u2 = u * u
    u3 = u2 * u
    return omu3 * p0 + 3.0 * omu2 * u * p1 + 3.0 * omu * u2 * p2 + u3 * p3


def build_beat_map(
    tempo_automation: list[tuple[float, float, Optional[float], Optional[float], Optional[float], Optional[float]]],
    max_seconds: float = 1e12,
) -> list[BeatMapEntry]:
    if not tempo_automation:
        return []

    normalized: dict[float, tuple[float, float, Optional[float], Optional[float], Optional[float], Optional[float]]] = {}
    for entry in tempo_automation:
        t = 0.0 if entry[0] < 0 else entry[0]
        normalized[t] = (t, entry[1], entry[2], entry[3], entry[4], entry[5])

    distinct_times = sorted(normalized.keys())
    by_time = [normalized[t] for t in distinct_times]
    if not by_time:
        return []

    entries: list[BeatMapEntry] = [BeatMapEntry(wall_seconds=0.0, beats=0.0, bpm=by_time[0][1])]
    current_seconds = 0.0

    for i in range(len(by_time) - 1):
        t0, v0, c1x, c1y, c2x, c2y = by_time[i]
        t1, v1, _c1x, _c1y, _c2x, _c2y = by_time[i + 1]

        beat0 = t0
        beat1 = t1
        beat_span = beat1 - beat0
        if beat_span <= 0:
            continue

        has_curve = c1x is not None and c1y is not None and c2x is not None and c2y is not None

        if has_curve:
            p0x, p0y = t0, v0
            p3x, p3y = t1, v1
            p1x = t0 + c1x * (t1 - t0)
            p1y = v0 + c1y * (v1 - v0)
            p2x = t1 - c2x * (t1 - t0)
            p2y = v1 - c2y * (v1 - v0)

            seg_seconds = 0.0
            for j in range(BEZIER_SUBDIVISIONS):
                ua = j / BEZIER_SUBDIVISIONS
                ub = (j + 1) / BEZIER_SUBDIVISIONS

                xa = _cubic_bezier(p0x, p1x, p2x, p3x, ua)
                xb = _cubic_bezier(p0x, p1x, p2x, p3x, ub)
                delta_16th = xb - xa

                umid = (ua + ub) / 2.0
                bpm_mid = _cubic_bezier(p0y, p1y, p2y, p3y, umid)
                if bpm_mid <= 0:
                    continue

                delta_beats = delta_16th
                seg_seconds += delta_beats / (bpm_mid / 60.0)

            current_seconds += seg_seconds
            entries.append(BeatMapEntry(wall_seconds=current_seconds, beats=beat1, bpm=v1))
        else:
            bpm = v0
            if bpm <= 0:
                continue
            seg_seconds = beat_span / (bpm / 60.0)
            current_seconds += seg_seconds
            entries.append(BeatMapEntry(wall_seconds=current_seconds, beats=beat1, bpm=v1))

    return entries


def sample_position_to_beats(
    start_samples: int,
    sample_rate: int,
    beat_map: list[BeatMapEntry],
) -> float:
    if not beat_map:
        raise ValueError("Beat map is empty")
    target_sec = start_samples / sample_rate

    if target_sec <= beat_map[0].wall_seconds:
        return beat_map[0].beats
    if target_sec >= beat_map[-1].wall_seconds:
        last = beat_map[-1]
        overshoot_sec = target_sec - last.wall_seconds
        return last.beats + overshoot_sec * (last.bpm / 60.0)

    lo, hi = 0, len(beat_map) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if beat_map[mid].wall_seconds <= target_sec:
            lo = mid
        else:
            hi = mid

    seg0 = beat_map[lo]
    seg1 = beat_map[hi]
    ratio = (target_sec - seg0.wall_seconds) / (seg1.wall_seconds - seg0.wall_seconds)
    return seg0.beats + ratio * (seg1.beats - seg0.beats)
