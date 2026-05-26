from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET


@dataclass(slots=True)
class TrackInfo:
    index: int
    track_id: str
    track_type: str
    name: str
    group_id: Optional[str]
    group_path: list[str] = field(default_factory=lambda: [])
    group_resolution: str = "unresolved"
    element: Optional[ET.Element] = None

    @property
    def prefixed_name(self) -> str:
        if self.group_path:
            return "_".join([*self.group_path, self.name])
        return self.name


@dataclass(slots=True)
class ParsedSet:
    path: Path
    tree: ET.ElementTree
    root: ET.Element
    namespace: str
    tempo_bpm: Optional[float]
    tracks: list[TrackInfo]


@dataclass(slots=True)
class RenameOperation:
    source: Path
    destination: Path
    track: TrackInfo
    matched_name: str
    match_strategy: str


@dataclass(slots=True)
class RenderClip:
    path: Path
    base_name: str
    start_samples: int
    sample_rate: int
    frame_count: int
    matched_track: TrackInfo

    @property
    def duration_samples(self) -> int:
        return self.frame_count
