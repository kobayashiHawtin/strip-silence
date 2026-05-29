from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Sequence

from .phase1 import execute_phase1
from .phase2 import execute_phase2
from .pipeline import execute_auto
from .silence import TrimSettings, trim_directory

DEFAULT_AUTO_DIR_NAME = "ableton-strip-silence"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ableton-strip-silence",
        description="Automate Ableton Live WAV export cleanup, silence stripping, and ALS restoration workflows.",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file", type=Path, default=None, help="Optional log file path. If omitted, a phase-specific default is used.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    phase1 = subparsers.add_parser("phase1", help="Alias of rename: preserve ALS track order and group hierarchy.")
    add_rename_arguments(phase1)

    rename = subparsers.add_parser("rename", help="Rename exported WAV files to preserve ALS track order and group hierarchy.")
    add_rename_arguments(rename)

    strip = subparsers.add_parser("strip-silence", help="Detect non-silent regions and write _tc_ timecoded WAV clips.")
    strip.add_argument("--inputs", required=True, type=Path, help="Directory containing WAV files to trim.")
    strip.add_argument("--output", required=True, type=Path, help="Directory to write timecoded WAV clips into.")
    add_trim_arguments(strip)
    strip.add_argument("--manifest", type=Path, default=None, help="Optional manifest output path. Defaults to <output>/strip_silence_manifest.json.")
    strip.add_argument("--dry-run", action="store_true", help="Print the trim plan without writing clips.")

    restore = subparsers.add_parser("restore", help="Restore _tc_ timecoded WAV clips into an Ableton .als arrangement.")
    add_restore_arguments(restore)

    phase2 = subparsers.add_parser("phase2", help="Alias of restore for timecoded WAV clips.")
    add_restore_arguments(phase2)

    auto = subparsers.add_parser("auto", help="Run rename, strip-silence, and restore in one self-contained pipeline.")
    auto.add_argument("--als", required=True, type=Path, help="Path to the source Ableton .als file.")
    auto.add_argument("--exports", required=True, type=Path, help="Directory containing raw WAV exports from Ableton Live.")
    auto.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Working directory for renamed files, clips, logs, and manifests. Defaults to <als-dir>/ableton-strip-silence/<als-stem>.",
    )
    auto.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path for the restored .als output file. Defaults to <work-dir>/<als-stem>.strip_silence.als.",
    )
    auto.add_argument("--bpm", type=float, default=None, help="Override BPM if the ALS file tempo cannot be parsed.")
    auto.add_argument(
        "--clear-existing",
        dest="clear_existing",
        action="store_true",
        help="Remove existing AudioClip nodes from matched target tracks before inserting clips. This is the default for auto.",
    )
    auto.add_argument(
        "--keep-existing",
        dest="clear_existing",
        action="store_false",
        help="Keep existing AudioClip nodes and append stripped clips instead of replacing matched tracks.",
    )
    auto.set_defaults(clear_existing=True)
    add_trim_arguments(auto)
    auto.add_argument("--manifest", type=Path, default=None, help="Optional manifest output path. Defaults to <work-dir>/auto_manifest.json.")
    auto.add_argument("--dry-run", action="store_true", help="Print the pipeline plan without writing files.")

    return parser


def add_rename_arguments(phase1: argparse.ArgumentParser) -> None:
    phase1.add_argument("--als", required=True, type=Path, help="Path to the source Ableton .als file.")
    phase1.add_argument("--exports", required=True, type=Path, help="Directory containing raw WAV exports from Ableton Live.")
    phase1.add_argument("--output", required=True, type=Path, help="Directory to write renamed files into.")
    phase1.add_argument("--manifest", type=Path, default=None, help="Optional manifest output path. Defaults to <output>/phase1_manifest.json.")
    phase1.add_argument("--dry-run", action="store_true", help="Print the rename plan without copying files.")


def add_restore_arguments(phase2: argparse.ArgumentParser) -> None:
    phase2.add_argument("--als", required=True, type=Path, help="Path to the base Ableton .als file.")
    phase2.add_argument("--clips", "--renders", dest="renders", required=True, type=Path, help="Directory containing _tc_ timecoded WAV clips.")
    phase2.add_argument("--output", required=True, type=Path, help="Path for the restored .als output file.")
    phase2.add_argument("--bpm", type=float, default=None, help="Override BPM if the ALS file tempo cannot be parsed.")
    phase2.add_argument("--clear-existing", action="store_true", help="Remove existing AudioClip nodes from target tracks before inserting timecoded clips.")
    phase2.add_argument("--manifest", type=Path, default=None, help="Optional manifest output path. Defaults to <output>.phase2_manifest.json.")
    phase2.add_argument("--dry-run", action="store_true", help="Print the restore plan without writing the ALS file.")


def add_trim_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--threshold-db", type=float, default=None, help="Fixed silence threshold in dBFS. Omit for conservative per-file adaptive thresholding.")
    parser.add_argument("--min-silence-ms", type=float, default=350.0, help="Minimum silent gap length used to split clips.")
    parser.add_argument("--min-clip-ms", type=float, default=80.0, help="Drop detected clips shorter than this duration.")
    parser.add_argument("--keep-leading-ms", type=float, default=20.0, help="Keep this much audio before each detected active region.")
    parser.add_argument("--keep-trailing-ms", type=float, default=40.0, help="Keep this much audio after each detected active region.")
    parser.add_argument("--window-ms", type=float, default=20.0, help="Analysis window length.")
    parser.add_argument("--hop-ms", type=float, default=10.0, help="Analysis hop length.")
    parser.add_argument("--detection", choices=["hybrid", "peak", "rms"], default="hybrid", help="Activity detector. Hybrid is the safe default for mixed material.")
    parser.add_argument("--mode", choices=["independent", "linked"], default="independent", help="Trim each file independently, or use one linked edit map for all files.")


def build_trim_settings(args: argparse.Namespace) -> TrimSettings:
    return TrimSettings(
        threshold_db=args.threshold_db,
        min_silence_ms=args.min_silence_ms,
        min_clip_ms=args.min_clip_ms,
        keep_leading_ms=args.keep_leading_ms,
        keep_trailing_ms=args.keep_trailing_ms,
        window_ms=args.window_ms,
        hop_ms=args.hop_ms,
        detection=args.detection,
        mode=args.mode,
    )


def default_auto_work_dir(als_path: Path) -> Path:
    return als_path.parent / DEFAULT_AUTO_DIR_NAME / als_path.stem


def default_auto_output_path(als_path: Path, work_dir: Path) -> Path:
    return work_dir / f"{als_path.stem}.strip_silence.als"


def resolve_auto_paths(args: argparse.Namespace) -> None:
    if args.command != "auto":
        return
    if args.work_dir is None:
        args.work_dir = default_auto_work_dir(args.als)
    if args.output is None:
        args.output = default_auto_output_path(args.als, args.work_dir)


def configure_logging(level: str, log_file: Path | None = None) -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level))
    root_logger.handlers.clear()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


def resolve_default_log_file(args: argparse.Namespace) -> Path | None:
    if args.log_file is not None:
        return args.log_file
    if args.command in {"phase1", "rename"}:
        return args.output / "phase1.log"
    if args.command == "strip-silence":
        return args.output / "strip_silence.log"
    if args.command == "auto":
        return args.work_dir / "auto.log"
    return args.output.parent / "phase2.log"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    resolve_auto_paths(args)
    log_file = resolve_default_log_file(args)
    configure_logging(args.log_level, log_file)
    logging.getLogger(__name__).info("Logging to %s", log_file)

    if args.command in {"phase1", "rename"}:
        result = execute_phase1(
            als_path=args.als,
            exports_dir=args.exports,
            output_dir=args.output,
            dry_run=args.dry_run,
            manifest_path=args.manifest,
        )
    elif args.command == "strip-silence":
        result = trim_directory(
            inputs_dir=args.inputs,
            output_dir=args.output,
            settings=build_trim_settings(args),
            dry_run=args.dry_run,
            manifest_path=args.manifest,
        )
    elif args.command in {"phase2", "restore"}:
        result = execute_phase2(
            als_path=args.als,
            renders_dir=args.renders,
            output_path=args.output,
            bpm_override=args.bpm,
            clear_existing=args.clear_existing,
            dry_run=args.dry_run,
            manifest_path=args.manifest,
        )
    else:
        result = execute_auto(
            als_path=args.als,
            exports_dir=args.exports,
            work_dir=args.work_dir,
            output_als=args.output,
            settings=build_trim_settings(args),
            bpm_override=args.bpm,
            clear_existing=args.clear_existing,
            dry_run=args.dry_run,
            manifest_path=args.manifest,
        )

    if log_file is not None:
        result["log_file"] = str(log_file)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
