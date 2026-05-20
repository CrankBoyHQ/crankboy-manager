"""Playdate PDI image encode/decode for crankboy-manager.

Reference: https://github.com/cranksters/playdate-reverse-engineering/blob/main/formats/pdi.md
Matches CrankBoy's own conversion path (src/scenes/image_conversion_scene.c
+ src/pdi.h) including the Floyd-Steinberg dither with the parabolic
brightness-curve fit used for cover art.

Public surface (no external deps required):

    decode_pdi(data) -> PDIImage
        Parse a .pdi file. Returns a small dataclass exposing the
        raw 1-bit planes plus geometry. Use `pdi_to_pil` for a PIL image.

    encode_pdi(width, height, white_bits, opaque_bits=None, *,
               compress=False) -> bytes
        Build a .pdi from already-packed 1-bit planes.

    dither(rgba, in_w, in_h, out_w, out_h, *,
           brightness_compensation=0.95) -> bytes (1-bit packed)
        Mirrors CrankBoy's errdiff_dither.

PIL-based convenience wrappers (import lazily, raise if PIL missing):

    pil_to_pdi(img, *, max_width=None, max_height=None,
               compress=False, alpha_threshold=32) -> bytes
    pdi_to_pil(data) -> PIL.Image
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Optional


# ---- format constants ------------------------------------------------------

PDI_MAGIC = b"Playdate IMG"
PDI_FLAG_COMPRESSED = 0x80000000
PDI_CELL_FLAG_TRANSPARENCY = 3  # cell.flags carries this when an opaque
                                # plane follows the white plane.

# CrankBoy's grayscale weights (src/scenes/image_conversion_scene.c:21).
_WEIGHT_R = 312
_WEIGHT_G = 591
_WEIGHT_B = 126
_WEIGHT_DIVISOR = 256 * 1024

# Fixed-point arithmetic for the dither (FW_BITS=10).
_FW_BITS = 10
_FW_ONE = 1 << _FW_BITS
_FW_HALF = _FW_ONE >> 1
_GRAYDIV = _WEIGHT_DIVISOR // _FW_ONE


def _stride_for(width: int) -> int:
    """Bytes per row in the 1-bit plane, rounded up to a multiple of 4."""
    return ((width + 31) // 32) * 4


# ---- decoded representation ------------------------------------------------

@dataclass
class PDIImage:
    width: int
    height: int
    stride: int
    clip_left: int
    clip_right: int
    clip_top: int
    clip_bottom: int
    has_transparency: bool
    white: bytes        # 1-bit packed; bit set = white pixel
    opaque: Optional[bytes] = None  # 1-bit packed; bit set = opaque


# ---- decode ----------------------------------------------------------------

def decode_pdi(data: bytes) -> PDIImage:
    """Parse a .pdi file.

    Layout (per reverse-engineering doc + src/pdi.h):

        PDIHeader { char magic[12]; uint32_t flags; }
        if flags & PDI_FLAG_COMPRESSED:
            PDIMetadata { uint32_t size, width, height, reserved; }
            followed by a zlib stream of: PDICell + white[] + opaque[]?
        else:
            PDICell + white[] + opaque[]?

        PDICell {
            uint16_t clip_width, clip_height, stride;
            uint16_t clip_left, clip_right, clip_top, clip_bottom;
            uint16_t flags;
        }
    """
    if len(data) < 16:
        raise ValueError("PDI buffer too small for header")
    magic, flags = struct.unpack_from("<12sI", data, 0)
    if magic != PDI_MAGIC:
        raise ValueError(f"not a PDI file (magic={magic!r})")

    if flags & PDI_FLAG_COMPRESSED:
        # PDIMetadata sits between the header and the zlib payload.
        meta = struct.unpack_from("<IIII", data, 16)
        size = meta[0]
        compressed = data[16 + 16:]
        try:
            payload = zlib.decompress(compressed)
        except zlib.error as e:
            raise ValueError(f"failed to decompress PDI: {e}") from e
        if size and size != len(payload):
            # informational; some encoders set this loosely
            pass
    else:
        payload = data[16:]

    # PDICell
    (
        clip_w, clip_h, stride,
        clip_left, clip_right, clip_top, clip_bottom,
        cell_flags,
    ) = struct.unpack_from("<8H", payload, 0)
    off = 16  # sizeof(PDICell)

    plane_size = stride * clip_h
    if off + plane_size > len(payload):
        raise ValueError("PDI payload truncated (white plane)")
    white = bytes(payload[off:off + plane_size])
    off += plane_size

    has_t = bool(cell_flags & PDI_CELL_FLAG_TRANSPARENCY)
    opaque: Optional[bytes] = None
    if has_t:
        if off + plane_size > len(payload):
            raise ValueError("PDI payload truncated (opaque plane)")
        opaque = bytes(payload[off:off + plane_size])

    return PDIImage(
        width=clip_w,
        height=clip_h,
        stride=stride,
        clip_left=clip_left,
        clip_right=clip_right,
        clip_top=clip_top,
        clip_bottom=clip_bottom,
        has_transparency=has_t,
        white=white,
        opaque=opaque,
    )


# ---- encode (raw planes) ---------------------------------------------------

def encode_pdi(
    width: int,
    height: int,
    white_bits: bytes,
    opaque_bits: Optional[bytes] = None,
    *,
    compress: bool = False,
    stride: Optional[int] = None,
    clip_left: int = 0,
    clip_right: int = 0,
    clip_top: int = 0,
    clip_bottom: int = 0,
) -> bytes:
    """Build a .pdi file from already-packed 1-bit planes.

    `white_bits` and `opaque_bits` are MSB-first packed, with `stride`
    bytes per row. `stride` defaults to `((width + 31) // 32) * 4`
    (what CrankBoy and the SDK's own encoder produce), but some real
    PDIs in the wild use a tighter `(width + 7) // 8`; pass `stride`
    explicitly to round-trip those.

    The opaque plane is required only if the image has transparency.
    """
    if stride is None:
        stride = _stride_for(width)
    expected = stride * height
    if len(white_bits) != expected:
        raise ValueError(
            f"white plane size mismatch: got {len(white_bits)}, "
            f"expected {expected} (stride={stride}, height={height})"
        )
    has_t = opaque_bits is not None
    if has_t and len(opaque_bits) != expected:
        raise ValueError(
            f"opaque plane size mismatch: got {len(opaque_bits)}, "
            f"expected {expected}"
        )

    cell = struct.pack(
        "<8H",
        width, height, stride,
        clip_left, clip_right, clip_top, clip_bottom,
        PDI_CELL_FLAG_TRANSPARENCY if has_t else 0,
    )
    body = cell + white_bits + (opaque_bits or b"")

    if compress:
        compressed = zlib.compress(body)
        header = struct.pack("<12sI", PDI_MAGIC, PDI_FLAG_COMPRESSED)
        meta = struct.pack(
            "<IIII",
            len(body),  # uncompressed size
            width,
            height,
            0,           # reserved
        )
        return header + meta + compressed
    else:
        header = struct.pack("<12sI", PDI_MAGIC, 0)
        return header + body


# ---- dither (matches src/scenes/image_conversion_scene.c) ------------------

def _rgba_to_gray(r: int, g: int, b: int) -> int:
    return (r * _WEIGHT_R + g * _WEIGHT_G + b * _WEIGHT_B) // _GRAYDIV


def _get_image_statistics(rgba: memoryview, in_w: int, in_h: int):
    darkest = _FW_ONE
    brightest = 0
    total = 0
    n = in_w * in_h
    for i in range(n):
        off = i * 4
        gray = _rgba_to_gray(rgba[off], rgba[off + 1], rgba[off + 2])
        if gray < darkest:
            darkest = gray
        if gray > brightest:
            brightest = gray
        total += gray
    return darkest, brightest, total // n


def dither(
    rgba: bytes,
    in_w: int,
    in_h: int,
    out_w: int,
    out_h: int,
    *,
    brightness_compensation: float = 0.95,
) -> bytes:
    """Floyd-Steinberg dither matching CrankBoy's errdiff_dither.

    Args:
        rgba: input image bytes, 4 bytes per pixel (R,G,B,A), row-major.
        in_w, in_h: input dimensions.
        out_w, out_h: output dimensions. Source is sampled at
            `scale = max(in_w/out_w, in_h/out_h)` (CrankBoy clips by
            cropping the longer axis; if the caller wants the same crop
            they should pre-size out_w / out_h accordingly).
        brightness_compensation: 0..1 blend factor that pulls the
            adaptive brightness curve back toward a neutral one.
            CrankBoy uses 0.95.

    Returns 1-bit packed bytes, MSB-first, with stride
    `((out_w + 31) // 32) * 4`. Bit set = white pixel.
    """
    if len(rgba) < in_w * in_h * 4:
        raise ValueError("rgba buffer too short for given dimensions")
    if out_w <= 0 or out_h <= 0:
        raise ValueError("out_w and out_h must be positive")

    scale = max(in_w / out_w, in_h / out_h)

    lo, hi, avg = _get_image_statistics(memoryview(rgba), in_w, in_h)

    # Clamp the percentile statistics the same way C does.
    lo = min(lo, int(_FW_ONE * 0.05))
    hi = max(lo, int(_FW_ONE * 0.95))
    avg = max(avg, int(_FW_ONE * 0.2))
    avg = min(avg, int(_FW_ONE * 0.8))

    bc = brightness_compensation
    lo_f = bc * lo
    hi_f = bc * hi + (1 - bc) * _FW_ONE
    avg_f = bc * avg + (1 - bc) * _FW_ONE / 2

    l = lo_f / _FW_ONE
    h = hi_f / _FW_ONE
    v = avg_f / _FW_ONE

    # parabola through (l,0)(v,1)(h,0)
    dva = 1.0 / ((v - l) * (v - h))
    va = (l * h) * dva
    vb = (-l - h) * dva
    vc = 1.0 * dva
    # parabola through (l,0)(v,0)(h,1)
    dha = 1.0 / ((h - l) * (h - v))
    ha = (l * v) * dha
    hb = (-l - v) * dha
    hc = 1.0 * dha
    # blended (l,0)(v,0.5)(h,1)
    a = va * 0.5 + ha
    b = vb * 0.5 + hb
    c = vc * 0.5 + hc

    # Floyd-Steinberg, matching the C matrix:
    #   . . 7
    #   3 5 1
    # divisor 16.
    out_stride = _stride_for(out_w)
    out = bytearray(out_stride * out_h)

    # Two error rows, rotated each scanline.
    err0 = [0] * out_w
    err1 = [0] * out_w

    rgba_mv = memoryview(rgba)

    for y in range(out_h):
        # Pull current row's accumulated error; the row we're about to
        # write into is err1 (will diffuse to err0=next row).
        cur, nxt = err0, err1
        # CrankBoy resets the just-consumed row before diffusing into
        # next-row slots ("err_row_idx[0]"), so do the same.
        for j in range(out_w):
            cur[j] = cur[j]  # no-op: keep current row's accumulated error
        # The C code resets the row it just rotated INTO, not OUT of;
        # functionally that matches treating `cur` as the active row
        # whose value we consume and clear, while `nxt` collects errors
        # for the following scanline.

        iy_base = min(int(y * scale), in_h - 1)

        for x in range(out_w):
            ix = min(int(x * scale), in_w - 1)
            src = (iy_base * in_w + ix) * 4
            g = _rgba_to_gray(rgba_mv[src], rgba_mv[src + 1], rgba_mv[src + 2])

            # Apply brightness curve (float form, matching C's USE_FW... else branch).
            fg = g / _FW_ONE
            g = int(_FW_ONE * (a + fg * b + fg * c * fg))
            if g < 0:
                g = 0
            if g > _FW_ONE:
                g = _FW_ONE

            e = cur[x] // 16  # divisor=16
            if g + e > _FW_HALF:
                ediff = (g + e) - _FW_ONE
                xb = x >> 3
                out[out_stride * y + xb] |= (1 << (7 - (x & 7)))
            else:
                ediff = g + e

            # Diffuse error (Floyd-Steinberg):
            #   right (this row):    7/16 -> cur[x+1]
            #   down-left:           3/16 -> nxt[x-1]
            #   down:                5/16 -> nxt[x]
            #   down-right:          1/16 -> nxt[x+1]
            if x + 1 < out_w:
                cur[x + 1] += 7 * ediff
            if x - 1 >= 0:
                nxt[x - 1] += 3 * ediff
            nxt[x] += 5 * ediff
            if x + 1 < out_w:
                nxt[x + 1] += 1 * ediff

        # Swap rows for next scanline; the row we just consumed (cur)
        # is now the "next row" buffer with stale data, so clear it.
        for j in range(out_w):
            cur[j] = 0
        err0, err1 = err1, err0  # err0 = next row's accumulated errors

    return bytes(out)


# ---- PIL convenience wrappers ---------------------------------------------

def _require_pil():
    try:
        from PIL import Image  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "Pillow is required for pdi.pil_to_pdi / pdi.pdi_to_pil. "
            "Install with `pip install Pillow`."
        ) from e


def pil_to_pdi(
    img,
    *,
    max_width: Optional[int] = None,
    max_height: Optional[int] = None,
    compress: bool = False,
    alpha_threshold: int = 32,
) -> bytes:
    """Encode a PIL image to PDI using CrankBoy's dither.

    Behavior mirrors `png_to_pdi`:

    - If `max_width` / `max_height` are set and the image is larger,
      the output is scaled (uniform `scale = max(w/maxw, h/maxh)`) and
      cropped on the longer axis to fit.
    - The white plane is produced by Floyd-Steinberg error diffusion
      with the parabolic brightness fit and `brightness_compensation=0.95`.
    - An opaque (alpha) plane is emitted only when the input has alpha
      and at least one pixel falls below `alpha_threshold`.
    """
    _require_pil()
    from PIL import Image

    # Normalize to RGBA so the dither only sees one layout.
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    in_w, in_h = img.size

    # Compute output geometry the same way the C code does.
    out_w, out_h = in_w, in_h
    wscale, hscale = 1.0, 1.0
    if max_width is not None and max_width < in_w:
        wscale = in_w / max_width
        out_w = max_width
    if max_height is not None and max_height < in_h:
        hscale = in_h / max_height
        out_h = max_height
    scale = max(wscale, hscale, 1.0)

    if max_width is not None and max_height is not None:
        # Crop on the longer axis -- match the C "+0.75" rounding rule.
        if in_w / scale + 0.75 < max_width - 1:
            out_w = int(in_w / scale + 0.75)
        elif in_h / scale + 0.75 < max_height - 1:
            out_h = int(in_h / scale + 0.75)

    if out_w <= 0 or out_h <= 0:
        raise ValueError("requested output dimensions resolve to zero")

    rgba = img.tobytes()
    white = dither(rgba, in_w, in_h, out_w, out_h)

    # Optional opaque plane.
    has_alpha = any(rgba[i] < alpha_threshold for i in range(3, len(rgba), 4))
    opaque = None
    if has_alpha:
        opaque = _pack_alpha_plane(
            rgba, in_w, in_h, out_w, out_h, scale, alpha_threshold
        )

    return encode_pdi(out_w, out_h, white, opaque, compress=compress)


def _pack_alpha_plane(
    rgba: bytes,
    in_w: int,
    in_h: int,
    out_w: int,
    out_h: int,
    scale: float,
    alpha_threshold: int,
) -> bytes:
    """Threshold the alpha channel and pack to a 1-bit MSB-first plane
    matching `_stride_for(out_w)`. Bit set = opaque (matches CrankBoy).
    """
    stride = _stride_for(out_w)
    out = bytearray(stride * out_h)
    rgba_mv = memoryview(rgba)
    for y in range(out_h):
        iy = min(int(y * scale), in_h - 1)
        for x in range(out_w):
            ix = min(int(x * scale), in_w - 1)
            a = rgba_mv[(iy * in_w + ix) * 4 + 3]
            if a > alpha_threshold:
                out[stride * y + (x >> 3)] |= (1 << (7 - (x & 7)))
    return bytes(out)


def pdi_to_pil(data: bytes):
    """Decode a PDI to a PIL image.

    Returns mode "1" for a plain image, or mode "LA" (luminance +
    alpha) when the PDI carries an opaque plane.
    """
    _require_pil()
    from PIL import Image

    img = decode_pdi(data)

    def unpack(plane: bytes) -> bytes:
        """Expand a 1-bit MSB-first plane (with the .pdi row stride) to
        an 8-bit-per-pixel byte buffer of size `width*height`.
        """
        out = bytearray(img.width * img.height)
        stride = img.stride
        for y in range(img.height):
            row_off = y * stride
            for x in range(img.width):
                bit = (plane[row_off + (x >> 3)] >> (7 - (x & 7))) & 1
                out[y * img.width + x] = 255 if bit else 0
        return bytes(out)

    white = unpack(img.white)
    if img.opaque is None:
        return Image.frombytes("L", (img.width, img.height), white).convert("1")
    alpha = unpack(img.opaque)
    # Interleave into LA: (L, A) per pixel.
    la = bytearray(img.width * img.height * 2)
    la[0::2] = white
    la[1::2] = alpha
    return Image.frombytes("LA", (img.width, img.height), bytes(la))
