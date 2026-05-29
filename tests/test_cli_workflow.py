from __future__ import annotations

import gzip
import json
from pathlib import Path
import tempfile
import unittest
import wave
import xml.etree.ElementTree as ET

from ableton_strip_silence.audio import build_beat_map, parse_timecode_from_name, read_bwf_time_reference_samples, sample_position_to_beats, samples_to_beats
from ableton_strip_silence.cli import build_parser, resolve_auto_paths
from ableton_strip_silence.phase1 import build_rename_plan, execute_phase1
from ableton_strip_silence.phase2 import execute_phase2, parse_render_clips
from ableton_strip_silence.silence import TrimSettings, trim_directory


SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8'?>
<Ableton>
  <LiveSet>
    <Tracks>
      <GroupTrack>
        <Id Value="1" />
        <TrackGroupId Value="-1" />
        <Name>
          <EffectiveName Value="Drums" />
        </Name>
        <MainSequencer>
          <Sample>
            <ArrangerAutomation>
              <Events />
            </ArrangerAutomation>
          </Sample>
        </MainSequencer>
      </GroupTrack>
      <AudioTrack>
        <Id Value="2" />
        <TrackGroupId Value="1" />
        <Name>
          <EffectiveName Value="Kick" />
        </Name>
        <MainSequencer>
          <Sample>
            <ArrangerAutomation>
              <Events />
            </ArrangerAutomation>
          </Sample>
        </MainSequencer>
      </AudioTrack>
      <AudioTrack>
        <Id Value="3" />
        <TrackGroupId Value="1" />
        <Name>
          <EffectiveName Value="Snare" />
        </Name>
        <MainSequencer>
          <Sample>
            <ArrangerAutomation>
              <Events />
            </ArrangerAutomation>
          </Sample>
        </MainSequencer>
      </AudioTrack>
      <AudioTrack>
        <Id Value="4" />
        <TrackGroupId Value="-1" />
        <Name>
          <EffectiveName Value="Bass" />
        </Name>
        <MainSequencer>
          <Sample>
            <ArrangerAutomation>
              <Events />
            </ArrangerAutomation>
          </Sample>
        </MainSequencer>
      </AudioTrack>
    </Tracks>
    <MasterTrack>
      <DeviceChain>
        <Mixer>
          <Tempo>
            <Manual Value="120" />
          </Tempo>
        </Mixer>
      </DeviceChain>
    </MasterTrack>
  </LiveSet>
</Ableton>
"""


DUPLICATE_TRACK_ID_XML = """<?xml version='1.0' encoding='UTF-8'?>
<Ableton>
  <LiveSet>
    <Tracks>
      <AudioTrack>
        <Id Value="0" />
        <TrackGroupId Value="-1" />
        <Name><EffectiveName Value="Kick" /></Name>
        <MainSequencer>
          <Sample>
            <ArrangerAutomation>
              <Events>
                <AudioClip Id="10">
                  <CurrentStart Value="0" />
                  <CurrentEnd Value="10" />
                  <LoopStart Value="0" />
                  <LoopEnd Value="10" />
                  <OutMarker Value="20" />
                  <HiddenLoopStart Value="0" />
                  <HiddenLoopEnd Value="10" />
                  <RightTime Value="10" />
                  <SampleRef><FileRef><Path Value="old_kick.wav" /></FileRef></SampleRef>
                </AudioClip>
              </Events>
            </ArrangerAutomation>
          </Sample>
        </MainSequencer>
      </AudioTrack>
      <AudioTrack>
        <Id Value="0" />
        <TrackGroupId Value="-1" />
        <Name><EffectiveName Value="Snare" /></Name>
        <MainSequencer>
          <Sample>
            <ArrangerAutomation>
              <Events>
                <AudioClip Id="11">
                  <CurrentStart Value="0" />
                  <CurrentEnd Value="10" />
                  <LoopStart Value="0" />
                  <LoopEnd Value="10" />
                  <OutMarker Value="20" />
                  <HiddenLoopStart Value="0" />
                  <HiddenLoopEnd Value="10" />
                  <RightTime Value="10" />
                  <SampleRef><FileRef><Path Value="old_snare.wav" /></FileRef></SampleRef>
                </AudioClip>
              </Events>
            </ArrangerAutomation>
          </Sample>
        </MainSequencer>
      </AudioTrack>
    </Tracks>
    <MasterTrack>
      <DeviceChain><Mixer><Tempo><Manual Value="60" /></Tempo></Mixer></DeviceChain>
    </MasterTrack>
  </LiveSet>
</Ableton>
"""


def write_als(path: Path, xml_text: str = SAMPLE_XML) -> None:
    with gzip.open(path, "wb") as handle:
        handle.write(xml_text.encode("utf-8"))


def write_wav(path: Path, sample_rate: int = 48000, frames: int = 48000) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames)


def write_wav_with_samples(path: Path, sample_rate: int, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        payload = b"".join(int(sample).to_bytes(2, "little", signed=True) for sample in samples)
        handle.writeframes(payload)


class CliWorkflowTests(unittest.TestCase):
    def test_auto_defaults_outputs_inside_ableton_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            als_path = tmp / "ProjectX.als"
            exports_dir = tmp / "exports"
            parser = build_parser()

            args = parser.parse_args(["auto", "--als", str(als_path), "--exports", str(exports_dir)])
            resolve_auto_paths(args)

            self.assertEqual(tmp / "ableton-strip-silence" / "ProjectX", args.work_dir)
            self.assertEqual(tmp / "ableton-strip-silence" / "ProjectX" / "ProjectX.strip_silence.als", args.output)
            self.assertTrue(args.clear_existing)

            keep_args = parser.parse_args(["auto", "--als", str(als_path), "--exports", str(exports_dir), "--keep-existing"])
            resolve_auto_paths(keep_args)
            self.assertFalse(keep_args.clear_existing)

    def test_phase1_builds_expected_rename_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            als_path = tmp / "ProjectX.als"
            exports_dir = tmp / "exports"
            output_dir = tmp / "renamed"
            exports_dir.mkdir()
            write_als(als_path)
            write_wav(exports_dir / "ProjectX - Kick.wav")
            write_wav(exports_dir / "ProjectX - Snare.wav")
            write_wav(exports_dir / "ProjectX - Bass.wav")

            operations, warnings = build_rename_plan(als_path, exports_dir, output_dir)

            self.assertEqual([], warnings)
            self.assertEqual(
                [
                    "002_Drums_Kick.wav",
                    "003_Drums_Snare.wav",
                    "004_Bass.wav",
                ],
                [operation.destination.name for operation in operations],
            )

    def test_phase1_writes_manifest_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            als_path = tmp / "ProjectX.als"
            exports_dir = tmp / "exports"
            output_dir = tmp / "renamed"
            exports_dir.mkdir()
            write_als(als_path)
            write_wav(exports_dir / "ProjectX - Kick.wav")
            write_wav(exports_dir / "ProjectX - Snare.wav")
            write_wav(exports_dir / "ProjectX - Bass.wav")

            result = execute_phase1(als_path, exports_dir, output_dir)

            self.assertTrue((output_dir / "002_Drums_Kick.wav").exists())
            manifest = json.loads((output_dir / "phase1_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(3, len(result["operations"]))
            self.assertEqual(result["operations"], manifest["operations"])

    def test_phase2_parses_renders_and_inserts_audio_clips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            als_path = tmp / "ProjectX.als"
            renders_dir = tmp / "renders"
            output_path = tmp / "restored.als"
            renders_dir.mkdir()
            write_als(als_path)
            write_wav(renders_dir / "002_Drums_Kick_tc_48000.wav", frames=48000)
            write_wav(renders_dir / "003_Drums_Snare_tc_96000.wav", frames=24000)

            parsed_renders, bpm = parse_render_clips(als_path, renders_dir)
            self.assertEqual(120.0, bpm)
            self.assertEqual([2, 3], [render.matched_track.index for render in parsed_renders])

            result = execute_phase2(als_path, renders_dir, output_path)

            self.assertTrue(output_path.exists())
            with gzip.open(output_path, "rb") as handle:
                root = ET.fromstring(handle.read())
            audio_clips = list(root.iter("AudioClip"))
            self.assertEqual(2, len(audio_clips))
            starts = [clip.find("CurrentStart").attrib["Value"] for clip in audio_clips]
            ends = [clip.find("CurrentEnd").attrib["Value"] for clip in audio_clips]
            self.assertEqual(["2.000000000000", "4.000000000000"], starts)
            self.assertEqual(["4.000000000000", "5.000000000000"], ends)
            self.assertEqual(2, len(result["inserted"]))
            relative_paths = [node.attrib.get("Value", "") for node in root.iter("RelativePath")]
            self.assertTrue(any(path.startswith("renders/") for path in relative_paths))
            self.assertFalse(any(path.lower().startswith("c:/") for path in relative_paths))

    def test_phase2_clear_existing_handles_duplicate_track_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            als_path = tmp / "ProjectX.als"
            renders_dir = tmp / "renders"
            output_path = tmp / "restored.als"
            renders_dir.mkdir()
            write_als(als_path, DUPLICATE_TRACK_ID_XML)
            write_wav(renders_dir / "001_Kick_tc_1000.wav", sample_rate=1000, frames=1000)
            write_wav(renders_dir / "002_Snare_tc_2000.wav", sample_rate=1000, frames=1000)

            result = execute_phase2(als_path, renders_dir, output_path, clear_existing=True)

            with gzip.open(output_path, "rb") as handle:
                root = ET.fromstring(handle.read())
            audio_clips = list(root.iter("AudioClip"))
            paths = [path.attrib.get("Value", "") for path in root.iter("Path")]
            loop_starts = [node.attrib.get("Value") for node in root.iter("LoopStart")]
            loop_ends = [node.attrib.get("Value") for node in root.iter("LoopEnd")]
            out_markers = [node.attrib.get("Value") for node in root.iter("OutMarker")]
            right_times = [node.attrib.get("Value") for node in root.iter("RightTime")]
            clip_times = [clip.attrib.get("Time") for clip in audio_clips]
            self.assertEqual(2, len(audio_clips))
            self.assertFalse(any("old_" in path for path in paths))
            self.assertEqual(["1.000000000000", "2.000000000000"], clip_times)
            self.assertEqual(["0", "0"], loop_starts)
            self.assertEqual(["1.000000000000", "1.000000000000"], loop_ends)
            self.assertEqual(["1.000000000000", "1.000000000000"], out_markers)
            self.assertEqual([], right_times)
            self.assertTrue(result["summary"]["clear_existing"])

    def test_phase2_clear_existing_can_clear_matched_tracks_without_rendered_clips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            als_path = tmp / "ProjectX.als"
            renders_dir = tmp / "renders"
            output_path = tmp / "restored.als"
            renders_dir.mkdir()
            write_als(als_path, DUPLICATE_TRACK_ID_XML)
            write_wav(renders_dir / "001_Kick_tc_1000.wav", sample_rate=1000, frames=1000)

            result = execute_phase2(
                als_path,
                renders_dir,
                output_path,
                clear_existing=True,
                clear_track_indices={1, 2},
            )

            with gzip.open(output_path, "rb") as handle:
                root = ET.fromstring(handle.read())
            audio_clips = list(root.iter("AudioClip"))
            paths = [path.attrib.get("Value", "") for path in root.iter("Path")]
            self.assertEqual(1, len(audio_clips))
            self.assertFalse(any("old_snare" in path for path in paths))
            self.assertEqual(2, result["summary"]["cleared_tracks"])

    def test_timecode_helpers(self) -> None:
        parsed = parse_timecode_from_name(Path("002_Drums_Kick_tc_48000.wav"))
        self.assertEqual(("002_Drums_Kick", 48000), parsed)
        self.assertAlmostEqual(2.0, samples_to_beats(48000, 48000, 120.0))

    def test_beat_map_normalizes_negative_automation_start(self) -> None:
        beat_map = build_beat_map([
            (-63072000.0, 116.0, None, None, None, None),
            (256.0, 136.0, None, None, None, None),
        ])

        self.assertAlmostEqual(19.333333333333332, sample_position_to_beats(480000, 48000, beat_map))
        self.assertAlmostEqual(77.33333333333333, sample_position_to_beats(1920000, 48000, beat_map))
        self.assertAlmostEqual(409.1954022988506, sample_position_to_beats(9600000, 48000, beat_map))

    def test_bwf_reader_returns_none_for_plain_wav(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            wav_path = Path(tmp_dir) / "plain.wav"
            write_wav(wav_path)
            self.assertIsNone(read_bwf_time_reference_samples(wav_path))

    def test_strip_silence_writes_timecoded_clips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            inputs = tmp / "inputs"
            output = tmp / "clips"
            inputs.mkdir()
            samples = [0] * 1000 + [12000] * 1000 + [0] * 1000
            write_wav_with_samples(inputs / "002_Drums_Kick.wav", sample_rate=1000, samples=samples)

            result = trim_directory(
                inputs_dir=inputs,
                output_dir=output,
                settings=TrimSettings(
                    threshold_db=-30,
                    min_silence_ms=200,
                    min_clip_ms=100,
                    keep_leading_ms=0,
                    keep_trailing_ms=0,
                    window_ms=50,
                    hop_ms=50,
                ),
            )

            self.assertEqual(1, result["summary"]["output_clips"])
            clip = result["clips"][0]
            self.assertTrue(Path(clip["output"]).exists())
            self.assertTrue(Path(clip["output"]).name.startswith("002_part_001_tc_1000"))
            self.assertEqual(1000, clip["start_samples"])
            self.assertEqual(2000, clip["end_samples"])

    def test_strip_silence_rejects_invalid_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            inputs = tmp / "inputs"
            output = tmp / "clips"
            inputs.mkdir()
            write_wav_with_samples(inputs / "002_Drums_Kick.wav", sample_rate=1000, samples=[0, 1, 0])

            with self.assertRaises(ValueError):
                trim_directory(
                    inputs_dir=inputs,
                    output_dir=output,
                    settings=TrimSettings(min_silence_ms=-1),
                )

            with self.assertRaises(ValueError):
                trim_directory(
                    inputs_dir=inputs,
                    output_dir=output,
                    settings=TrimSettings(detection="unexpected"),
                )


if __name__ == "__main__":
    unittest.main()
