from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from .als import build_audio_clip, find_arrangement_events_container, format_export_name, insert_clip, normalize_name, read_als, strip_project_prefix, write_als
from .audio import read_wave_metadata, samples_to_beats
from . import __version__
from .models import ParsedSet
from .models import RenameOperation, TrackInfo

LOGGER = logging.getLogger(__name__)


class Phase1MatchError(ValueError):
    """Raised when exported WAV files cannot be matched to ALS tracks."""


SUPPORTED_EXTENSIONS = {".wav", ".wave", ".aif", ".aiff"}
TRAILING_TIMESTAMP_PATTERN = re.compile(r"\s*\[\d{4}-\d{2}-\d{2}\s+\d{6}\]$", re.IGNORECASE)
LEADING_NUMBER_PATTERN = re.compile(r"^\d+\s*[-_ ]\s*")


def collect_export_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS)


def _build_file_cache(export_files: list[Path], project_name: str) -> dict[Path, dict]:
    cache: dict[Path, dict] = {}
    for path in export_files:
        stem = path.stem
        stripped = strip_stem_prefix(stem, project_name)
        prefix_stripped = strip_project_prefix(stem, project_name)
        cache[path] = {
            "stem": stem,
            "able_stripped": stripped,
            "able_norm": normalize_name(stripped) if stripped else None,
            "able_canon": canonical_name(stripped) if stripped else None,
            "prefix_stripped": prefix_stripped,
            "norm2": normalize_name(prefix_stripped),
            "canon2": canonical_name(prefix_stripped),
        }
    return cache


def build_rename_plan(
    als_path: Path,
    exports_dir: Path,
    output_dir: Path,
    parsed_set: ParsedSet | None = None,
) -> tuple[list[RenameOperation], list[str]]:
    parsed = parsed_set if parsed_set is not None else read_als(als_path)
    project_name = als_path.stem
    export_files = collect_export_files(exports_dir)
    if not export_files:
        raise Phase1MatchError(f"No supported audio files found in '{exports_dir}'.")

    file_cache = _build_file_cache(export_files, project_name)
    unmatched_files = export_files.copy()
    operations: list[RenameOperation] = []
    warnings: list[str] = []

    for track in parsed.tracks:
        if track.track_type != "AudioTrack":
            continue
        match, strategy = match_export_file(track, project_name, unmatched_files, file_cache)
        if match is None:
            warning = f"No exported file matched track '{track.name}' (index {track.index})."
            LOGGER.warning(warning)
            warnings.append(warning)
            continue
        unmatched_files.remove(match)
        destination = output_dir / format_export_name(track, match.suffix.lower())
        operations.append(
            RenameOperation(
                source=match,
                destination=destination,
                track=track,
                matched_name=match.name,
                match_strategy=strategy,
            )
        )

    if unmatched_files:
        names = ", ".join(path.name for path in unmatched_files)
        warning = f"Exported files left unmatched: {names}"
        LOGGER.warning(warning)
        warnings.append(warning)

    return operations, warnings


def strip_stem_prefix(stem: str, project_name: str) -> str | None:
    if stem == project_name:
        return None
    space_prefix = f"{project_name} "
    if stem.startswith(space_prefix):
        return stem[len(space_prefix):]
    dash_prefix = f"{project_name} - "
    if stem.startswith(dash_prefix):
        return stem[len(dash_prefix):]
    return stem


def _candidates(track: TrackInfo) -> set[str]:
    return {normalize_name(track.name), normalize_name(track.prefixed_name)}

def _canonical_candidates(track: TrackInfo) -> set[str]:
    return {canonical_name(track.name), canonical_name(track.prefixed_name)}


def match_export_file(
    track: TrackInfo,
    project_name: str,
    available_files: list[Path],
    file_cache: dict[Path, dict] | None = None,
) -> tuple[Path | None, str]:
    candidates = _candidates(track)
    canon_candidates = _canonical_candidates(track)

    ableton_matches: list[Path] = []
    ableton_canon_matches: list[Path] = []
    exact_prefix_matches: list[Path] = []
    canonical_matches: list[Path] = []
    fuzzy_matches: list[Path] = []
    for path in available_files:
        if file_cache is not None and path in file_cache:
            entry = file_cache[path]
            able_stripped = entry["able_stripped"]
            if able_stripped is None:
                continue
            able_norm = entry["able_norm"]
            if able_norm in candidates:
                ableton_matches.append(path)
                continue
            able_canon = entry["able_canon"]
            if able_canon in canon_candidates or any(able_canon.startswith(c) for c in canon_candidates):
                ableton_canon_matches.append(path)
                continue
            norm2 = entry["norm2"]
            canon2 = entry["canon2"]
            if norm2 in candidates:
                exact_prefix_matches.append(path)
                continue
            if canon2 in canon_candidates or any(canon2.startswith(c) for c in canon_candidates):
                canonical_matches.append(path)
                continue
            if any(norm2.startswith(c) for c in candidates):
                fuzzy_matches.append(path)
        else:
            able_stripped = strip_stem_prefix(path.stem, project_name)
            if able_stripped is None:
                continue
            able_norm = normalize_name(able_stripped)
            if able_norm in candidates:
                ableton_matches.append(path)
                continue
            able_canon = canonical_name(able_stripped)
            if able_canon in canon_candidates or any(able_canon.startswith(c) for c in canon_candidates):
                ableton_canon_matches.append(path)
                continue
            prefix_stripped = strip_project_prefix(path.stem, project_name)
            norm2 = normalize_name(prefix_stripped)
            canon2 = canonical_name(prefix_stripped)
            if norm2 in candidates:
                exact_prefix_matches.append(path)
                continue
            if canon2 in canon_candidates or any(canon2.startswith(c) for c in canon_candidates):
                canonical_matches.append(path)
                continue
            if any(norm2.startswith(c) for c in candidates):
                fuzzy_matches.append(path)

    if len(ableton_matches) == 1:
        return ableton_matches[0], "ableton-default"
    if len(ableton_matches) > 1:
        return min(ableton_matches, key=lambda p: len(p.name)), "ableton-default-shortest"
    if len(ableton_canon_matches) == 1:
        return ableton_canon_matches[0], "ableton-canon"
    if len(ableton_canon_matches) > 1:
        return min(ableton_canon_matches, key=lambda p: len(p.name)), "ableton-canon-shortest"
    if len(exact_prefix_matches) == 1:
        return exact_prefix_matches[0], "exact-normalized"
    if len(exact_prefix_matches) > 1:
        return min(exact_prefix_matches, key=lambda p: len(p.name)), "exact-normalized-shortest"
    if len(canonical_matches) == 1:
        return canonical_matches[0], "canonical-normalized"
    if len(canonical_matches) > 1:
        return min(canonical_matches, key=lambda p: len(p.name)), "canonical-normalized-shortest"
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0], "fuzzy-prefix"
    return None, "unmatched"


def canonical_name(value: str) -> str:
    raw = value
    raw = TRAILING_TIMESTAMP_PATTERN.sub("", raw)
    raw = raw.replace("(Bounce)", " ").replace("bounce", " ")
    raw = LEADING_NUMBER_PATTERN.sub("", raw)
    return normalize_name(raw)


def place_stems_into_als(
    parsed: ParsedSet,
    operations: list[RenameOperation],
    output_path: Path,
    bpm: float,
) -> None:
    placed = 0
    skipped: list[str] = []
    parsed.path = output_path
    for operation in operations:
        track = operation.track
        stem_path = operation.destination
        sample_rate, frame_count = read_wave_metadata(stem_path)
        duration_beats = samples_to_beats(frame_count, sample_rate, bpm)
        container = find_arrangement_events_container(track)
        if container is None:
            LOGGER.warning("Skipping track '%s': no arrangement events container (MIDI track?).", track.name)
            skipped.append(track.name)
            continue
        clip = build_audio_clip(
            parsed=parsed,
            track=track,
            file_path=stem_path,
            clip_name=stem_path.stem,
            start_beats=0.0,
            end_beats=duration_beats,
            duration_samples=frame_count,
            sample_rate=sample_rate,
        )
        insert_clip(parsed, track, clip)
        placed += 1
    LOGGER.info("Placed %d stem(s) into ALS (%d skipped)", placed, len(skipped))
    if skipped:
        LOGGER.info("Skipped tracks: %s", ", ".join(skipped))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_als(parsed, output_path)


def execute_phase1(
    als_path: Path,
    exports_dir: Path,
    output_dir: Path,
    dry_run: bool = False,
    manifest_path: Path | None = None,
    place_als_path: Path | None = None,
    bpm_override: float | None = None,
) -> dict[str, Any]:
    parsed = read_als(als_path)
    operations, warnings = build_rename_plan(als_path, exports_dir, output_dir, parsed_set=parsed)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_entries = []
    for operation in operations:
        LOGGER.info("%s -> %s", operation.source.name, operation.destination.name)
        manifest_entries.append(
            {
                "track_index": operation.track.index,
                "track_id": operation.track.track_id,
                "track_name": operation.track.name,
                "group_path": operation.track.group_path,
                "group_resolution": operation.track.group_resolution,
                "source": str(operation.source),
                "destination": str(operation.destination),
                "match_strategy": operation.match_strategy,
            }
        )
        if not dry_run:
            shutil.copy2(operation.source, operation.destination)

    if place_als_path is not None and not dry_run and operations:
        bpm = bpm_override if bpm_override is not None else parsed.tempo_bpm
        if bpm is None:
            raise ValueError("Cannot place stems: BPM could not be determined from ALS. Use --bpm.")
        LOGGER.info("Placing %d stem(s) into ALS: %s", len(operations), place_als_path)
        place_stems_into_als(parsed, operations, place_als_path, bpm)

    unmatched_tracks = [
        {
            "track_index": track.index,
            "track_name": track.name,
            "group_id": track.group_id,
            "group_path": track.group_path,
            "group_resolution": track.group_resolution,
        }
        for track in parsed.tracks
        if all(entry["track_index"] != track.index for entry in manifest_entries)
    ]

    matched_sources = {entry["source"] for entry in manifest_entries}
    unmatched_files = [
        str(path)
        for path in collect_export_files(exports_dir)
        if str(path) not in matched_sources
    ]

    placed_als_str = str(place_als_path) if place_als_path else None
    manifest = {
        "tool": "ableton-strip-silence",
        "version": __version__,
        "phase": "phase1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "als": str(als_path),
        "exports_dir": str(exports_dir),
        "output_dir": str(output_dir),
        "place_als_path": placed_als_str,
        "summary": {
            "total_tracks": len(parsed.tracks),
            "matched_operations": len(manifest_entries),
            "unmatched_tracks": len(unmatched_tracks),
            "unmatched_files": len(unmatched_files),
            "warnings": len(warnings),
            "clips_placed": len(manifest_entries) if place_als_path and not dry_run else 0,
            "dry_run": dry_run,
        },
        "operations": manifest_entries,
        "unmatched_tracks": unmatched_tracks,
        "unmatched_files": unmatched_files,
        "warnings": warnings,
    }

    manifest_output = manifest_path or output_dir / "phase1_manifest.json"
    manifest["manifest"] = str(manifest_output)
    LOGGER.info("Writing phase1 manifest: %s", manifest_output)
    if not dry_run:
        manifest_output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if not manifest_entries:
        LOGGER.warning("Phase 1 completed with zero matched operations. Check unmatched_tracks/unmatched_files in the manifest.")

    return manifest
