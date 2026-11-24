# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from fsq_agent.models import SampleColorParams

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_PNG_BYTES = 33_554_432
_MAX_CHUNKS = 65_536
_MAX_PIXELS = 33_554_432
_MAX_ROW_BYTES = 262_144
_MAX_SCANLINE_BYTES = 134_217_728
_MAX_SAMPLE_PIXELS = 1_048_576
_MAX_COORDINATE = 2_147_483_647
_APNG_CHUNKS = frozenset({b"acTL", b"fcTL", b"fdAT"})
_KNOWN_CRITICAL = frozenset({b"IHDR", b"PLTE", b"IDAT", b"IEND"})
_LEGAL_PNG_DEPTHS: dict[int, tuple[int, ...]] = {
    0: (1, 2, 4, 8, 16),
    2: (8, 16),
    3: (1, 2, 4, 8),
    4: (8, 16),
    6: (8, 16),
}
_PNG_CHANNELS: dict[int, int] = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class LocalColorSampleError(RuntimeError):
    def __init__(self, code: str, message: str, *, request_state: str = "not_sent") -> None:
        super().__init__(message)
        self.code = code
        self.request_state = request_state


def _sample_color(device: Any, params: SampleColorParams, *, control: Any) -> dict[str, object]:
    from appium_connect_agent.automation import ImageRegion

    point, region = params.point, params.region
    return asdict(
        device.sample_color(
            point.x if point is not None else None,
            point.y if point is not None else None,
            region=ImageRegion(**region.model_dump()) if region is not None else None,
            coordinate_space=params.coordinate_space,
            control=control,
        )
    )


def _chunk_type_valid(chunk_type: bytes) -> bool:
    return len(chunk_type) == 4 and all(65 <= b <= 90 or 97 <= b <= 122 for b in chunk_type) and bool(chunk_type[2] & 0x20) is False


def _png_keyword(data: bytes) -> str:
    if not 1 <= len(data) <= 79 or data[0] == 32 or data[-1] == 32 or b"  " in data:
        raise LocalColorSampleError("command_failed", "Invalid PNG keyword.")
    if any(not (32 <= value <= 126 or 161 <= value <= 255) for value in data):
        raise LocalColorSampleError("command_failed", "Invalid PNG keyword character.")
    return data.decode("latin-1")


def _row_filter(row: bytearray, previous: bytearray, filter_type: int, bpp: int) -> None:
    if filter_type == 0:
        return
    if filter_type == 1:
        for idx in range(bpp, len(row)):
            row[idx] = (row[idx] + row[idx - bpp]) & 0xFF
        return
    if filter_type == 2:
        for idx in range(len(row)):
            row[idx] = (row[idx] + previous[idx]) & 0xFF
        return
    if filter_type == 3:
        for idx in range(len(row)):
            left = row[idx - bpp] if idx >= bpp else 0
            row[idx] = (row[idx] + ((left + previous[idx]) >> 1)) & 0xFF
        return
    if filter_type == 4:
        for idx in range(len(row)):
            a = row[idx - bpp] if idx >= bpp else 0
            b = previous[idx]
            c = previous[idx - bpp] if idx >= bpp else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            predictor = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            row[idx] = (row[idx] + predictor) & 0xFF
        return
    raise LocalColorSampleError("command_failed", "Invalid PNG scanline filter.")


def _packed_sample(row: bytearray, x: int, bit_depth: int) -> int:
    bit_offset = x * bit_depth
    shift = 8 - bit_depth - (bit_offset % 8)
    return (row[bit_offset // 8] >> shift) & ((1 << bit_depth) - 1)


def _quantize_8bit(value: int, maximum: int) -> int:
    return (value * 255 + maximum // 2) // maximum


def _decode_pixel(
    *,
    row: bytearray,
    x: int,
    bit_depth: int,
    color_type: int,
    palette: tuple[tuple[int, int, int], ...] | None,
    transparency: bytes | None,
) -> tuple[tuple[int, int, int], int, int]:
    if color_type == 3:
        if palette is None:
            raise LocalColorSampleError("command_failed", "Indexed PNG is missing PLTE.")
        idx = row[x] if bit_depth == 8 else _packed_sample(row, x, bit_depth)
        if idx >= len(palette):
            raise LocalColorSampleError("command_failed", "Indexed PNG pixel exceeds palette length.")
        alpha = transparency[idx] if transparency is not None and idx < len(transparency) else 255
        return palette[idx], alpha, 255

    maximum = (1 << bit_depth) - 1
    channels = _PNG_CHANNELS[color_type]
    if bit_depth < 8:
        gray = _packed_sample(row, x, bit_depth)
        t_gray = struct.unpack(">H", transparency)[0] if transparency is not None else -1
        return (gray, gray, gray), (0 if gray == t_gray else maximum), maximum

    step = 2 if bit_depth == 16 else 1
    base = x * channels * step
    values = [struct.unpack_from(">H", row, base + i * 2)[0] if bit_depth == 16 else row[base + i] for i in range(channels)]
    if color_type == 0:
        t_gray = struct.unpack(">H", transparency)[0] if transparency is not None else -1
        return (values[0], values[0], values[0]), (0 if values[0] == t_gray else maximum), maximum
    if color_type == 4:
        return (values[0], values[0], values[0]), values[1], maximum
    if color_type == 2:
        rgb = (values[0], values[1], values[2])
        t_rgb = struct.unpack(">HHH", transparency) if transparency is not None else None
        return rgb, (0 if t_rgb is not None and rgb == t_rgb else maximum), maximum
    return (values[0], values[1], values[2]), values[3], maximum


def _parse_single_png(png_bytes: bytes, checkpoint: Callable[[], None]) -> dict[str, Any]:
    if not isinstance(png_bytes, bytes):
        raise LocalColorSampleError("command_failed", "PNG payload must be bytes.")
    total = len(png_bytes)
    if not (58 <= total <= _MAX_PNG_BYTES) or not png_bytes.startswith(_PNG_SIGNATURE):
        raise LocalColorSampleError("command_failed", "Invalid PNG header or size.")

    view = memoryview(png_bytes)
    offset = len(_PNG_SIGNATURE)
    chunk_count = 0
    width = height = bit_depth = color_type = row_bytes = 0
    saw_ihdr = saw_idat = saw_iend = idat_ended = False
    idat_bytes = 0
    palette: tuple[tuple[int, int, int], ...] | None = None
    transparency: bytes | None = None
    idat_parts: list[memoryview] = []
    saw_srgb = saw_iccp = saw_cicp = saw_gama = saw_chrm = False
    icc_profile_name: str | None = None

    while offset < total:
        checkpoint()
        chunk_count += 1
        if chunk_count > _MAX_CHUNKS or offset + 12 > total:
            raise LocalColorSampleError("command_failed", "PNG chunk limit or framing exceeded.")
        chunk_len = struct.unpack_from(">I", png_bytes, offset)[0]
        chunk_type = png_bytes[offset + 4 : offset + 8]
        if not _chunk_type_valid(chunk_type):
            raise LocalColorSampleError("command_failed", "Invalid PNG chunk type.")
        data_start = offset + 8
        data_end = data_start + chunk_len
        chunk_end = data_end + 4
        if chunk_end > total:
            raise LocalColorSampleError("command_failed", "Truncated PNG chunk.")
        expected_crc = struct.unpack_from(">I", png_bytes, data_end)[0]
        if (zlib.crc32(view[offset + 4 : data_end]) & 0xFFFFFFFF) != expected_crc:
            raise LocalColorSampleError("command_failed", "PNG chunk CRC mismatch.")
        data = b"" if chunk_type == b"IDAT" else png_bytes[data_start:data_end]

        if chunk_type in _APNG_CHUNKS:
            raise LocalColorSampleError("frame_not_single", "APNG is not a single-frame PNG.")
        if not (chunk_type[0] & 0x20) and chunk_type not in _KNOWN_CRITICAL:
            raise LocalColorSampleError("unsupported_png_format", "Unknown critical PNG chunk.")

        if chunk_count == 1:
            if chunk_type != b"IHDR" or chunk_len != 13:
                raise LocalColorSampleError("command_failed", "PNG must start with IHDR.")
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(">IIBBBBB", data)
            if not (1 <= width <= _MAX_COORDINATE and 1 <= height <= _MAX_COORDINATE):
                raise LocalColorSampleError("command_failed", "Invalid PNG dimensions.")
            if compression != 0 or filter_method != 0 or interlace != 0:
                raise LocalColorSampleError("unsupported_png_format", "Unsupported PNG compression, filter, or interlace mode.")
            if color_type not in _LEGAL_PNG_DEPTHS or bit_depth not in _LEGAL_PNG_DEPTHS[color_type]:
                raise LocalColorSampleError("unsupported_png_format", "Unsupported PNG color type or bit depth.")
            row_bytes = (width * _PNG_CHANNELS[color_type] * bit_depth + 7) // 8
            scanline_bytes = (row_bytes + 1) * height
            if width * height > _MAX_PIXELS or row_bytes > _MAX_ROW_BYTES or scanline_bytes > _MAX_SCANLINE_BYTES:
                raise LocalColorSampleError("command_failed", "PNG decoded size exceeds safety bounds.")
            saw_ihdr = True
        elif chunk_type == b"IHDR":
            raise LocalColorSampleError("command_failed", "Duplicate PNG IHDR.")
        elif chunk_type == b"PLTE":
            if saw_idat or palette is not None or color_type in (0, 4) or not (3 <= chunk_len <= 768) or chunk_len % 3:
                raise LocalColorSampleError("command_failed", "Invalid PNG PLTE chunk.")
            entries = tuple((data[i], data[i + 1], data[i + 2]) for i in range(0, chunk_len, 3))
            if color_type == 3 and len(entries) > (1 << bit_depth):
                raise LocalColorSampleError("command_failed", "PNG PLTE exceeds indexed bit depth.")
            palette = entries
        elif chunk_type == b"tRNS":
            if saw_idat or transparency is not None:
                raise LocalColorSampleError("command_failed", "Invalid PNG tRNS placement.")
            if color_type == 0 and (chunk_len != 2 or struct.unpack(">H", data)[0] >= (1 << bit_depth)):
                raise LocalColorSampleError("command_failed", "Invalid grayscale tRNS chunk.")
            if color_type == 2 and (chunk_len != 6 or any(v >= (1 << bit_depth) for v in struct.unpack(">HHH", data))):
                raise LocalColorSampleError("command_failed", "Invalid RGB tRNS chunk.")
            if color_type == 3 and (palette is None or not (1 <= chunk_len <= len(palette))):
                raise LocalColorSampleError("command_failed", "Invalid palette tRNS chunk.")
            if color_type in (4, 6):
                raise LocalColorSampleError("command_failed", "Alpha PNG cannot contain tRNS.")
            transparency = data
        elif chunk_type == b"sRGB":
            if saw_idat or palette is not None or saw_srgb or saw_iccp or chunk_len != 1 or data[0] > 3:
                raise LocalColorSampleError("command_failed", "Invalid sRGB chunk.")
            saw_srgb = True
        elif chunk_type == b"iCCP":
            if saw_idat or palette is not None or saw_iccp or saw_srgb:
                raise LocalColorSampleError("command_failed", "Invalid iCCP chunk.")
            sep = data.find(b"\0")
            if sep < 0 or sep + 2 >= len(data) or data[sep + 1] != 0:
                raise LocalColorSampleError("command_failed", "Invalid iCCP profile header.")
            icc_profile_name = _png_keyword(data[:sep])
            saw_iccp = True
        elif chunk_type == b"cICP":
            if saw_idat or palette is not None or saw_cicp or chunk_len != 4 or data[3] not in (0, 1):
                raise LocalColorSampleError("command_failed", "Invalid cICP chunk.")
            saw_cicp = True
        elif chunk_type == b"gAMA":
            if saw_idat or palette is not None or saw_gama or chunk_len != 4 or struct.unpack(">I", data)[0] == 0:
                raise LocalColorSampleError("command_failed", "Invalid gAMA chunk.")
            saw_gama = True
        elif chunk_type == b"cHRM":
            if saw_idat or palette is not None or saw_chrm or chunk_len != 32:
                raise LocalColorSampleError("command_failed", "Invalid cHRM chunk.")
            saw_chrm = True
        elif chunk_type == b"IDAT":
            if idat_ended or (color_type == 3 and palette is None):
                raise LocalColorSampleError("command_failed", "Invalid PNG IDAT sequence.")
            saw_idat = True
            idat_bytes += chunk_len
            idat_parts.append(view[data_start:data_end])
        elif chunk_type == b"IEND":
            if chunk_len != 0 or not saw_idat or idat_bytes < 1 or chunk_end != total:
                raise LocalColorSampleError("command_failed", "Invalid PNG IEND.")
            saw_iend = True
            offset = chunk_end
            break
        elif saw_idat:
            idat_ended = True
        offset = chunk_end

    if not saw_ihdr or not saw_iend or offset != total:
        raise LocalColorSampleError("command_failed", "Incomplete PNG container.")

    color_space = "cicp" if saw_cicp else "embedded_icc" if saw_iccp else "srgb" if saw_srgb else "untagged"
    return {
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
        "row_bytes": row_bytes,
        "scanline_bytes": (row_bytes + 1) * height,
        "idat_parts": tuple(idat_parts),
        "palette": palette,
        "transparency": transparency,
        "color_space": color_space,
        "icc_profile_name": icc_profile_name,
        "png_sha256": hashlib.sha256(png_bytes).hexdigest(),
    }


def _sample_local_png_color(
    png_bytes: bytes,
    params: SampleColorParams,
    *,
    window_rect: tuple[int, int, int, int] | None = None,
    pointer_size: tuple[int, int] | None = None,
    cancellation_check: Callable[[], None] | None = None,
) -> dict[str, object]:
    def checkpoint() -> None:
        if cancellation_check is not None:
            cancellation_check()

    checkpoint()
    frame = _parse_single_png(png_bytes, checkpoint)
    img_w: int = frame["width"]
    img_h: int = frame["height"]

    if params.point is not None:
        sel_x, sel_y, sel_w, sel_h = params.point.x, params.point.y, 1, 1
    elif params.region is not None:
        sel_x = params.region.left
        sel_y = params.region.top
        sel_w = params.region.right - params.region.left
        sel_h = params.region.bottom - params.region.top
    else:
        raise LocalColorSampleError("invalid_argument", "Provide point or region.")

    mapping_valid = False
    if window_rect is not None and pointer_size is not None:
        win_left, win_top, win_right, win_bottom = window_rect
        ptr_w, ptr_h = pointer_size
        if ptr_w > 0 and ptr_h > 0 and win_left >= 0 and win_top >= 0 and (win_right - win_left) == img_w and (win_bottom - win_top) == img_h and win_right <= ptr_w and win_bottom <= ptr_h:
            mapping_valid = True

    if params.coordinate_space == "pointer":
        if not mapping_valid or window_rect is None or pointer_size is None:
            raise LocalColorSampleError("pointer_mapping_unavailable", "Verified 1:1 window-to-screen pointer mapping is unavailable.", request_state="response_received")
        win_left, win_top, win_right, win_bottom = window_rect
        if sel_x < win_left or sel_y < win_top or (sel_x + sel_w) > win_right or (sel_y + sel_h) > win_bottom:
            raise LocalColorSampleError("invalid_argument", "Requested pointer selection falls outside the bound window.", request_state="response_received")
        img_x = sel_x - win_left
        img_y = sel_y - win_top
        ptr_bounds: dict[str, int] | None = {"x": sel_x, "y": sel_y, "width": sel_w, "height": sel_h}
        ptr_point: tuple[int, int] | None = (sel_x + sel_w // 2, sel_y + sel_h // 2)
        ptr_size_dict: dict[str, int] | None = {"width": pointer_size[0], "height": pointer_size[1]}
    else:
        if sel_x < 0 or sel_y < 0 or (sel_x + sel_w) > img_w or (sel_y + sel_h) > img_h:
            raise LocalColorSampleError("invalid_argument", "Requested image selection exceeds frame bounds.", request_state="response_received")
        img_x, img_y = sel_x, sel_y
        if mapping_valid and window_rect is not None and pointer_size is not None:
            px = window_rect[0] + img_x
            py = window_rect[1] + img_y
            ptr_bounds = {"x": px, "y": py, "width": sel_w, "height": sel_h}
            ptr_point = (px + sel_w // 2, py + sel_h // 2)
            ptr_size_dict = {"width": pointer_size[0], "height": pointer_size[1]}
        else:
            ptr_bounds = None
            ptr_point = None
            ptr_size_dict = None

    if sel_w * sel_h > _MAX_SAMPLE_PIXELS:
        raise LocalColorSampleError("command_failed", "Sample region exceeds pixel limit.")

    image_point = (img_x + sel_w // 2, img_y + sel_h // 2)
    sample_x, sample_y = image_point
    row_bytes: int = frame["row_bytes"]
    scanline_bytes: int = frame["scanline_bytes"]
    bit_depth: int = frame["bit_depth"]
    color_type: int = frame["color_type"]
    palette = frame["palette"]
    transparency = frame["transparency"]
    bpp = max(1, (_PNG_CHANNELS[color_type] * bit_depth + 7) // 8)

    previous = bytearray(row_bytes)
    pending = bytearray()
    decompressor = zlib.decompressobj()
    produced = 0
    row_index = 0
    sample_rgb: tuple[int, int, int] | None = None
    first_raw: tuple[tuple[int, int, int], int, int] | None = None
    uniform = True
    opaque = True
    min_alpha = 255
    min_rgb = [255, 255, 255]
    max_rgb = [0, 0, 0]
    block_size = 65_536

    def consume(output: bytes) -> None:
        nonlocal produced, row_index, previous, sample_rgb, first_raw, uniform, opaque, min_alpha
        produced += len(output)
        if produced > scanline_bytes:
            raise LocalColorSampleError("command_failed", "PNG DEFLATE output exceeds declared scanline size.")
        pending.extend(output)
        stride = row_bytes + 1
        offset = 0
        end = len(pending) - stride
        while offset <= end:
            checkpoint()
            filter_type = pending[offset]
            row = bytearray(memoryview(pending)[offset + 1 : offset + stride])
            offset += stride
            _row_filter(row, previous, filter_type, bpp)
            if img_y <= row_index < img_y + sel_h:
                for x_val in range(img_x, img_x + sel_w):
                    if (x_val & 4095) == 0:
                        checkpoint()
                    rgb_raw, alpha_raw, maximum = _decode_pixel(
                        row=row,
                        x=x_val,
                        bit_depth=bit_depth,
                        color_type=color_type,
                        palette=palette,
                        transparency=transparency,
                    )
                    identity = (rgb_raw, alpha_raw, maximum)
                    if first_raw is None:
                        first_raw = identity
                    elif identity != first_raw:
                        uniform = False
                    rgb8 = (
                        _quantize_8bit(rgb_raw[0], maximum),
                        _quantize_8bit(rgb_raw[1], maximum),
                        _quantize_8bit(rgb_raw[2], maximum),
                    )
                    alpha8 = _quantize_8bit(alpha_raw, maximum)
                    if alpha_raw != maximum:
                        opaque = False
                    min_alpha = min(min_alpha, alpha8)
                    for channel in range(3):
                        min_rgb[channel] = min(min_rgb[channel], rgb8[channel])
                        max_rgb[channel] = max(max_rgb[channel], rgb8[channel])
                    if x_val == sample_x and row_index == sample_y:
                        sample_rgb = rgb8
            previous = row
            row_index += 1
        if offset:
            del pending[:offset]

    try:
        for part in frame["idat_parts"]:
            for offset in range(0, len(part), block_size):
                chunk: bytes | memoryview = part[offset : offset + block_size]
                while True:
                    checkpoint()
                    output = decompressor.decompress(chunk, block_size)
                    if decompressor.unused_data:
                        raise LocalColorSampleError("command_failed", "Trailing PNG DEFLATE data.")
                    consume(output)
                    tail = decompressor.unconsumed_tail
                    if tail:
                        if not output and len(tail) == len(chunk):
                            raise LocalColorSampleError("command_failed", "PNG DEFLATE made no progress.")
                        chunk = tail
                    elif len(output) == block_size:
                        chunk = b""
                    else:
                        break
    except zlib.error as exc:
        raise LocalColorSampleError("command_failed", "Corrupt PNG DEFLATE stream.") from exc

    if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail or pending or produced != scanline_bytes or row_index != img_h or sample_rgb is None or first_raw is None:
        raise LocalColorSampleError("command_failed", "Incomplete PNG DEFLATE stream.")

    return {
        "rgb": sample_rgb,
        "hex": f"#{sample_rgb[0]:02X}{sample_rgb[1]:02X}{sample_rgb[2]:02X}",
        "opaque": opaque,
        "min_alpha": min_alpha,
        "uniform": uniform,
        "min_rgb": (min_rgb[0], min_rgb[1], min_rgb[2]),
        "max_rgb": (max_rgb[0], max_rgb[1], max_rgb[2]),
        "color_space": frame["color_space"],
        "icc_profile_name": frame["icc_profile_name"],
        "coordinate_space": params.coordinate_space,
        "pointer_point": ptr_point,
        "pointer_bounds": ptr_bounds,
        "image_point": image_point,
        "image_bounds": {"x": img_x, "y": img_y, "width": sel_w, "height": sel_h},
        "image_size": {"width": img_w, "height": img_h},
        "pointer_size": ptr_size_dict,
        "png_sha256": frame["png_sha256"],
    }
