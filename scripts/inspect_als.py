from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def find_first_value(element: ET.Element, candidate_tags: set[str]) -> str | None:
    for node in element.iter():
        if local_name(node.tag) not in candidate_tags:
            continue
        value = node.attrib.get("Value") or (node.text or "").strip()
        if value:
            return value
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect an Ableton .als file and print a JSON schema snapshot.")
    parser.add_argument("als", type=Path, help="Path to .als file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    als_path = args.als
    with gzip.open(als_path, "rb") as handle:
        root = ET.fromstring(handle.read())

    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""

    tracks_container = next((node for node in root.iter() if local_name(node.tag) == "Tracks"), None)
    track_nodes = []
    if tracks_container is not None:
        track_nodes = [
            node for node in list(tracks_container) if local_name(node.tag) in {"AudioTrack", "GroupTrack", "MidiTrack"}
        ]

    track_info = []
    direct_fields = []
    for index, track in enumerate(track_nodes, start=1):
        track_info.append(
            {
                "index": index,
                "tag": local_name(track.tag),
                "id": find_first_value(track, {"Id", "LomId", "TrackId"}),
                "group_id": find_first_value(track, {"TrackGroupId", "GroupTrackId", "ParentGroupTrackId"}),
                "name": find_first_value(track, {"EffectiveName", "UserName", "Name"}),
            }
        )
        if index <= 12:
            snapshot: dict[str, str | None] = {}
            for child in list(track):
                child_name = local_name(child.tag)
                value = child.attrib.get("Value")
                if value is None and len(child):
                    for grandchild in list(child):
                        value = grandchild.attrib.get("Value")
                        if value:
                            break
                snapshot[child_name] = value
            direct_fields.append({"index": index, "tag": local_name(track.tag), "fields": snapshot})

    audio_clips = [node for node in root.iter() if local_name(node.tag) == "AudioClip"]
    first_audio_clip = audio_clips[0] if audio_clips else None
    first_audio_clip_children = [local_name(child.tag) for child in list(first_audio_clip)] if first_audio_clip is not None else []
    first_audio_clip_sample_ref_children: list[str] = []
    first_audio_clip_sample_ref_values: dict[str, str] = {}
    first_audio_clip_file_ref_children: list[str] = []
    first_audio_clip_file_ref_values: dict[str, str] = {}
    if first_audio_clip is not None:
        sample_ref = next((node for node in first_audio_clip.iter() if local_name(node.tag) in {"SampleRef", "FileRef"}), None)
        if sample_ref is not None:
            for child in list(sample_ref):
                first_audio_clip_sample_ref_children.append(local_name(child.tag))
                value = child.attrib.get("Value")
                if value:
                    first_audio_clip_sample_ref_values[local_name(child.tag)] = value
            file_ref = next((node for node in sample_ref.iter() if local_name(node.tag) == "FileRef"), None)
            if file_ref is not None:
                for child in list(file_ref):
                    first_audio_clip_file_ref_children.append(local_name(child.tag))
                    value = child.attrib.get("Value")
                    if value:
                        first_audio_clip_file_ref_values[local_name(child.tag)] = value

    group_track_id_candidates: list[dict] = []
    for index, track in enumerate(track_nodes, start=1):
        if local_name(track.tag) != "GroupTrack":
            continue
        pairs: list[tuple[str, str]] = []
        for node in track.iter():
            tag_name = local_name(node.tag)
            if "id" not in tag_name.lower():
                continue
            value = node.attrib.get("Value")
            if value is None:
                continue
            pairs.append((tag_name, value))
        group_track_id_candidates.append(
            {
                "index": index,
                "name": find_first_value(track, {"EffectiveName", "UserName", "Name"}),
                "id_pairs_head": pairs[:30],
                "id_pairs_nontrivial": [
                    pair for pair in pairs if pair[1] not in {"0", "-1", "", "false", "true"}
                ][:80],
            }
        )

    events_nodes = [node for node in root.iter() if local_name(node.tag) in {"Events", "Clips"}]

    tempo_candidates: list[float] = []
    for node in root.iter():
        if local_name(node.tag) not in {"Manual", "CurrentSongTempo", "Tempo"}:
            continue
        raw = node.attrib.get("Value") or (node.text or "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if 20 <= value <= 300:
            tempo_candidates.append(value)

    print(
        json.dumps(
            {
                "root_tag": local_name(root.tag),
                "has_namespace": bool(namespace),
                "tracks_count": len(track_nodes),
                "first_20_tracks": track_info[:20],
                "first_12_tracks_direct_fields": direct_fields,
                "audio_clip_count": len(audio_clips),
                "first_audio_clip_child_tags": first_audio_clip_children[:50],
                "first_audio_clip_sample_ref_children": first_audio_clip_sample_ref_children,
                "first_audio_clip_sample_ref_values": first_audio_clip_sample_ref_values,
                "first_audio_clip_file_ref_children": first_audio_clip_file_ref_children,
                "first_audio_clip_file_ref_values": first_audio_clip_file_ref_values,
                "group_track_id_candidates": group_track_id_candidates,
                "events_or_clips_node_count": len(events_nodes),
                "tempo_candidates": tempo_candidates[:10],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
