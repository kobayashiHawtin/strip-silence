from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from .als import build_audio_clip, clear_audio_clips, find_audio_clip_template, insert_clip, read_als, track_by_index, track_by_name, write_als
from .audio import extract_serial_index, read_wave_metadata, resolve_render_start_samples, samples_to_beats
from . import __version__
from .models import ParsedSet
from .models import RenderClip

LOGGER = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".wav", ".wave"}


class Phase2RestoreError(ValueError):
    """Raised when timecoded audio clips cannot be restored into an ALS file."""


def collect_render_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS)


def parse_render_clips(
    als_path: Path,
    renders_dir: Path,
    bpm_override: float | None = None,
    parsed_set: ParsedSet | None = None,
) -> tuple[list[RenderClip], float]:
    parsed = parsed_set if parsed_set is not None else read_als(als_path)
    bpm = bpm_override if bpm_override is not None else parsed.tempo_bpm
    if bpm is None:
        raise Phase2RestoreError("Could not determine BPM from ALS. Please provide --bpm explicitly.")

    render_files = collect_render_files(renders_dir)
    if not render_files:
        raise Phase2RestoreError(f"No timecoded WAV clips found in '{renders_dir}'.")

    render_clips: list[RenderClip] = []
    for render_path in render_files:
        base_name, start_samples = resolve_render_start_samples(render_path)
        sample_rate, frame_count = read_wave_metadata(render_path)

        serial_index = extract_serial_index(base_name)
        matched_track = track_by_index(parsed.tracks, serial_index) if serial_index is not None else None
        if matched_track is None:
            matched_track = track_by_name(parsed.tracks, base_name)
        if matched_track is None:
            raise Phase2RestoreError(f"Could not match timecoded clip '{render_path.name}' to any ALS track.")

        render_clips.append(
            RenderClip(
                path=render_path,
                base_name=base_name,
                start_samples=start_samples,
                sample_rate=sample_rate,
                frame_count=frame_count,
                matched_track=matched_track,
            )
        )

    render_clips.sort(key=lambda clip: (clip.matched_track.index, clip.start_samples, clip.path.name.lower()))
    return render_clips, float(bpm)


def execute_phase2(
    als_path: Path,
    renders_dir: Path,
    output_path: Path,
    bpm_override: float | None = None,
    clear_existing: bool = False,
    clear_track_indices: set[int] | None = None,
    dry_run: bool = False,
    manifest_path: Path | None = None,
) -> dict:
    parsed = read_als(als_path)
    bpm = bpm_override if bpm_override is not None else parsed.tempo_bpm
    if bpm is None:
        raise Phase2RestoreError("Could not determine BPM from ALS. Please provide --bpm explicitly.")

    render_clips, _ = parse_render_clips(als_path, renders_dir, bpm_override=bpm, parsed_set=parsed)
    track_templates = {clip.matched_track.index: find_audio_clip_template(parsed.root, clip.matched_track) for clip in render_clips}

    if clear_existing:
        cleared_track_indices: set[int] = set()
        for track_index in sorted(clear_track_indices or set()):
            track = track_by_index(parsed.tracks, track_index)
            if track is None:
                continue
            LOGGER.info("Clearing existing audio clips from track '%s'", track.name)
            clear_audio_clips(track)
            cleared_track_indices.add(track.index)
        for render_clip in render_clips:
            track = render_clip.matched_track
            if track.index in cleared_track_indices:
                continue
            LOGGER.info("Clearing existing audio clips from track '%s'", track.name)
            clear_audio_clips(track)
            cleared_track_indices.add(track.index)

    inserted_entries: list[dict] = []
    for render in render_clips:
        start_beats = samples_to_beats(render.start_samples, render.sample_rate, bpm)
        end_beats = samples_to_beats(render.start_samples + render.duration_samples, render.sample_rate, bpm)
        LOGGER.info(
            "Insert %s on track '%s' at %.6f beats -> %.6f beats",
            render.path.name,
            render.matched_track.name,
            start_beats,
            end_beats,
        )
        clip = build_audio_clip(
            parsed=parsed,
            track=render.matched_track,
            file_path=render.path,
            clip_name=render.path.stem,
            start_beats=start_beats,
            end_beats=end_beats,
            duration_samples=render.duration_samples,
            sample_rate=render.sample_rate,
            template=track_templates.get(render.matched_track.index),
        )
        insert_clip(parsed, render.matched_track, clip)
        inserted_entries.append(
            {
                "track_index": render.matched_track.index,
                "track_name": render.matched_track.name,
                "file": str(render.path),
                "start_samples": render.start_samples,
                "sample_rate": render.sample_rate,
                "duration_samples": render.duration_samples,
                "start_beats": start_beats,
                "end_beats": end_beats,
            }
        )

    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_als(parsed, output_path)

    manifest_output = manifest_path or output_path.with_suffix(".phase2_manifest.json")
    manifest = {
        "tool": "ableton-strip-silence",
        "version": __version__,
        "phase": "phase2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "als": str(als_path),
        "clips_dir": str(renders_dir),
        "output": str(output_path),
        "manifest": str(manifest_output),
        "bpm": bpm,
        "summary": {
            "clip_files": len(render_clips),
            "inserted_clips": len(inserted_entries),
            "clear_existing": clear_existing,
            "cleared_tracks": len(cleared_track_indices) if clear_existing else 0,
            "dry_run": dry_run,
        },
        "inserted": inserted_entries,
    }
    if not dry_run:
        manifest_output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return manifest
