from __future__ import annotations

import copy
import gzip
import logging
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional
import xml.etree.ElementTree as ET
from defusedxml import ElementTree as DefusedET

from .models import ParsedSet, TrackInfo

LOGGER = logging.getLogger(__name__)


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def detect_namespace(root: ET.Element) -> str:
    if root.tag.startswith("{") and "}" in root.tag:
        return root.tag[1:].split("}", 1)[0]
    return ""


def qname(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}" if namespace else tag


def read_als(path: Path) -> ParsedSet:
    LOGGER.debug("Reading ALS file: %s", path)
    with gzip.open(path, "rb") as handle:
        data = handle.read()
    root = DefusedET.fromstring(data)
    tree = ET.ElementTree(root)
    namespace = detect_namespace(root)
    tracks = extract_tracks(root)
    tempo = extract_tempo(root)
    return ParsedSet(path=path, tree=tree, root=root, namespace=namespace, tempo_bpm=tempo, tracks=tracks)


def write_als(parsed: ParsedSet, output_path: Path) -> None:
    LOGGER.debug("Writing ALS file: %s", output_path)
    xml_bytes = ET.tostring(parsed.root, encoding="utf-8", xml_declaration=True)
    with gzip.open(output_path, "wb") as handle:
        handle.write(xml_bytes)


def iter_descendants(element: ET.Element, tag_name: Optional[str] = None) -> Iterable[ET.Element]:
    for node in element.iter():
        if tag_name is None or local_name(node.tag) == tag_name:
            yield node


def child_by_local_name(element: ET.Element, tag_name: str) -> Optional[ET.Element]:
    for child in list(element):
        if local_name(child.tag) == tag_name:
            return child
    return None


def descendants_by_local_name(element: ET.Element, tag_name: str) -> list[ET.Element]:
    return [node for node in element.iter() if local_name(node.tag) == tag_name]


def find_first_value(element: ET.Element, candidate_tags: Iterable[str]) -> Optional[str]:
    names = set(candidate_tags)
    for node in element.iter():
        if local_name(node.tag) not in names:
            continue
        if "Value" in node.attrib:
            return node.attrib["Value"]
        text = (node.text or "").strip()
        if text:
            return text
    return None


def direct_child_value(element: ET.Element, child_name: str) -> Optional[str]:
    child = child_by_local_name(element, child_name)
    if child is None:
        return None
    if "Value" in child.attrib:
        value = child.attrib["Value"].strip()
        if value:
            return value
    text = (child.text or "").strip()
    if text:
        return text
    for grandchild in list(child):
        if "Value" in grandchild.attrib:
            value = grandchild.attrib["Value"].strip()
            if value:
                return value
        nested_text = (grandchild.text or "").strip()
        if nested_text:
            return nested_text
    return None


def extract_track_name(track: ET.Element) -> str:
    for candidate in (
        ("Name", "EffectiveName"),
        ("Name", "UserName"),
        ("Name", "Annotation"),
    ):
        parent = track
        found = None
        for part in candidate:
            found = child_by_local_name(parent, part)
            if found is None:
                break
            parent = found
        if found is not None:
            if "Value" in found.attrib and found.attrib["Value"].strip():
                return found.attrib["Value"].strip()
            text = (found.text or "").strip()
            if text:
                return text

    for name_tag in ("EffectiveName", "UserName", "Name"):
        value = find_first_value(track, [name_tag])
        if value:
            return value

    return "UnnamedTrack"


def extract_track_id(track: ET.Element) -> str:
    for field_name in ("LomId", "Id", "TrackId"):
        value = direct_child_value(track, field_name)
        if value:
            return value
    value = find_first_value(track, ["LomId", "Id", "TrackId"])
    if value:
        return value
    return str(id(track))


def extract_group_id(track: ET.Element) -> Optional[str]:
    for field_name in ("TrackGroupId", "GroupTrackId", "ParentGroupTrackId"):
        value = direct_child_value(track, field_name)
        if value and value not in {"", "-1", "0"}:
            return value
    value = find_first_value(track, ["TrackGroupId", "GroupTrackId", "ParentGroupTrackId"])
    if value in {None, "", "-1", "0"}:
        return None
    return value


def find_tracks_container(root: ET.Element) -> Optional[ET.Element]:
    for node in root.iter():
        if local_name(node.tag) == "Tracks":
            return node
    return None


def extract_tracks(root: ET.Element) -> list[TrackInfo]:
    container = find_tracks_container(root)
    if container is None:
        raise ValueError("Could not locate <Tracks> container in ALS XML.")

    tracks: list[TrackInfo] = []
    for child in list(container):
        tag = local_name(child.tag)
        if tag not in {"AudioTrack", "GroupTrack"}:
            continue
        tracks.append(
            TrackInfo(
                index=len(tracks) + 1,
                track_id=extract_track_id(child),
                track_type=tag,
                name=extract_track_name(child),
                group_id=extract_group_id(child),
                element=child,
            )
        )

    group_token_to_track: dict[str, TrackInfo] = {}
    for track in tracks:
        if track.track_type != "GroupTrack":
            continue
        token = track.track_id
        if token and token not in {"0", "-1"} and token not in group_token_to_track:
            group_token_to_track[token] = track

    group_indices = [idx for idx, track in enumerate(tracks) if track.track_type == "GroupTrack"]
    for pos, group_index in enumerate(group_indices):
        group_track = tracks[group_index]
        next_group_index = group_indices[pos + 1] if pos + 1 < len(group_indices) else len(tracks)
        in_between = tracks[group_index + 1 : next_group_index]
        candidate_tokens = [
            track.group_id
            for track in in_between
            if track.group_id not in {None, "", "0", "-1"} and track.track_type != "GroupTrack"
        ]
        if not candidate_tokens:
            continue
        token, count = Counter(candidate_tokens).most_common(1)[0]
        if token not in group_token_to_track and count > 0:
            group_token_to_track[token] = group_track

    LOGGER.debug(
        "Resolved %d group tokens for %d group tracks.",
        len(group_token_to_track),
        len([track for track in tracks if track.track_type == "GroupTrack"]),
    )

    for track in tracks:
        group_path: list[str] = []
        current_group_id = track.group_id
        seen: set[str] = set()
        while current_group_id and current_group_id not in seen:
            seen.add(current_group_id)
            parent = group_token_to_track.get(current_group_id)
            if parent is None:
                break
            group_path.insert(0, parent.name)
            current_group_id = parent.group_id
        track.group_path = group_path
        track.group_resolution = "resolved" if group_path else ("none" if not track.group_id else "unresolved")

    return tracks


def extract_tempo(root: ET.Element) -> Optional[float]:
    candidate_paths = [
        ["LiveSet", "MasterTrack", "DeviceChain", "Mixer", "Tempo", "Manual"],
        ["LiveSet", "MainTrack", "DeviceChain", "Mixer", "Tempo", "Manual"],
        ["MasterTrack", "DeviceChain", "Mixer", "Tempo", "Manual"],
        ["MainTrack", "DeviceChain", "Mixer", "Tempo", "Manual"],
        ["Tempo", "Manual"],
        ["CurrentSongTempo"],
    ]

    for path in candidate_paths:
        node = find_path(root, path)
        if node is None:
            continue
        raw = node.attrib.get("Value") or (node.text or "").strip()
        if not raw:
            continue
        try:
            return float(raw)
        except ValueError:
            LOGGER.debug("Failed to parse tempo value: %s", raw)
    return None


def find_path(element: ET.Element, parts: list[str]) -> Optional[ET.Element]:
    current = element
    for index, part in enumerate(parts):
        next_node = child_by_local_name(current, part)
        if next_node is None:
            if index == 0 and local_name(current.tag) == part:
                continue
            return None
        current = next_node
    return current


def sanitize_component(text: str) -> str:
    sanitized = "".join(char if char.isalnum() or char in {"-", "_", " "} else "_" for char in text)
    sanitized = "_".join(filter(None, sanitized.replace(" ", "_").split("_")))
    return sanitized or "unnamed"


def format_export_name(track: TrackInfo, extension: str) -> str:
    serial = f"{track.index:03d}"
    segments = [serial]
    if track.group_path:
        segments.extend(sanitize_component(part) for part in track.group_path)
    segments.append(sanitize_component(track.name))
    return "_".join(segments) + extension


def find_arrangement_events_container(track: TrackInfo) -> Optional[ET.Element]:
    if track.element is None:
        return None

    priority_paths = [
        ["ArrangementClipsListWrapper", "Events"],
        ["ArrangementClipsListWrapper", "Clips"],
        ["MainSequencer", "Sample", "ArrangerAutomation", "Events"],
        ["MainSequencer", "Sample", "ArrangerAutomation", "Clips"],
        ["DeviceChain", "MainSequencer", "Sample", "ArrangerAutomation", "Events"],
        ["DeviceChain", "MainSequencer", "Sample", "ArrangerAutomation", "Clips"],
    ]
    for path in priority_paths:
        node = find_path(track.element, path)
        if node is not None:
            return node

    wrapper = child_by_local_name(track.element, "ArrangementClipsListWrapper")
    if wrapper is not None:
        for candidate in list(wrapper):
            if local_name(candidate.tag) in {"Events", "Clips"}:
                return candidate

    for candidate in track.element.iter():
        if local_name(candidate.tag) in {"Events", "Clips"} and list(candidate):
            if any(local_name(grandchild.tag) == "AudioClip" for grandchild in list(candidate)):
                return candidate
    return None


def find_audio_clip_template(root: ET.Element, track: Optional[TrackInfo] = None) -> Optional[ET.Element]:
    if track is not None:
        container = find_arrangement_events_container(track)
        if container is not None:
            for node in list(container):
                if local_name(node.tag) == "AudioClip":
                    return copy.deepcopy(node)

    search_roots: list[ET.Element] = [track.element] if track and track.element is not None else []
    search_roots.append(root)
    for search_root in search_roots:
        for node in search_root.iter():
            if local_name(node.tag) == "AudioClip":
                return copy.deepcopy(node)
    return None


def next_clip_id(root: ET.Element) -> int:
    values: list[int] = []
    for node in root.iter():
        if local_name(node.tag) != "AudioClip":
            continue
        raw = node.attrib.get("Id")
        if raw and raw.isdigit():
            values.append(int(raw))
        id_node = child_by_local_name(node, "Id")
        if id_node is not None:
            value = id_node.attrib.get("Value")
            if value and value.isdigit():
                values.append(int(value))
    return (max(values) + 1) if values else 1


def next_numeric_value(root: ET.Element, tag_names: Iterable[str], default_start: int = 1) -> int:
    names = set(tag_names)
    values: list[int] = []
    for node in root.iter():
        if local_name(node.tag) not in names:
            continue
        raw = node.attrib.get("Value") or (node.text or "").strip()
        if raw and raw.lstrip("-").isdigit():
            values.append(int(raw))
    if not values:
        return default_start
    return max(values) + 1


def set_value_on_first_descendant(element: ET.Element, tag_names: Iterable[str], value: str) -> bool:
    names = set(tag_names)
    for node in element.iter():
        if local_name(node.tag) in names:
            node.attrib["Value"] = value
            return True
    return False


def append_value_element(parent: ET.Element, namespace: str, tag_name: str, value: str) -> ET.Element:
    node = ET.SubElement(parent, qname(namespace, tag_name))
    node.attrib["Value"] = value
    return node


def update_file_ref(clip: ET.Element, namespace: str, file_path: Path, project_root: Path | None = None) -> None:
    sample_ref = next((node for node in clip.iter() if local_name(node.tag) == "SampleRef"), None)
    if sample_ref is None:
        sample_ref = ET.SubElement(clip, qname(namespace, "SampleRef"))

    file_ref = next((node for node in sample_ref.iter() if local_name(node.tag) == "FileRef"), None)
    if file_ref is None:
        file_ref = ET.SubElement(sample_ref, qname(namespace, "FileRef"))

    resolved_file_path = file_path.resolve()
    abs_path = resolved_file_path.as_posix()
    relative_path = abs_path
    if project_root is not None:
        try:
            relative_path = resolved_file_path.relative_to(project_root.resolve()).as_posix()
        except ValueError:
            relative_path = abs_path
    file_name = file_path.name

    touched: set[str] = set()
    for node in file_ref.iter():
        node_name = local_name(node.tag)
        if node_name == "Path":
            node.attrib["Value"] = abs_path
            touched.add(node_name)
        elif node_name == "RelativePath":
            node.attrib["Value"] = relative_path
            touched.add(node_name)
        elif node_name in {"FileName", "Name"}:
            node.attrib["Value"] = file_name
            touched.add(node_name)

    if "Path" not in touched:
        append_value_element(file_ref, namespace, "Path", abs_path)
    if "RelativePath" not in touched:
        append_value_element(file_ref, namespace, "RelativePath", relative_path)
    if "Name" not in touched and "FileName" not in touched:
        append_value_element(file_ref, namespace, "Name", file_name)


def update_sample_ref_metadata(
    clip: ET.Element,
    namespace: str,
    file_path: Path,
    duration_samples: int | None,
    sample_rate: int | None,
) -> None:
    sample_ref = next((node for node in clip.iter() if local_name(node.tag) == "SampleRef"), None)
    if sample_ref is None:
        sample_ref = ET.SubElement(clip, qname(namespace, "SampleRef"))

    if duration_samples is not None:
        if not set_value_on_first_descendant(sample_ref, {"DefaultDuration"}, str(duration_samples)):
            append_value_element(sample_ref, namespace, "DefaultDuration", str(duration_samples))
    if sample_rate is not None:
        if not set_value_on_first_descendant(sample_ref, {"DefaultSampleRate"}, str(sample_rate)):
            append_value_element(sample_ref, namespace, "DefaultSampleRate", str(sample_rate))

    try:
        modified_at = int(file_path.stat().st_mtime)
    except OSError:
        modified_at = None
    if modified_at is not None:
        if not set_value_on_first_descendant(sample_ref, {"LastModDate"}, str(modified_at)):
            append_value_element(sample_ref, namespace, "LastModDate", str(modified_at))


def build_audio_clip(
    parsed: ParsedSet,
    track: TrackInfo,
    file_path: Path,
    clip_name: str,
    start_beats: float,
    end_beats: float,
    duration_samples: int | None = None,
    sample_rate: int | None = None,
    template: ET.Element | None = None,
) -> ET.Element:
    template = copy.deepcopy(template) if template is not None else find_audio_clip_template(parsed.root, track)
    namespace = parsed.namespace
    source_start = "0"
    source_end = None
    source_out_marker = None
    if duration_samples is not None and sample_rate is not None and sample_rate > 0:
        duration_seconds = duration_samples / sample_rate
        source_end = f"{duration_seconds:.12f}"
        source_out_marker = f"{duration_seconds * 2.0:.12f}"

    if template is None:
        LOGGER.debug("No existing AudioClip template found. Creating a minimal AudioClip node.")
        clip = ET.Element(qname(namespace, "AudioClip"), {"Id": str(next_clip_id(parsed.root)), "Time": f"{start_beats:.12f}"})
        append_value_element(clip, namespace, "Name", clip_name)
        append_value_element(clip, namespace, "LomId", str(next_numeric_value(parsed.root, {"LomId"}, default_start=1)))
        append_value_element(clip, namespace, "CurrentStart", f"{start_beats:.12f}")
        append_value_element(clip, namespace, "CurrentEnd", f"{end_beats:.12f}")
        update_source_timing(clip, source_start, source_end, source_out_marker)
        update_file_ref(clip, namespace, file_path, parsed.path.parent)
        update_sample_ref_metadata(clip, namespace, file_path, duration_samples, sample_rate)
        return clip

    clip = template
    clip.attrib["Time"] = f"{start_beats:.12f}"
    if "Id" in clip.attrib:
        clip.attrib["Id"] = str(next_clip_id(parsed.root))

    set_value_on_first_descendant(clip, {"LomId"}, str(next_numeric_value(parsed.root, {"LomId"}, default_start=1)))
    if set_value_on_first_descendant(clip, {"TakeId"}, str(next_numeric_value(parsed.root, {"TakeId"}, default_start=1))):
        LOGGER.debug("Updated TakeId for cloned clip on track '%s'", track.name)

    if not set_value_on_first_descendant(clip, {"Name", "EffectiveName", "UserName"}, clip_name):
        append_value_element(clip, namespace, "Name", clip_name)
    if not set_value_on_first_descendant(clip, {"CurrentStart", "Start", "ClipStart"}, f"{start_beats:.12f}"):
        append_value_element(clip, namespace, "CurrentStart", f"{start_beats:.12f}")
    if not set_value_on_first_descendant(clip, {"CurrentEnd", "End", "ClipEnd"}, f"{end_beats:.12f}"):
        append_value_element(clip, namespace, "CurrentEnd", f"{end_beats:.12f}")
    update_source_timing(clip, source_start, source_end, source_out_marker)
    update_file_ref(clip, namespace, file_path, parsed.path.parent)
    update_sample_ref_metadata(clip, namespace, file_path, duration_samples, sample_rate)
    return clip


def update_source_timing(clip: ET.Element, source_start: str, source_end: str | None, source_out_marker: str | None) -> None:
    set_value_on_first_descendant(clip, {"LoopStart"}, source_start)
    set_value_on_first_descendant(clip, {"HiddenLoopStart"}, source_start)
    set_value_on_first_descendant(clip, {"StartRelative"}, source_start)
    set_value_on_first_descendant(clip, {"LeftTime"}, source_start)
    set_value_on_first_descendant(clip, {"AnchorTime"}, source_start)
    set_value_on_first_descendant(clip, {"OtherTime"}, source_start)
    set_value_on_first_descendant(clip, {"FreezeStart"}, source_start)

    if source_end is None:
        return
    set_value_on_first_descendant(clip, {"LoopEnd"}, source_end)
    set_value_on_first_descendant(clip, {"HiddenLoopEnd"}, source_end)
    set_value_on_first_descendant(clip, {"RightTime"}, source_end)
    if source_out_marker is not None:
        set_value_on_first_descendant(clip, {"OutMarker"}, source_out_marker)
    set_value_on_first_descendant(clip, {"FreezeEnd"}, source_start)


def insert_clip(parsed: ParsedSet, track: TrackInfo, clip: ET.Element) -> None:
    container = find_arrangement_events_container(track)
    if container is None:
        raise ValueError(f"Could not locate arrangement events container for track '{track.name}'.")

    start_value = None
    for node in clip.iter():
        if local_name(node.tag) in {"CurrentStart", "Start", "ClipStart"}:
            start_value = node.attrib.get("Value")
            break
    start_float = float(start_value) if start_value is not None else 0.0

    existing_clips = [child for child in list(container) if local_name(child.tag) == "AudioClip"]
    insert_at = len(existing_clips)
    for idx, existing in enumerate(existing_clips):
        existing_start = 0.0
        for node in existing.iter():
            if local_name(node.tag) in {"CurrentStart", "Start", "ClipStart"}:
                try:
                    existing_start = float(node.attrib.get("Value", "0"))
                except ValueError:
                    existing_start = 0.0
                break
        if start_float < existing_start:
            insert_at = idx
            break

    container.insert(insert_at, clip)


def clear_audio_clips(track: TrackInfo) -> None:
    container = find_arrangement_events_container(track)
    if container is None:
        return
    for child in list(container):
        if local_name(child.tag) == "AudioClip":
            container.remove(child)


def track_by_index(tracks: list[TrackInfo], index: int) -> Optional[TrackInfo]:
    for track in tracks:
        if track.index == index:
            return track
    return None


def track_by_name(tracks: list[TrackInfo], name: str) -> Optional[TrackInfo]:
    normalized = normalize_name(name)
    for track in tracks:
        if normalize_name(track.name) == normalized:
            return track
        if normalize_name(track.prefixed_name) == normalized:
            return track
    return None


def normalize_name(value: str) -> str:
    filtered = [char.lower() if char.isalnum() else " " for char in value]
    return " ".join("".join(filtered).split())


def strip_project_prefix(stem: str, project_name: str) -> str:
    prefix = f"{project_name} - "
    if stem.startswith(prefix):
        return stem[len(prefix):]
    return stem
