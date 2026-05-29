from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import re
import struct
from typing import Any

import numpy as np

from . import __version__

LOGGER = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".wav", ".wave"}
PCM_FORMAT = 1
IEEE_FLOAT_FORMAT = 3
EXTENSIBLE_FORMAT = 0xFFFE
PCM_GUID_PREFIX = bytes.fromhex("0100000000001000800000aa00389b71")
FLOAT_GUID_PREFIX = bytes.fromhex("0300000000001000800000aa00389b71")
SERIAL_PREFIX_PATTERN = re.compile(r"^(?P<index>\d{3,})[_-].+")


@dataclass(slots=True)
class TrimSettings:
    threshold_db: float | None = None
    min_silence_ms: float = 350.0
    min_clip_ms: float = 80.0
    keep_leading_ms: float = 20.0
    keep_trailing_ms: float = 40.0
    window_ms: float = 20.0
    hop_ms: float = 10.0
    detection: str = "hybrid"
    mode: str = "independent"


@dataclass(slots=True)
class WaveData:
    path: Path
    format_tag: int
    effective_format_tag: int
    channels: int
    sample_rate: int
    byte_rate: int
    block_align: int
    bits_per_sample: int
    fmt_chunk: bytes
    data: bytes

    @property
    def frame_count(self) -> int:
        return len(self.data) // self.block_align

    @property
    def bytes_per_sample(self) -> int:
        return max(1, self.block_align // max(1, self.channels))


@dataclass(slots=True)
class TrimmedClip:
    source: Path
    output: Path
    start_samples: int
    end_samples: int
    sample_rate: int

    @property
    def duration_samples(self) -> int:
        return self.end_samples - self.start_samples


@dataclass(slots=True)
class DetectionResult:
    spans: list[tuple[int, int]]
    threshold_db: float
    threshold_mode: str


def collect_audio_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS)


def read_wave_data(path: Path) -> WaveData:
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise ValueError(f"'{path}' is not a RIFF/WAVE file.")

        fmt_chunk: bytes | None = None
        data: bytes | None = None
        while True:
            chunk_header = handle.read(8)
            if len(chunk_header) < 8:
                break
            chunk_id = chunk_header[:4]
            chunk_size = struct.unpack("<I", chunk_header[4:8])[0]
            chunk_data = handle.read(chunk_size)
            if chunk_size % 2 == 1:
                handle.seek(1, 1)
            if chunk_id == b"fmt ":
                fmt_chunk = chunk_data
            elif chunk_id == b"data":
                data = chunk_data

    if fmt_chunk is None or data is None:
        raise ValueError(f"'{path}' does not contain both fmt and data chunks.")
    if len(fmt_chunk) < 16:
        raise ValueError(f"'{path}' has an invalid fmt chunk.")

    format_tag, channels, sample_rate, byte_rate, block_align, bits_per_sample = struct.unpack("<HHIIHH", fmt_chunk[:16])
    effective_format = effective_format_tag(format_tag, fmt_chunk)
    if effective_format not in {PCM_FORMAT, IEEE_FLOAT_FORMAT}:
        raise ValueError(f"Unsupported WAV format tag {format_tag} / effective {effective_format} in '{path.name}'.")
    if channels <= 0 or sample_rate <= 0 or block_align <= 0:
        raise ValueError(f"Invalid WAV format values in '{path.name}'.")

    return WaveData(
        path=path,
        format_tag=format_tag,
        effective_format_tag=effective_format,
        channels=channels,
        sample_rate=sample_rate,
        byte_rate=byte_rate,
        block_align=block_align,
        bits_per_sample=bits_per_sample,
        fmt_chunk=fmt_chunk,
        data=data,
    )


def effective_format_tag(format_tag: int, fmt_chunk: bytes) -> int:
    if format_tag != EXTENSIBLE_FORMAT or len(fmt_chunk) < 40:
        return format_tag
    subformat = fmt_chunk[24:40]
    if subformat == PCM_GUID_PREFIX:
        return PCM_FORMAT
    if subformat == FLOAT_GUID_PREFIX:
        return IEEE_FLOAT_FORMAT
    return format_tag


def write_wave_data(path: Path, source: WaveData, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = len(data) // source.block_align
    chunks: list[tuple[bytes, bytes]] = [(b"fmt ", source.fmt_chunk)]
    if source.effective_format_tag != PCM_FORMAT:
        chunks.append((b"fact", struct.pack("<I", frame_count)))
    chunks.append((b"data", data))

    riff_size = 4
    for _chunk_id, chunk_data in chunks:
        riff_size += 8 + len(chunk_data) + (len(chunk_data) % 2)

    with path.open("wb") as handle:
        handle.write(b"RIFF")
        handle.write(struct.pack("<I", riff_size))
        handle.write(b"WAVE")
        for chunk_id, chunk_data in chunks:
            handle.write(chunk_id)
            handle.write(struct.pack("<I", len(chunk_data)))
            handle.write(chunk_data)
            if len(chunk_data) % 2 == 1:
                handle.write(b"\x00")


def threshold_to_amplitude(threshold_db: float) -> float:
    return 10.0 ** (threshold_db / 20.0)


def amplitude_to_db(amplitude: float) -> float:
    if amplitude <= 0:
        return -120.0
    return 20.0 * math.log10(amplitude)


def ms_to_samples(sample_rate: int, ms: float) -> int:
    return max(0, int(round(sample_rate * ms / 1000.0)))


def detect_active_spans(wave: WaveData, settings: TrimSettings) -> list[tuple[int, int]]:
    return detect_active_regions(wave, settings).spans


def _wave_to_np_peaks(wave: WaveData) -> np.ndarray | None:
    try:
        frames = wave.frame_count
        channels = wave.channels
        tag = wave.effective_format_tag
        bps = wave.bits_per_sample

        if tag == IEEE_FLOAT_FORMAT:
            if bps == 64:
                arr = np.frombuffer(wave.data, dtype=np.float64).reshape(frames, channels).astype(np.float32)
            else:
                arr = np.frombuffer(wave.data, dtype=np.float32).reshape(frames, channels)
        elif bps == 8:
            arr = np.frombuffer(wave.data, dtype=np.uint8).reshape(frames, channels).astype(np.float32)
            arr = (arr - 128.0) / 128.0
        elif bps == 16:
            arr = np.frombuffer(wave.data, dtype=np.int16).reshape(frames, channels).astype(np.float32)
            arr /= 32768.0
        elif bps == 24:
            raw = np.frombuffer(wave.data, dtype=np.uint8).reshape(frames, channels * 3)
            lo = raw[:, 0::3].astype(np.int32)
            mi = raw[:, 1::3].astype(np.int32)
            hi = raw[:, 2::3].astype(np.int32)
            samples = lo | (mi << 8) | (hi << 16)
            sign_mask = samples & 0x800000
            samples = np.where(sign_mask, samples - 0x1000000, samples)
            arr = samples.astype(np.float32) / 8388608.0
        elif bps == 32:
            arr = np.frombuffer(wave.data, dtype=np.int32).reshape(frames, channels).astype(np.float32)
            arr /= 2147483648.0
        else:
            return None

        peaks = np.max(np.abs(arr), axis=1)
        np.clip(peaks, 0.0, 1.0, out=peaks)
        return peaks
    except Exception:
        LOGGER.debug("numpy conversion failed for %s, falling back", wave.path.name)
        return None


def _compute_window_levels_np(peaks: np.ndarray, frame_count: int, window: int, hop: int, detection: str) -> list[tuple[int, int, float]]:
    measured: list[tuple[int, int, float]] = []
    for start in range(0, frame_count, hop):
        end = min(frame_count, start + window)
        segment = peaks[start:end]
        if detection == "peak":
            level = float(segment.max())
        elif detection == "rms":
            level = float(np.sqrt(np.mean(segment ** 2)))
        else:
            peak_val = float(segment.max())
            rms_val = float(np.sqrt(np.mean(segment ** 2)))
            level = max(rms_val, peak_val * 0.5)
        measured.append((start, end, level))
    return measured


def detect_active_regions(wave: WaveData, settings: TrimSettings) -> DetectionResult:
    np_peaks = _wave_to_np_peaks(wave)
    if np_peaks is not None:
        return _np_detect_active_regions(np_peaks, wave, settings)

    window = max(1, ms_to_samples(wave.sample_rate, settings.window_ms))
    hop = max(1, ms_to_samples(wave.sample_rate, settings.hop_ms))
    frame_count = wave.frame_count
    raw_spans: list[tuple[int, int]] = []
    measured_windows: list[tuple[int, int, float]] = []

    for start in range(0, frame_count, hop):
        end = min(frame_count, start + window)
        level = window_level(wave, start, end, settings.detection)
        measured_windows.append((start, end, level))

    if settings.threshold_db is None:
        threshold = adaptive_threshold(measured_windows)
        threshold_db = amplitude_to_db(threshold)
        threshold_mode = "adaptive"
    else:
        threshold = threshold_to_amplitude(settings.threshold_db)
        threshold_db = settings.threshold_db
        threshold_mode = "fixed"

    LOGGER.debug(
        "%s: using %s threshold %.2f dBFS",
        wave.path.name,
        threshold_mode,
        threshold_db,
    )

    for start, end, level in measured_windows:
        if level >= threshold:
            raw_spans.append((start, end))

    merged = merge_spans(raw_spans, max_gap=ms_to_samples(wave.sample_rate, settings.min_silence_ms))
    leading = ms_to_samples(wave.sample_rate, settings.keep_leading_ms)
    trailing = ms_to_samples(wave.sample_rate, settings.keep_trailing_ms)
    min_clip = ms_to_samples(wave.sample_rate, settings.min_clip_ms)

    expanded: list[tuple[int, int]] = []
    for start, end in merged:
        expanded_start = max(0, start - leading)
        expanded_end = min(frame_count, end + trailing)
        if expanded_end - expanded_start >= min_clip:
            expanded.append((expanded_start, expanded_end))
    return DetectionResult(spans=merge_spans(expanded, max_gap=0), threshold_db=threshold_db, threshold_mode=threshold_mode)


def _np_detect_active_regions(peaks: np.ndarray, wave: WaveData, settings: TrimSettings) -> DetectionResult:
    window = max(1, ms_to_samples(wave.sample_rate, settings.window_ms))
    hop = max(1, ms_to_samples(wave.sample_rate, settings.hop_ms))
    frame_count = wave.frame_count
    measured_windows = _compute_window_levels_np(peaks, frame_count, window, hop, settings.detection)

    if settings.threshold_db is None:
        threshold = adaptive_threshold(measured_windows)
        threshold_db = amplitude_to_db(threshold)
        threshold_mode = "adaptive"
    else:
        threshold = threshold_to_amplitude(settings.threshold_db)
        threshold_db = settings.threshold_db
        threshold_mode = "fixed"

    LOGGER.debug("%s: using %s threshold %.2f dBFS", wave.path.name, threshold_mode, threshold_db)

    raw_spans: list[tuple[int, int]] = [(s, e) for s, e, lvl in measured_windows if lvl >= threshold]
    merged = merge_spans(raw_spans, max_gap=ms_to_samples(wave.sample_rate, settings.min_silence_ms))
    leading = ms_to_samples(wave.sample_rate, settings.keep_leading_ms)
    trailing = ms_to_samples(wave.sample_rate, settings.keep_trailing_ms)
    min_clip = ms_to_samples(wave.sample_rate, settings.min_clip_ms)

    expanded: list[tuple[int, int]] = []
    for start, end in merged:
        expanded_start = max(0, start - leading)
        expanded_end = min(frame_count, end + trailing)
        if expanded_end - expanded_start >= min_clip:
            expanded.append((expanded_start, expanded_end))
    return DetectionResult(spans=merge_spans(expanded, max_gap=0), threshold_db=threshold_db, threshold_mode=threshold_mode)


def adaptive_threshold(measured_windows: list[tuple[int, int, float]]) -> float:
    nonzero = sorted(level for _start, _end, level in measured_windows if level > 0.0)
    if not nonzero:
        return threshold_to_amplitude(-60.0)

    noise_floor = percentile(nonzero, 5.0)
    signal_reference = percentile(nonzero, 95.0)

    threshold = noise_floor * 1.5
    if signal_reference > 0:
        threshold = min(threshold, signal_reference * 0.5)
    return max(threshold, threshold_to_amplitude(-90.0))


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * percent / 100.0
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def window_level(wave: WaveData, start_frame: int, end_frame: int, detection: str) -> float:
    if end_frame <= start_frame:
        return 0.0
    if detection == "rms":
        total = 0.0
        count = 0
        for frame_index in range(start_frame, end_frame):
            amp = frame_peak(wave, frame_index)
            total += amp * amp
            count += 1
        return math.sqrt(total / count) if count else 0.0

    if detection == "hybrid":
        peak = 0.0
        total = 0.0
        count = 0
        for frame_index in range(start_frame, end_frame):
            amp = frame_peak(wave, frame_index)
            peak = max(peak, amp)
            total += amp * amp
            count += 1
        rms = math.sqrt(total / count) if count else 0.0
        return max(rms, peak * 0.5)

    peak = 0.0
    for frame_index in range(start_frame, end_frame):
        peak = max(peak, frame_peak(wave, frame_index))
        if peak >= 1.0:
            return peak
    return peak


def frame_peak(wave: WaveData, frame_index: int) -> float:
    offset = frame_index * wave.block_align
    frame = wave.data[offset : offset + wave.block_align]
    peak = 0.0
    sample_width = wave.bytes_per_sample
    for channel in range(wave.channels):
        start = channel * sample_width
        sample = frame[start : start + sample_width]
        peak = max(peak, abs(decode_sample(sample, wave.effective_format_tag, wave.bits_per_sample)))
    return min(peak, 1.0)


def decode_sample(sample: bytes, format_tag: int, bits_per_sample: int) -> float:
    if format_tag == IEEE_FLOAT_FORMAT:
        if bits_per_sample == 64 and len(sample) >= 8:
            return float(struct.unpack("<d", sample[:8])[0])
        if len(sample) >= 4:
            return float(struct.unpack("<f", sample[:4])[0])
        return 0.0

    if bits_per_sample == 8 and sample:
        return (sample[0] - 128) / 128.0
    if bits_per_sample == 16 and len(sample) >= 2:
        return struct.unpack("<h", sample[:2])[0] / 32768.0
    if bits_per_sample == 24 and len(sample) >= 3:
        raw = int.from_bytes(sample[:3], byteorder="little", signed=False)
        if raw & 0x800000:
            raw -= 0x1000000
        return raw / 8388608.0
    if bits_per_sample == 32 and len(sample) >= 4:
        return struct.unpack("<i", sample[:4])[0] / 2147483648.0
    return 0.0


def merge_spans(spans: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    if not spans:
        return []
    spans = sorted(spans)
    merged = [spans[0]]
    for start, end in spans[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + max_gap:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def union_spans(span_sets: list[list[tuple[int, int]]], max_gap: int = 0) -> list[tuple[int, int]]:
    spans = [span for span_set in span_sets for span in span_set]
    return merge_spans(spans, max_gap=max_gap)


def trim_directory(
    inputs_dir: Path,
    output_dir: Path,
    settings: TrimSettings,
    dry_run: bool = False,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    validate_trim_settings(settings)
    files = collect_audio_files(inputs_dir)
    if not files:
        raise ValueError(f"No WAV files found in '{inputs_dir}'.")

    output_dir.mkdir(parents=True, exist_ok=True)
    clips: list[TrimmedClip] = []
    analysis_entries: list[dict] = []

    if settings.mode == "independent":
        for path in files:
            wave = read_wave_data(path)
            result = detect_active_regions(wave, settings)
            analysis_entries.append(build_analysis_entry(wave, result))
            write_detected_clips(wave, result.spans, output_dir, clips, dry_run)
    else:
        waves = [read_wave_data(path) for path in files]
        detection_results = [detect_active_regions(wave, settings) for wave in waves]
        per_file_spans = [result.spans for result in detection_results]
        sample_rates = {wave.sample_rate for wave in waves}
        if len(sample_rates) != 1:
            raise ValueError("Linked mode requires all WAV files to have the same sample rate.")
        linked_spans = union_spans(per_file_spans, max_gap=ms_to_samples(waves[0].sample_rate, settings.min_silence_ms))
        per_file_spans = [clip_spans_to_length(linked_spans, wave.frame_count, settings, wave.sample_rate) for wave in waves]
        analysis_entries = [build_analysis_entry(wave, result) for wave, result in zip(waves, detection_results)]
        for wave, spans in zip(waves, per_file_spans):
            write_detected_clips(wave, spans, output_dir, clips, dry_run)

    manifest_output = manifest_path or output_dir / "strip_silence_manifest.json"
    manifest = {
        "tool": "ableton-strip-silence",
        "version": __version__,
        "phase": "strip-silence",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs_dir": str(inputs_dir),
        "output_dir": str(output_dir),
        "manifest": str(manifest_output),
        "settings": asdict(settings),
        "summary": {
            "input_files": len(files),
            "output_clips": len(clips),
            "mode": settings.mode,
            "detection": settings.detection,
            "dry_run": dry_run,
        },
        "analysis": analysis_entries,
        "clips": [
            {
                "source": str(clip.source),
                "output": str(clip.output),
                "start_samples": clip.start_samples,
                "end_samples": clip.end_samples,
                "duration_samples": clip.duration_samples,
                "sample_rate": clip.sample_rate,
            }
            for clip in clips
        ],
    }
    if not dry_run:
        manifest_output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def validate_trim_settings(settings: TrimSettings) -> None:
    if settings.detection not in {"hybrid", "peak", "rms"}:
        raise ValueError("detection must be one of: hybrid, peak, rms.")
    if settings.mode not in {"independent", "linked"}:
        raise ValueError("mode must be one of: independent, linked.")

    numeric_fields = {
        "min_silence_ms": settings.min_silence_ms,
        "min_clip_ms": settings.min_clip_ms,
        "keep_leading_ms": settings.keep_leading_ms,
        "keep_trailing_ms": settings.keep_trailing_ms,
        "window_ms": settings.window_ms,
        "hop_ms": settings.hop_ms,
    }
    for name, value in numeric_fields.items():
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number.")

    for name in ("min_silence_ms", "min_clip_ms", "keep_leading_ms", "keep_trailing_ms"):
        if numeric_fields[name] < 0:
            raise ValueError(f"{name} must be greater than or equal to 0.")
    for name in ("window_ms", "hop_ms"):
        if numeric_fields[name] <= 0:
            raise ValueError(f"{name} must be greater than 0.")

    if settings.threshold_db is not None:
        if not isinstance(settings.threshold_db, (int, float)) or not math.isfinite(settings.threshold_db):
            raise ValueError("threshold_db must be a finite number when provided.")


def build_analysis_entry(wave: WaveData, result: DetectionResult) -> dict[str, Any]:
    return {
        "source": str(wave.path),
        "threshold_mode": result.threshold_mode,
        "threshold_db": result.threshold_db,
        "detected_spans": len(result.spans),
        "sample_rate": wave.sample_rate,
        "frame_count": wave.frame_count,
    }


def write_detected_clips(
    wave: WaveData,
    spans: list[tuple[int, int]],
    output_dir: Path,
    clips: list[TrimmedClip],
    dry_run: bool,
) -> None:
    LOGGER.info("%s: detected %d clip(s)", wave.path.name, len(spans))
    for index, (start, end) in enumerate(spans, start=1):
        data = slice_frames(wave, start, end)
        output = output_dir / build_clip_output_name(wave.path, index, start)
        clips.append(TrimmedClip(source=wave.path, output=output, start_samples=start, end_samples=end, sample_rate=wave.sample_rate))
        if not dry_run:
            write_wave_data(output, wave, data)


def build_clip_output_name(source_path: Path, index: int, start_samples: int) -> str:
    suffix = source_path.suffix.lower()
    serial_match = SERIAL_PREFIX_PATTERN.match(source_path.stem)
    if serial_match:
        serial = serial_match.group("index")
        return f"{serial}_part_{index:03d}_tc_{start_samples}{suffix}"

    stem = source_path.stem
    if len(stem) > 48:
        stem = stem[:48]
    return f"{stem}_part_{index:03d}_tc_{start_samples}{suffix}"


def clip_spans_to_length(
    spans: list[tuple[int, int]],
    frame_count: int,
    settings: TrimSettings,
    sample_rate: int,
) -> list[tuple[int, int]]:
    min_clip = ms_to_samples(sample_rate, settings.min_clip_ms)
    clipped: list[tuple[int, int]] = []
    for start, end in spans:
        clipped_start = max(0, min(frame_count, start))
        clipped_end = max(0, min(frame_count, end))
        if clipped_end - clipped_start >= min_clip:
            clipped.append((clipped_start, clipped_end))
    return clipped


def slice_frames(wave: WaveData, start_frame: int, end_frame: int) -> bytes:
    start = max(0, start_frame) * wave.block_align
    end = min(wave.frame_count, end_frame) * wave.block_align
    return wave.data[start:end]
