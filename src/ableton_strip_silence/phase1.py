from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
import shutil
from pathlib import Path

from .als import format_export_name, normalize_name, read_als, strip_project_prefix
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

    unmatched_files = export_files.copy()
    operations: list[RenameOperation] = []
    warnings: list[str] = []

    for track in parsed.tracks:
        match, strategy = match_export_file(track, project_name, unmatched_files)
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


def match_export_file(track: TrackInfo, project_name: str, available_files: list[Path]) -> tuple[Path | None, str]:
    track_candidates = {normalize_name(track.name), normalize_name(track.prefixed_name)}
    canonical_candidates = {canonical_name(track.name), canonical_name(track.prefixed_name)}

    exact_prefix_matches: list[Path] = []
    canonical_matches: list[Path] = []
    fuzzy_matches: list[Path] = []
    for path in available_files:
        stripped = strip_project_prefix(path.stem, project_name)
        normalized = normalize_name(stripped)
        canonical = canonical_name(stripped)
        if normalized in track_candidates:
            exact_prefix_matches.append(path)
            continue
        if canonical in canonical_candidates or any(canonical.startswith(candidate) for candidate in canonical_candidates):
            canonical_matches.append(path)
            continue
        if any(normalized.startswith(candidate) for candidate in track_candidates):
            fuzzy_matches.append(path)

    if len(exact_prefix_matches) == 1:
        return exact_prefix_matches[0], "exact-normalized"
    if len(exact_prefix_matches) > 1:
        exact_prefix_matches.sort(key=lambda path: len(path.name))
        return exact_prefix_matches[0], "exact-normalized-shortest"
    if len(canonical_matches) == 1:
        return canonical_matches[0], "canonical-normalized"
    if len(canonical_matches) > 1:
        canonical_matches.sort(key=lambda path: len(path.name))
        return canonical_matches[0], "canonical-normalized-shortest"
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0], "fuzzy-prefix"
    return None, "unmatched"


def canonical_name(value: str) -> str:
    raw = value
    raw = TRAILING_TIMESTAMP_PATTERN.sub("", raw)
    raw = raw.replace("(Bounce)", " ").replace("bounce", " ")
    raw = LEADING_NUMBER_PATTERN.sub("", raw)
    return normalize_name(raw)


def execute_phase1(
    als_path: Path,
    exports_dir: Path,
    output_dir: Path,
    dry_run: bool = False,
    manifest_path: Path | None = None,
) -> dict:
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

    manifest = {
        "tool": "ableton-strip-silence",
        "version": __version__,
        "phase": "phase1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "als": str(als_path),
        "exports_dir": str(exports_dir),
        "output_dir": str(output_dir),
        "summary": {
            "total_tracks": len(parsed.tracks),
            "matched_operations": len(manifest_entries),
            "unmatched_tracks": len(unmatched_tracks),
            "unmatched_files": len(unmatched_files),
            "warnings": len(warnings),
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
