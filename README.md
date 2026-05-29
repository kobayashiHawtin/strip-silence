# ableton-strip-silence

`ableton-strip-silence` is a Python CLI that processes Ableton Live exports and writes the result back into an `.als` project.

It provides an end-to-end workflow:

1. Match and rename exported WAV files based on track order/grouping in `.als`
2. Detect and trim silence from audio files
3. Restore timecoded clips into an Ableton `.als` file

## Features

- Works with Ableton `.als` + exported WAVs
- Automatic silence detection with safe defaults for mixed material
- `_tc_<sample_offset>` based placement for stable timeline restoration
- BWF `bext` time reference support
- Track matching that tolerates common naming variations
- `dry-run`, logs, and manifest output for traceability
- Multi-mode silence detection (hybrid, peak, RMS)
- Flexible trim parameters for different audio material types

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
- Log file: `<work-dir>/auto.log`
- Result manifest: `<work-dir>/auto_manifest.json`

By default, `auto` clears existing `AudioClip` content on matched tracks (use `--keep-existing` to append instead).

## Commands

All commands output a JSON result object to stdout upon completion.

### `auto` (recommended)

Runs rename + strip-silence + restore in one self-contained pipeline.

**Aliases:** `phase1` and `phase2` are legacy aliases for `rename` and `restore` respectively.

### `rename` (phase1)

Rename exported WAV files to preserve ALS track order and group hierarchy.

```bash
ableton-strip-silence rename \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --output path/to/renamed-exports
```

Default log: `<output>/phase1.log`

### `strip-silence`

Detect non-silent regions and write `_tc_` timecoded WAV clips.

```bash
ableton-strip-silence strip-silence \
  --inputs path/to/renamed-exports \
  --output path/to/timecoded-clips
```

Default log: `<output>/strip_silence.log`

### `restore` (phase2)

Restore `_tc_` timecoded WAV clips into an Ableton `.als` arrangement.

```bash
ableton-strip-silence restore \
  --als path/to/base.als \
  --clips path/to/timecoded-clips \
  --output path/to/restored.als
```

Default log: `<output>.parent/phase2.log`

## Global Options

- `--log-level {DEBUG,INFO,WARNING,ERROR}`: Set logging verbosity (default: `INFO`)
- `--log-file path/to/run.log`: Write logs to file (defaults are phase-specific)

## Command-Specific Options

### Output & Metadata

- `--dry-run`: Preview operations without writing files or modifying the ALS
- `--manifest path/to/manifest.json`: Save run metadata to JSON file

### Silence Detection Parameters

- `--threshold-db VALUE`: Fixed silence threshold in dBFS (omit for adaptive per-file thresholding)
- `--min-silence-ms VALUE` (default: 350): Minimum silent gap length to split clips
- `--min-clip-ms VALUE` (default: 80): Drop detected clips shorter than this duration (ms)
- `--keep-leading-ms VALUE` (default: 20): Keep this much audio before detected active regions (ms)
- `--keep-trailing-ms VALUE` (default: 40): Keep this much audio after detected active regions (ms)
- `--window-ms VALUE` (default: 20): Analysis window length (ms)
- `--hop-ms VALUE` (default: 10): Analysis hop length (ms)

### Activity Detection

- `--detection {hybrid,peak,rms}` (default: `hybrid`)
  - `hybrid`: Combined peak and RMS detection; safe default for mixed material
  - `peak`: Peak-based detection (more aggressive)
  - `rms`: RMS-based detection

- `--mode {independent,linked}` (default: `independent`)
  - `independent`: Trim each file independently
  - `linked`: Use one linked edit map for all files

### ALS-Specific Options

- `--bpm VALUE`: Override BPM if tempo cannot be read from `.als`

### Audio Clip Management (`auto` command only)

- `--clear-existing`: Remove existing AudioClips from target tracks before inserting (default behavior)
- `--keep-existing`: Append stripped clips instead of replacing matched tracks

## Exit Code

Returns `0` on success. Output is always JSON-formatted to stdout.

---

## Getting Started for Beginners

### Step-by-Step Guide

If you're using this tool for the first time, follow these steps:

### Step 1: Test Run

Always perform a trial run with the `--dry-run` option first. **This shows you the operations without actually writing files or modifying your ALS file.**

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --dry-run
```

Review the log output to verify the operations will work as expected.

### Step 2: Test with Real Data

Make copies of your `project.als` and `live-exports` folder, and test with the copies first.

```bash
ableton-strip-silence auto \
  --als path/to/project_copy.als \
  --exports path/to/live-exports-copy \
  --log-level DEBUG
```

Using `--log-level DEBUG` provides detailed logging so you can see exactly what's happening.

### Step 3: Production Run

Once you've confirmed it works correctly, run it on your original files.

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports
```

### How to Specify File Paths

Path specification follows these rules:

- **Absolute path**: `C:\Users\username\Music\project.als` or `/home/username/Music/project.als`
- **Relative path**: Path relative to your current directory. Example: `./project.als` or `../exports`
- **Paths with spaces**: Wrap in double quotes

```bash
ableton-strip-silence auto \
  --als "C:\Users\username\My Documents\project.als" \
  --exports "C:\Users\username\Live Exports"
```

### Checking Log Files

If something goes wrong, review the log file to diagnose the issue.

```bash
type <work-dir>/auto.log
```

Logs are recorded chronologically and help identify problems quickly.

---

## Common Pitfalls & Important Notes

### ⚠️ Warning 1: Always Backup Your `.als` File

This tool writes directly to your `.als` file. **If something fails, your original file could be overwritten.**

Always do one of the following:

- Make a copy of your `.als` and work folder first
- Run `--dry-run` to preview operations
- Use version control (Git) to track your files

### ⚠️ Warning 2: Track Names Must Match Closely

The `rename` command matches exported WAV filenames with track names in your `.als` file.

- **Exact matching is not required**, but **similar names work best**
- If your track is named "Kick", filenames like "Kick" or "Kick_01" are recognized easily
- Completely different names like "K" or "KK" may not match

**Always check the logs to verify correct matching**.

### ⚠️ Warning 3: Ableton Live Version Matters

Different Ableton Live versions may have different `.als` XML structures.

- Older Live projects may behave unexpectedly
- Ableton Live 12+ recommended for best compatibility

### ⚠️ Warning 4: Silence Detection Defaults Are for Mixed Material

Default parameters are optimized for **mixed audio (vocals + drums, etc.)**.

- **Vocals only**: Try `--threshold-db -35` for higher detection
- **Drums only**: Try `--detection peak` for more aggressive detection
- **Drones/pads**: Try `--min-silence-ms 500` for longer silence gaps

**Test with a small file first** before applying to your full project.

### ⚠️ Warning 5: `--keep-existing` Behavior

By default, `--clear-existing` removes old clips before inserting new ones.

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --keep-existing
```

Using `--keep-existing` appends new clips instead, which may create overlaps or unintended placement. Use cautiously.

### ⚠️ Warning 6: Case Sensitivity Across Platforms

Filesystem behavior differs between Windows and Mac/Linux:

- **Windows**: Case-insensitive (`Export` and `export` are the same)
- **Mac/Linux**: Case-sensitive (`Export` and `export` are different)

Keep directory names consistent for cross-platform compatibility.

---

## Troubleshooting

### Tracks Are Not Matching

**Cause**: Track names and WAV filenames differ significantly

**Solution**:

1. Check the log file (`auto.log`)
2. Review filenames generated by the `rename` command
3. Adjust track names in your `.als` if needed
4. Use `--manifest` to inspect matching results

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --manifest result.json
```

### Silence Not Detected Correctly

**Cause**: Default values don't suit your audio material

**Solution**:

1. Review detailed logs with `--log-level DEBUG`
2. Test different parameters on small files first
3. Try fixing threshold with `--threshold-db VALUE`

```bash
ableton-strip-silence strip-silence \
  --inputs path/to/inputs \
  --output path/to/outputs \
  --threshold-db -30 \
  --dry-run
```

### Errors Occur

**Common error messages**:

- `FileNotFoundError`: Your path is incorrect; double-check it
- `XMLParseError`: Your `.als` file is corrupted or uses an unsupported Live version
- `No matching tracks found`: Track and file names don't match

**Solution**: Always use `--log-level DEBUG` with `--dry-run` to see detailed diagnostics.

---

## Beginner Tips

### ✅ Tip 1: Test One Track First

Run `rename` and `strip-silence` as separate commands to understand each step:

```bash
ableton-strip-silence rename \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --output ./renamed-exports \
  --dry-run

ableton-strip-silence strip-silence \
  --inputs ./renamed-exports \
  --output ./timecoded \
  --dry-run

ableton-strip-silence restore \
  --als path/to/project.als \
  --clips ./timecoded \
  --output ./restored.als \
  --dry-run
```

### ✅ Tip 2: Use Manifest Output

The `--manifest` option saves detailed execution results in JSON format.

```bash
ableton-strip-silence auto \
  --als path/to/project.als \
  --exports path/to/live-exports \
  --manifest result.json
```

Reviewing `result.json` shows exactly what matched and what was skipped.

### ✅ Tip 3: Organize Your Working Directories

The `auto` command creates work directories automatically. For multiple projects, keep them organized:

```text
projects/
├── project_1/
│   ├── project_1.als
│   └── exports/
└── project_2/
    ├── project_2.als
    └── exports/
```

### ✅ Tip 4: Start Small

Create a test `.als` file with just 2–3 tracks and verify behavior before working with your full project.

---

## Recommended Workflow

Beginner-friendly recommended execution order:

```bash
# 1. Prepare test files
cp project.als project_test.als
cp -r exports exports_test

# 2. Confirm with dry-run
ableton-strip-silence auto \
  --als ./project_test.als \
  --exports ./exports_test \
  --dry-run \
  --log-level DEBUG

# 3. Check logs
type <work-dir>/auto.log

# 4. Review manifest details (optional)
ableton-strip-silence auto \
  --als ./project_test.als \
  --exports ./exports_test \
  --manifest result.json \
  --dry-run

# 5. Run for real
ableton-strip-silence auto \
  --als ./project_test.als \
  --exports ./exports_test \
  --manifest result.json

# 6. Inspect results
type result.json
```

---
