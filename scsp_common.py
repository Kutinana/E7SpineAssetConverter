"""
Common types, reader, helpers, and decompression for SCSP parsing.

Shared by both V2 (Spine 2.1.27) and V3 (Spine 3.8.99) code paths.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import struct
import sys
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple, Set

import lz4.block
import numpy as np

ENDIAN = "<"

_S_b = struct.Struct("<b")
_S_B = struct.Struct("<B")
_S_h = struct.Struct("<h")
_S_H = struct.Struct("<H")
_S_i = struct.Struct("<i")
_S_I = struct.Struct("<I")
_S_f = struct.Struct("<f")

# ==============================
# Enums
# ==============================
class ScspVersion(IntEnum):
    V2 = 1       # 2.1.27
    V3 = 30001   # 3.8.99

class Inherit(IntEnum):
    Normal = 0
    OnlyTranslation = 1
    NoRotationOrReflection = 2
    NoScale = 3
    NoScaleOrReflection = 4

class BlendMode(IntEnum):
    Normal = 0
    Additive = 1
    Multiply = 2
    Screen = 3

class PositionMode(IntEnum):
    Fixed = 0
    Percent = 1

class SpacingMode(IntEnum):
    Length = 0
    Fixed = 1
    Percent = 2
    Proportional = 3

class RotateMode(IntEnum):
    Tangent = 0
    Chain = 1
    ChainScale = 2

class CurveType(IntEnum):
    LINEAR = 0
    STEPPED = 1
    BEZIER = 2

class AttachmentType(IntEnum):
    Region = 0
    Boundingbox = 1
    Mesh = 2
    Linkedmesh = 3
    Path = 4
    Point = 5
    Clipping = 6

class V2AttachmentType(IntEnum):
    Region = 0
    BoundingBox = 1
    Mesh = 2
    SkinnedMesh = 3

class V2TimelineType(IntEnum):
    Scale = 0
    Rotate = 1
    Translate = 2
    Color = 3
    Attachment = 4
    FlipX = 5
    FlipY = 6
    FFD = 7
    IkConstraint = 8
    DrawOrder = 9
    Event = 10

class TimelineType(IntEnum):
    Rotate = 0
    Translate = 1
    Scale = 2
    Shear = 3
    Attachment = 4
    Color = 5
    TwoColor = 14
    Deform = 6
    Event = 7
    DrawOrder = 8
    IkConstraint = 9
    TransformConstraint = 10
    PathConstraintPosition = 11
    PathConstraintSpacing = 12
    PathConstraintMix = 13

# ==============================
# Data Classes
# ==============================
@dataclass
class Color:
    r: int = 0xFF
    g: int = 0xFF
    b: int = 0xFF
    a: int = 0xFF

def color_to_string(color: Color, has_alpha: bool = True) -> str:
    if has_alpha:
        return f"{color.r:02X}{color.g:02X}{color.b:02X}{color.a:02X}"
    return f"{color.r:02X}{color.g:02X}{color.b:02X}"

def f32_color(r: float, g: float, b: float, a: float) -> Color:
    return Color(
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
        max(0, min(255, round(a * 255)))
    )

def uint64_to_base64(v: int) -> str:
    return base64.b64encode(struct.pack("<Q", v)).decode("ascii").rstrip("=")

# ---- Attachment data classes ----
@dataclass
class Attachment:
    name: Optional[str] = None
    path: Optional[str] = None
    type: AttachmentType = AttachmentType.Region

@dataclass
class VertexAttachment(Attachment):
    vertexCount: int = 0
    isWeighted: bool = False
    vertices: List[float] = field(default_factory=list)

@dataclass
class RegionAttachment(Attachment):
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    scaleX: float = 1.0
    scaleY: float = 1.0
    width: float = 0.0
    height: float = 0.0
    color: Optional[Color] = None

@dataclass
class MeshAttachment(VertexAttachment):
    uvs: List[float] = field(default_factory=list)
    triangles: List[int] = field(default_factory=list)
    hullLength: int = 0
    color: Optional[Color] = None
    edges: List[int] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    path: Optional[str] = None

@dataclass
class LinkedMeshAttachment(VertexAttachment):
    skinIndex: int = 0
    parentMesh: Optional[str] = None
    deform: bool = True
    width: float = 0.0
    height: float = 0.0
    color: Optional[Color] = None

@dataclass
class BoundingBoxAttachment(VertexAttachment):
    color: Optional[Color] = None

@dataclass
class PathAttachment(VertexAttachment):
    color: Optional[Color] = None
    closed: bool = False
    constantSpeed: bool = False
    lengths: List[float] = field(default_factory=list)

@dataclass
class PointAttachment(Attachment):
    rotation: float = 0.0
    x: float = 0.0
    y: float = 0.0
    color: Optional[Color] = None

@dataclass
class ClippingAttachment(VertexAttachment):
    color: Optional[Color] = None
    endSlot: Optional[str] = None

# V2 skinnedmesh: stored as weighted mesh vertices directly
@dataclass
class SkinnedMeshAttachment(Attachment):
    bones: List[int] = field(default_factory=list)
    weights: List[float] = field(default_factory=list)
    triangles: List[int] = field(default_factory=list)
    uvs: List[float] = field(default_factory=list)
    hullLength: int = 0
    color: Optional[Color] = None
    width: float = 0.0
    height: float = 0.0

# ---- Bone/Slot/Constraint/Skin/Event/Animation ----
@dataclass
class BoneData:
    name: Optional[str] = None
    parent: Optional[str] = None
    rotation: float = 0.0
    x: float = 0.0
    y: float = 0.0
    scaleX: float = 1.0
    scaleY: float = 1.0
    shearX: float = 0.0
    shearY: float = 0.0
    length: float = 0.0
    inherit: Inherit = Inherit.Normal
    skinRequired: bool = False
    color: Optional[Color] = None
    # V2 fields
    flipX: bool = False
    flipY: bool = False
    inheritScale: bool = True
    inheritRotation: bool = True

@dataclass
class SlotData:
    name: Optional[str] = None
    bone: Optional[str] = None
    color: Optional[Color] = None
    darkColor: Optional[Color] = None
    attachmentName: Optional[str] = None
    blendMode: BlendMode = BlendMode.Normal

@dataclass
class IKConstraintData:
    name: Optional[str] = None
    order: int = 0
    skinRequired: bool = False
    bones: List[str] = field(default_factory=list)
    target: Optional[str] = None
    mix: float = 1.0
    softness: float = 0.0
    bendPositive: bool = True
    compress: bool = False
    stretch: bool = False
    uniform: bool = False

@dataclass
class TransformConstraintData:
    name: Optional[str] = None
    order: int = 0
    skinRequired: bool = False
    bones: List[str] = field(default_factory=list)
    target: Optional[str] = None
    local: bool = False
    relative: bool = False
    offsetRotation: float = 0.0
    offsetX: float = 0.0
    offsetY: float = 0.0
    offsetScaleX: float = 0.0
    offsetScaleY: float = 0.0
    offsetShearY: float = 0.0
    rotateMix: float = 1.0
    translateMix: float = 1.0
    scaleMix: float = 1.0
    shearMix: float = 1.0

@dataclass
class PathConstraintData:
    name: Optional[str] = None
    order: int = 0
    skinRequired: bool = False
    bones: List[str] = field(default_factory=list)
    targetSlot: Optional[str] = None
    positionMode: PositionMode = PositionMode.Percent
    spacingMode: SpacingMode = SpacingMode.Length
    rotateMode: RotateMode = RotateMode.Tangent
    offsetRotation: float = 0.0
    position: float = 0.0
    spacing: float = 0.0
    rotateMix: float = 1.0
    translateMix: float = 1.0

@dataclass
class SkinData:
    name: Optional[str] = None
    attachments: Dict[str, Dict[str, Attachment]] = field(default_factory=dict)
    bones: List[str] = field(default_factory=list)
    ik: List[str] = field(default_factory=list)
    transform: List[str] = field(default_factory=list)
    paths: List[str] = field(default_factory=list)

@dataclass
class EventData:
    name: Optional[str] = None
    intValue: int = 0
    floatValue: float = 0.0
    stringValue: Optional[str] = None
    audioPath: Optional[str] = None
    volume: float = 1.0
    balance: float = 0.0

# ---- Timeline data classes (used by V3) ----
@dataclass
class TimelineData:
    type: TimelineType = TimelineType.Rotate
    frames: List[float] = field(default_factory=list)
    curves: List[float] = field(default_factory=list)
    times: List[float] = field(default_factory=list)

@dataclass
class RotateTimeline(TimelineData):
    bone_index: int = 0
    angles: List[float] = field(default_factory=list)
@dataclass
class TranslateTimeline(TimelineData):
    bone_index: int = 0
    xs: List[float] = field(default_factory=list)
    ys: List[float] = field(default_factory=list)
@dataclass
class ScaleTimeline(TimelineData):
    bone_index: int = 0
    xs: List[float] = field(default_factory=list)
    ys: List[float] = field(default_factory=list)
@dataclass
class ShearTimeline(TimelineData):
    bone_index: int = 0
    xs: List[float] = field(default_factory=list)
    ys: List[float] = field(default_factory=list)
@dataclass
class AttachmentTimeline(TimelineData):
    slot_index: int = 0
    names: List[Optional[str]] = field(default_factory=list)
@dataclass
class ColorTimeline(TimelineData):
    slot_index: int = 0
    colors: List[Color] = field(default_factory=list)
@dataclass
class DeformTimeline(TimelineData):
    skin: Optional[str] = None
    slot_index: int = 0
    attachment: Optional[str] = None
    offsets: List[List[float]] = field(default_factory=list)
    vertices: List[List[float]] = field(default_factory=list)
@dataclass
class EventTimeline(TimelineData):
    names: List[str] = field(default_factory=list)
@dataclass
class DrawOrderTimeline(TimelineData):
    orders: List[List[int]] = field(default_factory=list)
@dataclass
class IKTimeline(TimelineData):
    ik_index: int = 0
    mixs: List[float] = field(default_factory=list)
    softness: List[float] = field(default_factory=list)
    bend_directions: List[int] = field(default_factory=list)
    compresses: List[bool] = field(default_factory=list)
    stretches: List[bool] = field(default_factory=list)
@dataclass
class TransformTimeline(TimelineData):
    transform_index: int = 0
    rotateMixs: List[float] = field(default_factory=list)
    translateMixs: List[float] = field(default_factory=list)
    scaleMixs: List[float] = field(default_factory=list)
    shearMixs: List[float] = field(default_factory=list)
@dataclass
class PathPositionTimeline(TimelineData):
    path_index: int = 0
    positions: List[float] = field(default_factory=list)
@dataclass
class PathSpacingTimeline(TimelineData):
    path_index: int = 0
    spacings: List[float] = field(default_factory=list)
@dataclass
class PathMixTimeline(TimelineData):
    path_index: int = 0
    rotateMixs: List[float] = field(default_factory=list)
    translateMixs: List[float] = field(default_factory=list)
@dataclass
class TwoColorTimeline(TimelineData):
    slot_index: int = 0
    colorLights: List[Color] = field(default_factory=list)
    colorDarks: List[Color] = field(default_factory=list)

# V2 FFD timeline
@dataclass
class FFDTimeline(TimelineData):
    skin_name: Optional[str] = None
    slot_name: Optional[str] = None
    attachment_name: Optional[str] = None
    vertices: List[List[float]] = field(default_factory=list)

@dataclass
class AnimationData:
    name: Optional[str] = None
    duration: float = 0.0
    timelines: List[TimelineData] = field(default_factory=list)
    slots: Dict[str, Dict[str, List[Any]]] = field(default_factory=dict)
    bones: Dict[str, Dict[str, List[Any]]] = field(default_factory=dict)
    ik: Dict[str, List[Any]] = field(default_factory=dict)
    transform: Dict[str, List[Any]] = field(default_factory=dict)
    path: Dict[str, Dict[str, List[Any]]] = field(default_factory=dict)
    deform: Dict[str, Dict[str, Dict[str, List[Any]]]] = field(default_factory=dict)
    ffd: Dict[str, Dict[str, Dict[str, List[Any]]]] = field(default_factory=dict)
    drawOrder: List[Any] = field(default_factory=list)
    events: List[Any] = field(default_factory=list)
    _v2_draworder_raw: List[Any] = field(default_factory=list)
    _v2_trailing_arrays: List[List[int]] = field(default_factory=list)
    _v2_sentinel_times: List[float] = field(default_factory=list)

@dataclass
class SkeletonData:
    scspVersion: ScspVersion = ScspVersion.V3
    stringPool: bytes = b""
    hash: int = 0
    hashString: Optional[str] = None
    version: Optional[str] = None
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    nonessential: bool = True
    fps: float = 30.0
    imagesPath: Optional[str] = None
    audioPath: Optional[str] = None
    strings: List[str] = field(default_factory=list)
    bones: List[BoneData] = field(default_factory=list)
    slots: List[SlotData] = field(default_factory=list)
    ikConstraints: List[IKConstraintData] = field(default_factory=list)
    transformConstraints: List[TransformConstraintData] = field(default_factory=list)
    pathConstraints: List[PathConstraintData] = field(default_factory=list)
    skins: List[SkinData] = field(default_factory=list)
    events: List[EventData] = field(default_factory=list)
    animations: List[AnimationData] = field(default_factory=list)
    # V2 specific
    v2_bone_count: int = 0
    v2_ik_count: int = 0
    v2_slot_count: int = 0
    v2_skin_count: int = 0
    v2_event_count: int = 0
    v2_anim_count: int = 0
    v2_skin_records: List[Dict] = field(default_factory=list)
    v2_has_magic: bool = False
    _v2_used_pool_offsets: Set[int] = field(default_factory=set)


# ==============================
# Binary Reader
# ==============================
class SpineBinaryReader:
    def __init__(self, data: bytes, endian: str = ENDIAN):
        self.data = data
        self.pos = 0
        self.endian = endian

    def _read(self, size: int) -> bytes:
        if self.pos + size > len(self.data):
            raise EOFError(f"Unexpected end of data at {self.pos}, need {size} more bytes, have {len(self.data) - self.pos}")
        chunk = self.data[self.pos:self.pos + size]
        self.pos += size
        return chunk

    def reset_data(self, data: bytes, offset: int = 0) -> None:
        self.data = data
        self.pos = offset

    def reset_pos(self, pos: int = 0) -> None:
        self.pos = pos

    def read_byte(self) -> int:
        val = self.data[self.pos]
        self.pos += 1
        return val

    def read_bytes(self, n: int) -> bytes:
        return self._read(n)

    def read_sbyte(self) -> int:
        val = _S_b.unpack_from(self.data, self.pos)[0]
        self.pos += 1
        return val

    def read_boolean(self) -> bool:
        val = self.data[self.pos]
        self.pos += 1
        return val != 0

    def read_u8(self) -> int:
        val = self.data[self.pos]
        self.pos += 1
        return val

    def read_i16(self) -> int:
        val = _S_h.unpack_from(self.data, self.pos)[0]
        self.pos += 2
        return val

    def read_u16(self) -> int:
        val = _S_H.unpack_from(self.data, self.pos)[0]
        self.pos += 2
        return val

    def read_i32(self) -> int:
        val = _S_i.unpack_from(self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_u32(self) -> int:
        val = _S_I.unpack_from(self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_f32(self) -> float:
        val = _S_f.unpack_from(self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_color(self, has_alpha: bool = True) -> Color:
        p = self.pos
        d = self.data
        r, g, b = d[p], d[p + 1], d[p + 2]
        if has_alpha:
            a = d[p + 3]
            self.pos = p + 4
        else:
            a = 0xFF
            self.pos = p + 3
        return Color(r, g, b, a)

    def read_varint(self, optimize_positive: bool) -> int:
        result = 0
        shift = 0
        while True:
            b = self.read_byte()
            result |= (b & 0x7F) << shift
            if (b & 0x80) == 0:
                break
            shift += 7
        if not optimize_positive:
            result = (result >> 1) ^ -(result & 1)
        return result

    def read_string(self, strings: Optional[List[str]] = None) -> Optional[str]:
        length = self.read_varint(True)
        if length == 0:
            return None
        if length == 1:
            return ""
        length -= 1
        raw = self._read(length)
        s = raw.decode("utf-8", errors="replace")
        if strings is not None:
            strings.append(s)
        return s

    def read_string_ref(self, strings: List[str]) -> Optional[str]:
        index = self.read_varint(True)
        if index == 0:
            return None
        index -= 1
        if index >= len(strings):
            strings.append(self.read_string())
        return strings[index]

    def skip(self, n: int) -> None:
        self.pos += n


# ==============================
# Helpers
# ==============================
def get_pool_string(offset: int, sk: SkeletonData) -> Optional[str]:
    string_pool = sk.stringPool
    if offset == 0xFFFFFFFF:
        return None
    if offset >= len(string_pool):
        return f'<OOB:{offset:#x}>'
    try:
        end = string_pool.index(b'\x00', offset)
    except ValueError:
        end = len(string_pool)
    return string_pool[offset:end].decode('utf-8', errors='replace')

def read_f32_array(r: SpineBinaryReader, n: int) -> List[float]:
    if n <= 0:
        return []
    vals = struct.unpack_from(f"<{n}f", r.data, r.pos)
    r.pos += n * 4
    return list(vals)

def read_u8_array(r: SpineBinaryReader, n: int) -> List[int]:
    if n <= 0:
        return []
    vals = struct.unpack_from(f"<{n}B", r.data, r.pos)
    r.pos += n
    return list(vals)

def read_i16_array(r: SpineBinaryReader, n: int) -> List[int]:
    if n <= 0:
        return []
    vals = struct.unpack_from(f"<{n}h", r.data, r.pos)
    r.pos += n * 2
    return list(vals)

def read_u16_array(r: SpineBinaryReader, n: int) -> List[int]:
    if n <= 0:
        return []
    vals = struct.unpack_from(f"<{n}H", r.data, r.pos)
    r.pos += n * 2
    return list(vals)

def read_i32_array(r: SpineBinaryReader, n: int) -> List[int]:
    if n <= 0:
        return []
    vals = struct.unpack_from(f"<{n}i", r.data, r.pos)
    r.pos += n * 4
    return list(vals)

def read_u32_array(r: SpineBinaryReader, n: int) -> List[int]:
    if n <= 0:
        return []
    vals = struct.unpack_from(f"<{n}I", r.data, r.pos)
    r.pos += n * 4
    return list(vals)

def can_merge_weighted_vertices(vertices: List[float], bones: List[int]) -> bool:
    bpos = 0
    required = 0
    while bpos < len(bones):
        bc = bones[bpos]; bpos += 1
        if bc <= 0 or bpos + bc > len(bones):
            return False
        bpos += bc
        required += bc * 3
    return required == len(vertices)

def merge_weighted_vertices(vertices: List[float], bones: List[int]) -> List[float]:
    if not can_merge_weighted_vertices(vertices, bones):
        raise ValueError("Cannot merge weighted vertices")
    merged: List[float] = []
    bpos = 0; vpos = 0
    while bpos < len(bones):
        bc = bones[bpos]; bpos += 1
        merged.append(float(bc))
        for _ in range(bc):
            bi = bones[bpos]; bpos += 1
            x, y, w = vertices[vpos], vertices[vpos + 1], vertices[vpos + 2]
            vpos += 3
            merged.extend([float(bi), x, y, w])
    return merged


# ==============================
# Decompression & preprocessing
# ==============================
def lz4_decompress(reader: SpineBinaryReader) -> None:
    uncompressed_size = reader.read_u32()
    compressed_size = reader.read_u32()
    compressed_data = reader.read_bytes(compressed_size)
    decompressed = lz4.block.decompress(compressed_data, uncompressed_size=uncompressed_size)
    reader.reset_data(decompressed)

def custom_data_preprocess(reader: SpineBinaryReader, skeleton: SkeletonData) -> None:
    lz4_decompress(reader)
    data_size = reader.read_u32()
    string_pool_size = reader.read_u32()
    data_start_pos = reader.pos

    magic = reader.read_bytes(4)

    if magic == b"scsp":
        version = reader.read_u32()
        reader.reset_pos(data_start_pos)
        spine_data = reader.read_bytes(data_size)
        string_pool = reader.read_bytes(string_pool_size)
        reader.reset_data(spine_data)
        skeleton.scspVersion = ScspVersion.V3 if version > 2 else ScspVersion.V2
    else:
        # V2: no magic header — data section starts directly with spine data
        reader.reset_pos(data_start_pos)
        spine_data = reader.read_bytes(data_size)
        string_pool = reader.read_bytes(string_pool_size)
        reader.reset_data(spine_data)
        skeleton.scspVersion = ScspVersion.V2

    skeleton.stringPool = string_pool
