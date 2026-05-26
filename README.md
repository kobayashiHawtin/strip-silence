# ableton-strip-silence

`ableton-strip-silence` is a Python CLI that processes Ableton Live exports and writes the result back into an `.als` project.

It provides an end-to-end workflow:

1. Match and rename exported WAV files based on track order/grouping in `.als`
2. Detect and trim silence from audio files
3. Restore timecoded clips into an Ableton `.als` file

## Features

- Works with Ableton `.als` + exported WAVs (no Reaper dependency)
- Automatic silence detection with safe defaults for mixed material
- `_tc_<sample_offset>` based placement for stable timeline restoration
- BWF `bext` time reference support
- Track matching that tolerates common naming variations
- `dry-run`, logs, and manifest output for traceability

## Requirements

- Python 3.10+

## Installation

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e .
```

CLI command:

```bash
ableton-strip-silence
```

## Quick Start

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports
```

Default output:

- Work directory: `<als-dir>/ableton-strip-silence/<als-stem>/`
- Restored ALS: `<als-dir>/ableton-strip-silence/<als-stem>/<als-stem>.strip_silence.als`

By default, `auto` replaces existing `AudioClip` content on matched tracks. Use `--keep-existing` to append instead.

## Commands

### `auto` (recommended)

Runs rename + strip-silence + restore in one command.

### `rename`

```bash
ableton-strip-silence rename \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --output path/to/renamed-exports
```

### `strip-silence`

```bash
ableton-strip-silence strip-silence \
  --inputs path/to/renamed-exports \
  --output path/to/timecoded-clips
```

### `restore`

```bash
ableton-strip-silence restore \
  --als path/to/base.als \
  --clips path/to/timecoded-clips \
  --output path/to/restored.als
```

## Common Options

- `--keep-existing`: keep existing clips and append new ones
- `--dry-run`: preview without writing files
- `--manifest path/to/manifest.json`: write run metadata
- `--log-file path/to/run.log`: write logs to file
- `--log-level DEBUG`: verbose logging
- `--bpm 120`: fallback BPM when tempo cannot be read from `.als`

Silence detection parameters:

- `--threshold-db`
- `--min-silence-ms`
- `--min-clip-ms`
- `--keep-leading-ms`
- `--keep-trailing-ms`
- `--window-ms`
- `--hop-ms`
- `--detection hybrid|peak|rms`
- `--mode independent|linked`

## Notes

- Ableton `.als` XML structure can vary across Live versions.
- Restoration is most robust when reusable `AudioClip` templates exist.

## Security & Maintenance

- Security policy: `SECURITY.md`
- Contribution guide: `CONTRIBUTING.md`
- CI security checks: `.github/workflows/security.yml`
- Automated updates: `.github/dependabot.yml`

## Development

Run tests:

```bash
python -m unittest discover -s tests -v
```
