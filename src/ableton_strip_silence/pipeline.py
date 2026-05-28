from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
from typing import Any

from . import __version__
from .phase1 import execute_phase1
from .phase2 import execute_phase2
from .silence import TrimSettings, trim_directory

LOGGER = logging.getLogger(__name__)


def execute_auto(
    als_path: Path,
    exports_dir: Path,
    work_dir: Path,
    output_als: Path,
    settings: TrimSettings,
    bpm_override: float | None = None,
    clear_existing: bool = True,
    dry_run: bool = False,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    renamed_dir = work_dir / "01_renamed"
    clips_dir = work_dir / "02_stripped_clips"
    placed_als_path = work_dir / "placed.als"
    work_dir.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        clean_auto_outputs(renamed_dir, clips_dir)

    LOGGER.info("Auto pipeline step 1/3: rename Live exports and place stems into ALS")
    rename_manifest = execute_phase1(
        als_path=als_path,
        exports_dir=exports_dir,
        output_dir=renamed_dir,
        dry_run=dry_run,
        manifest_path=work_dir / "rename_manifest.json",
        place_als_path=placed_als_path,
        bpm_override=bpm_override,
    )

    if dry_run:
        manifest_output = manifest_path or work_dir / "auto_manifest.json"
        return {
            "tool": "ableton-strip-silence",
            "version": __version__,
            "phase": "auto",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "als": str(als_path),
            "exports_dir": str(exports_dir),
            "work_dir": str(work_dir),
            "output_als": str(output_als),
            "manifest": str(manifest_output),
            "summary": {
                "renamed_files": rename_manifest["summary"]["matched_operations"],
                "stripped_clips": 0,
                "restored_clips": 0,
                "dry_run": True,
                "note": "Dry-run stops after rename planning because downstream steps require generated WAV files.",
            },
            "settings": asdict(settings),
            "steps": {
                "rename": rename_manifest,
                "strip_silence": {"skipped": True},
                "restore": {"skipped": True},
            },
        }

    LOGGER.info("Auto pipeline step 2/3: strip silence")
    strip_manifest = trim_directory(
        inputs_dir=renamed_dir,
        output_dir=clips_dir,
        settings=settings,
        dry_run=dry_run,
        manifest_path=work_dir / "strip_silence_manifest.json",
    )

    LOGGER.info("Auto pipeline step 3/3: restore clips into placed ALS")
    matched_track_indices = {entry["track_index"] for entry in rename_manifest.get("operations", [])}
    restore_manifest = execute_phase2(
        als_path=placed_als_path,
        renders_dir=clips_dir,
        output_path=output_als,
        bpm_override=bpm_override,
        clear_existing=True,
        clear_track_indices=matched_track_indices,
        dry_run=dry_run,
        manifest_path=work_dir / "restore_manifest.json",
    )

    manifest_output = manifest_path or work_dir / "auto_manifest.json"
    manifest = {
        "tool": "ableton-strip-silence",
        "version": __version__,
        "phase": "auto",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "als": str(als_path),
        "exports_dir": str(exports_dir),
        "work_dir": str(work_dir),
        "output_als": str(output_als),
        "manifest": str(manifest_output),
        "summary": {
            "renamed_files": rename_manifest["summary"]["matched_operations"],
            "stripped_clips": strip_manifest["summary"]["output_clips"],
            "restored_clips": restore_manifest["summary"]["inserted_clips"],
            "dry_run": dry_run,
        },
        "settings": asdict(settings),
        "steps": {
            "rename": rename_manifest,
            "strip_silence": strip_manifest,
            "restore": restore_manifest,
        },
    }
    if not dry_run:
        manifest_output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def clean_auto_outputs(renamed_dir: Path, clips_dir: Path) -> None:
    for path in (renamed_dir, clips_dir):
        if path.exists():
            LOGGER.info("Removing previous auto output directory: %s", path)
            shutil.rmtree(path)
