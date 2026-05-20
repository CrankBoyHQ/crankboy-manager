"""Generate launcher list-icons for forwarder .pdx bundles.

The launcher icon ("icon.pdi" inside the .pdx/launcher/ folder) shows
when the Playdate is browsing the Game menu. We start from a 32x32
template (`src/assets/LauncherIcon.png`) and superimpose up to two
glyphs derived from the ROM's display title onto a fixed 23x13 area at
(5, 12).

Glyphs come from `src/assets/Glyphs13.png` -- 36 fixed-width 10x13
cells in the order [A..Z, 0..9]. The rightmost column of each cell is
intentionally blank so two glyphs render with a natural 1px gap when
placed adjacent.

Representative-letter selection rules (per spec):

  - Prefer letters from after the first " - " when the title contains
    one (so "Legend of Zelda - Link's Awakening" -> "LA", not "LZ").
  - Skip a leading article (The/A/An/La/Le/Der/Die/Das/...) before
    picking letters.
  - Substitute roman numerals II..IX with 2..9. Keep I, V, X as-is.
  - For each remaining word, contribute its first character. All-caps
    words still only contribute their first character (never the rest).
  - When the input collapses to a single non-all-caps word with at
    least two characters, take its first two letters instead of one,
    so we still hit the 2-letter target.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# Mirrors common_article_form's article list, plus singular "A"/"An"
# which CrankBoy's title rotator doesn't recognise (it only handles the
# trailing-article form) but which we want to drop for icon letters so
# "A Bug's Life" -> "BL" instead of "AB".
_ARTICLES = frozenset({
    "The", "A", "An",
    "Las", "Le", "La", "Los", "Les",
    "Der", "Die", "Das", "Un", "Une",
})

# II..IX become 2..9. I, V, X stay as their own letter (a single roman
# digit is treated as a one-character "word").
_ROMAN_SUB = {
    "II": "2",
    "III": "3",
    "IV": "4",
    "VI": "6",
    "VII": "7",
    "VIII": "8",
    "IX": "9",
}


def representative_letters(title: str) -> str:
    """Return up to two characters that represent `title`.

    See the module docstring for the rules. Returns an empty string for
    empty / unparseable input. The output is uppercase.
    """
    if not title:
        return ""

    # Roman numeral substitution helper. Match exact tokens only --
    # "Final Fantasy II" -> ["Final", "Fantasy", "2"]. Punctuation
    # around the token (e.g. trailing ":") would prevent the
    # substitution; we strip a small set of common trailers before
    # lookup.
    def _sub(w):
        bare = w.rstrip(":,.").upper()
        return _ROMAN_SUB.get(bare, w)

    # Prefer the side after the first " - " ... unless the side BEFORE
    # the hyphen already contains a number (after roman-numeral
    # substitution). That keeps numbered entries like
    # "Donkey Kong Country 2 - Diddy's Kong Quest" -> "D2" instead of
    # falling through to "DK".
    if " - " in title:
        head, tail = title.split(" - ", 1)
        head_words = [_sub(w) for w in head.split()]
        if any(w.isdigit() for w in head_words):
            title = head
        else:
            title = tail

    words = title.split()
    if not words:
        return ""

    # Strip a leading article only when there are more words to fall
    # back on; "The" alone is technically a title.
    if words[0] in _ARTICLES and len(words) > 1:
        words = words[1:]

    words = [_sub(w) for w in words]

    def _is_alnum_char(ch):
        return len(ch) == 1 and ch.isascii() and (ch.isalpha() or ch.isdigit())

    def _is_capitalized_leader(w):
        """A word's leading char qualifies as 'capitalized' (worth
        contributing a letter even when other words would otherwise be
        skipped). True for words whose first ASCII character is either
        an uppercase letter or a digit; lowercase first letters mean
        the word is a connector ('of', 'the', 'and', 'a') and gets
        skipped unless we'd otherwise come up short.
        """
        if not w:
            return False
        c = w[0]
        if not (c.isascii() and (c.isalpha() or c.isdigit())):
            return False
        return c.isdigit() or c.isupper()

    # If the last token is a single alphanumeric character (typical
    # sequel marker -- a digit from roman substitution, or one of the
    # kept "I"/"V"/"X"), prefer first-letter-of-first-CAPITALIZED-word
    # + that char. So "Bentham IV" -> "B4", "Final Fantasy VII" -> "F7",
    # "Mega Man X" -> "MX", "Return of Samus II" -> "R2" (skipping
    # the connector "of"). Falls back to the very first usable word
    # if no capitalized word exists.
    if len(words) >= 2 and _is_alnum_char(words[-1]):
        leading = None
        for w in words[:-1]:
            if _is_capitalized_leader(w):
                leading = w[0]
                break
        if leading is None:
            for w in words[:-1]:
                if w and _is_alnum_char(w[0].upper()):
                    leading = w[0]
                    break
        if leading:
            return (leading + words[-1]).upper()

    # Pass 1: prefer words whose first character is uppercase or a digit.
    # Skip connector words ("of", "and", "the", "a") that start with a
    # lowercase letter -- "Return of Samus" -> "RS", not "RO".
    letters = []
    for w in words:
        if _is_capitalized_leader(w):
            letters.append(w[0].upper())
            if len(letters) >= 2:
                break

    # Pass 2: if we still don't have two letters, fall back to any
    # remaining lowercase-led words (in order) to fill the slots --
    # e.g. an all-lowercase title still produces something.
    if len(letters) < 2:
        for w in words:
            if not w:
                continue
            ch = w[0]
            if not (ch.isascii() and ch.isalpha() and not ch.isupper()):
                continue
            letters.append(ch.upper())
            if len(letters) >= 2:
                break

    # Single non-all-caps word with room for a second letter -> double up.
    if len(letters) == 1 and len(words) == 1 and len(words[0]) > 1 and not words[0].isupper():
        second = words[0][1].upper()
        if _is_alnum_char(second):
            letters.append(second)

    return "".join(letters[:2])


# --- icon composition ------------------------------------------------------

# 32x32 launcher card. The fixed glyph area is 23 wide x 13 tall at (5, 12).
ICON_SIZE = (32, 32)
GLYPH_RECT = (5, 12, 23, 13)  # (x, y, w, h)

# Glyphs13.png layout: 36 cells of 10x13 in order [A..Z, 0..9].
_GLYPH_CELL_W = 10
_GLYPH_CELL_H = 13
_GLYPH_VISIBLE_W = 9       # rightmost column of each cell is intentionally blank
_GLYPH_ADJACENT_GAP = 1    # the blank column doubles as a natural 1px gap


def _assets_dir() -> Path:
    """Return the source asset directory (dev or PyInstaller bundle)."""
    import sys
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "src" / "assets"
    return Path(__file__).parent.parent / "assets"


def _glyph_index(ch: str) -> Optional[int]:
    """Map an A-Z / 0-9 character to its cell index in Glyphs13.png."""
    if not ch:
        return None
    c = ch.upper()
    if "A" <= c <= "Z":
        return ord(c) - ord("A")
    if "0" <= c <= "9":
        return 26 + (ord(c) - ord("0"))
    return None


def _load_pil(path):
    from PIL import Image
    img = Image.open(path)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return img


def compose_launcher_icon(letters: str, base_icon=None):
    """Return a 32x32 PIL RGBA image with up to two glyphs superimposed
    on the base launcher icon.

    `letters` is what `representative_letters` produces (0..2 chars).
    `base_icon` defaults to assets/LauncherIcon.png if not supplied;
    pass a PIL.Image to use a custom template.
    """
    from PIL import Image

    if base_icon is None:
        base_icon = _load_pil(_assets_dir() / "LauncherIcon.png")
    else:
        if base_icon.mode != "RGBA":
            base_icon = base_icon.convert("RGBA")
    # Force into the expected size if the caller supplied something else.
    if base_icon.size != ICON_SIZE:
        base_icon = base_icon.resize(ICON_SIZE, Image.LANCZOS)

    out = base_icon.copy()

    # Filter to the (up to 2) letters we have glyphs for.
    glyphs = []
    for ch in letters[:2]:
        idx = _glyph_index(ch)
        if idx is not None:
            glyphs.append(idx)
    if not glyphs:
        return out

    sheet = _load_pil(_assets_dir() / "Glyphs13.png")

    # Composite layout inside the glyph rect.
    rect_x, rect_y, rect_w, rect_h = GLYPH_RECT
    if len(glyphs) == 1:
        block_w = _GLYPH_VISIBLE_W
    else:
        # Two glyphs side-by-side, with a 1px gap from the first cell's
        # blank rightmost column.
        block_w = _GLYPH_VISIBLE_W + _GLYPH_ADJACENT_GAP + _GLYPH_VISIBLE_W
    x0 = rect_x + (rect_w - block_w) // 2
    y0 = rect_y + (rect_h - _GLYPH_CELL_H) // 2  # 0 since 13==13

    cur_x = x0
    for i, gidx in enumerate(glyphs):
        cell = sheet.crop((
            gidx * _GLYPH_CELL_W, 0,
            gidx * _GLYPH_CELL_W + _GLYPH_CELL_W, _GLYPH_CELL_H,
        ))
        out.alpha_composite(cell, dest=(cur_x, y0))
        # Advance: the cell is 10 wide, of which 9 are visible + 1 blank
        # gap, so adjacent placement is exactly cell width apart.
        cur_x += _GLYPH_CELL_W

    return out


def compose_launcher_icon_for_title(title: str, base_icon=None):
    """Convenience: compute representative letters and compose the icon."""
    letters = representative_letters(title)
    return compose_launcher_icon(letters, base_icon=base_icon)


# --- launcher card composition --------------------------------------------

# Standard Playdate launcher card is 350x155. The cover-art slot is up to
# 250x139 (centered, anchored to y=3); the spec gives x=106 for the common
# 139x139 case, which we reproduce with `(card_w - img_w + 1) // 2`.
CARD_SIZE = (350, 155)
CARD_IMAGE_MAX = (250, 139)
CARD_Y = 3

# Aspect ratio within 5% of square -> we treat the cover as a square and
# resize directly to 139x139 (slight distortion accepted by the spec).
_SQUARE_TOLERANCE = 0.05

# Libretro boxart source. Pinned to the commit the spec references so the
# URL is stable.
_LIBRETRO_GB_BASE = (
    "https://raw.githubusercontent.com/libretro-thumbnails/"
    "Nintendo_-_Game_Boy/"
    "d963e89e95c1fe48df9fdb88ccb60f7d1ffc68d3/"
    "Named_Boxarts/"
)
# CrankBoy cover fallback: PDI images on the manager's repo.
_CRANKBOY_COVERS_BASE = (
    "https://raw.githubusercontent.com/CrankBoyHQ/crankboy-covers/"
    "refs/heads/main/Combined_Boxarts/"
)
# Hand-curated bundle overrides. Each entry maps a title string to a
# directory under `bundles/` that contains card.png + icon.png. The
# whole thing is small enough to fetch every time without caching.
_CRANKBOY_BUNDLES_BASE = (
    "https://raw.githubusercontent.com/CrankBoyHQ/crankboy-bundles/"
    "refs/heads/main/"
)
_CRANKBOY_BUNDLES_MANIFEST = _CRANKBOY_BUNDLES_BASE + "manifest.json"
# Short network timeout — we don't want a build to hang on a slow CDN.
_COVER_FETCH_TIMEOUT_S = 5.0


def _http_fetch(url: str, log=None) -> Optional[bytes]:
    """GET `url`, returning bytes on 200 or None on any failure."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "crankboy-manager"})
        with urllib.request.urlopen(req, timeout=_COVER_FETCH_TIMEOUT_S) as resp:
            if resp.status != 200:
                if log:
                    log(f"  card: HTTP {resp.status} for {url}")
                return None
            return resp.read()
    except Exception as e:
        if log:
            log(f"  card: fetch failed: {e!r}")
        return None


def _normalise_for_match(s):
    """Lowercase + strip diacritics for tolerant title comparisons.
    e.g. "Pokémon Brown" and "Pokemon Brown" both normalise to
    "pokemon brown".
    """
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    ).lower().strip()


def fetch_bundle_override(rom_info, log=None):
    """Look up the ROM in the crankboy-bundles manifest. Returns a tuple
    `(card_image, icon_image)` of PIL Images when a match is found,
    else None. Each image is returned in its native RGBA form (350x155
    for the card, 32x32 for the icon) ready to drop straight into the
    forwarder .pdx.

    Matching: every key in the manifest's "Bundles" object is compared
    against the ROM's DB `short` and `long` titles after lowercasing
    and stripping diacritics, so "Pokemon Brown" matches "Pokémon
    Brown" and vice versa.
    """
    if not rom_info:
        return None
    candidates = []
    for k in ("short", "long"):
        v = rom_info.get(k)
        if v:
            candidates.append(_normalise_for_match(v))
    if not candidates:
        return None

    raw = _http_fetch(_CRANKBOY_BUNDLES_MANIFEST, log=log)
    if not raw:
        return None
    try:
        import json
        text = raw.decode("utf-8")
        manifest = json.loads(text)
    except Exception:
        # Be tolerant of trailing commas before } or ] -- a common
        # slip in hand-maintained manifests that Python's stdlib JSON
        # parser rejects.
        try:
            import json
            import re
            cleaned = re.sub(r",(\s*[}\]])", r"\1", text)
            manifest = json.loads(cleaned)
        except Exception as e:
            if log:
                log(f"  bundle: manifest parse failed: {e!r}")
            return None

    # Accept the (mis)spelt singular "Bundle" too, just in case.
    bundles = manifest.get("Bundles") or manifest.get("Bundle") or {}
    if not isinstance(bundles, dict):
        return None

    match_path = None
    for key, path in bundles.items():
        if not isinstance(key, str) or not isinstance(path, str):
            continue
        if _normalise_for_match(key) in candidates:
            match_path = path.strip().strip("/")
            if log:
                log(f"  bundle: '{key}' -> {match_path}")
            break
    if not match_path:
        if log:
            log("  bundle: no manifest match")
        return None

    from PIL import Image
    import io
    card_url = f"{_CRANKBOY_BUNDLES_BASE}{match_path}/card.png"
    icon_url = f"{_CRANKBOY_BUNDLES_BASE}{match_path}/icon.png"
    card_raw = _http_fetch(card_url, log=log)
    icon_raw = _http_fetch(icon_url, log=log)
    if not card_raw or not icon_raw:
        if log:
            log("  bundle: missing card.png or icon.png in bundle dir")
        return None
    try:
        card = Image.open(io.BytesIO(card_raw)).convert("RGBA")
        icon = Image.open(io.BytesIO(icon_raw)).convert("RGBA")
    except Exception as e:
        if log:
            log(f"  bundle: image decode failed: {e!r}")
        return None
    if log:
        log(
            f"  bundle: card {card.size[0]}x{card.size[1]}, "
            f"icon {icon.size[0]}x{icon.size[1]}"
        )
    return card, icon


def _fetch_libretro_boxart(long_name: str, log=None):
    """Download libretro's Game Boy boxart for `long_name` and return a PIL
    Image converted to grayscale ("L"), or None on failure.
    """
    if not long_name:
        return None
    from PIL import Image
    import io, urllib.parse
    url = _LIBRETRO_GB_BASE + urllib.parse.quote(long_name + ".png")
    if log:
        log(f"  card: libretro GET {url}")
    raw = _http_fetch(url, log=log)
    if not raw:
        return None
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        if log:
            log(f"  card: libretro hit ({img.size[0]}x{img.size[1]})")
        return img.convert("L")
    except Exception as e:
        if log:
            log(f"  card: libretro decode failed: {e!r}")
        return None


def _fetch_crankboy_cover(long_name: str, log=None):
    """Download CrankBoy's cover PDI for `long_name`, decode and apply a
    radius-2 Gaussian blur. Returns a PIL Image (mode "L") or None.
    """
    if not long_name:
        return None
    from PIL import Image, ImageFilter
    from src.core.database import encode_cover_filename
    from src.core.pdi import decode_pdi
    # `encode_cover_filename` already URL-encodes spaces etc; do NOT
    # re-quote here or we'd double-encode the percent signs.
    encoded = encode_cover_filename(long_name)
    url = f"{_CRANKBOY_COVERS_BASE}{encoded}.pdi"
    if log:
        log(f"  card: crankboy GET {url}")
    raw = _http_fetch(url, log=log)
    if not raw:
        return None
    try:
        pdi = decode_pdi(raw)
    except Exception as e:
        if log:
            log(f"  card: pdi decode failed: {e!r}")
        return None

    # Unpack white plane to an 8-bit "L" image. (Opaque plane is unused.)
    out = bytearray(pdi.width * pdi.height)
    stride = pdi.stride
    for y in range(pdi.height):
        row_off = y * stride
        for x in range(pdi.width):
            bit = (pdi.white[row_off + (x >> 3)] >> (7 - (x & 7))) & 1
            out[y * pdi.width + x] = 255 if bit else 0
    img = Image.frombytes("L", (pdi.width, pdi.height), bytes(out))
    if log:
        log(f"  card: crankboy hit ({img.size[0]}x{img.size[1]}), gaussian blur r=2")
    return img.filter(ImageFilter.GaussianBlur(radius=2))


def _fit_card_image(img):
    """Resize `img` (any aspect) to fit the launcher card cover slot.

    Per spec:
      - Aspect within 5% of 1.0  -> resize to 139x139 (square slot).
      - Otherwise               -> fit within 250x139 preserving aspect.
    Returns the resized PIL image (RGBA, mode preserved on input but
    output is RGBA for compositing).
    """
    from PIL import Image
    w, h = img.size
    if w <= 0 or h <= 0:
        return None
    aspect = w / h
    if abs(aspect - 1.0) <= _SQUARE_TOLERANCE:
        out = img.resize((139, 139), Image.LANCZOS)
    else:
        max_w, max_h = CARD_IMAGE_MAX
        scale = min(max_w / w, max_h / h)
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        out = img.resize((new_w, new_h), Image.LANCZOS)
    return out.convert("RGBA") if out.mode != "RGBA" else out


def _dither_pil(img):
    """Floyd-Steinberg dither an RGBA PIL Image. Returns a PIL "1" image
    of the same size, dithered through our `pdi.dither` (matches
    CrankBoy's brightness-curve fit).
    """
    from PIL import Image
    from src.core.pdi import dither, _stride_for  # noqa: F401
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    bits = dither(img.tobytes(), w, h, w, h)
    # Unpack 1-bit packed bits back into an "L" then "1" image for paste.
    stride = ((w + 31) // 32) * 4
    px = bytearray(w * h)
    for y in range(h):
        row_off = y * stride
        for x in range(w):
            bit = (bits[row_off + (x >> 3)] >> (7 - (x & 7))) & 1
            px[y * w + x] = 255 if bit else 0
    return Image.frombytes("L", (w, h), bytes(px)).convert("1")


# Characters we render in the text fallback (everything else is dropped,
# except space which is rendered as a glyph-cell-wide blank).
_FALLBACK_ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ")


def _wrap_fallback_text(title, max_chars):
    """Tokenize-and-wrap `title` into a list of lines that each fit within
    `max_chars` glyph cells. Strips disallowed characters; long words are
    hard-broken on the boundary.
    """
    # Normalise: uppercase + keep only allowed chars
    cleaned = []
    for ch in title.upper():
        if ch in _FALLBACK_ALLOWED:
            cleaned.append(ch)
        # else: drop
    text = "".join(cleaned)
    words = [w for w in text.split() if w]
    lines = []
    cur = ""
    for w in words:
        candidate = (cur + " " + w) if cur else w
        if len(candidate) <= max_chars:
            cur = candidate
            continue
        if cur:
            lines.append(cur)
            cur = ""
        # Word itself too long -> hard-break.
        while len(w) > max_chars:
            lines.append(w[:max_chars])
            w = w[max_chars:]
        cur = w
    if cur:
        lines.append(cur)
    return lines


def _compose_text_fallback(title, log=None):
    """Render the title (uppercased, A-Z/0-9 only, spaces preserved) as
    inverted glyph text inside a 139x139 panel. Returns an RGBA PIL image
    sized 139x139.
    """
    from PIL import Image
    panel = Image.new("RGBA", (139, 139), (0, 0, 0, 255))
    sheet = _load_pil(_assets_dir() / "Glyphs13.png")

    # 13 cells of 10 px per line.
    max_chars = 139 // _GLYPH_CELL_W
    lines = _wrap_fallback_text(title or "", max_chars)
    if log:
        log(f"  card: text fallback -> {len(lines)} line(s) {lines!r}")
    if not lines:
        return panel

    # Layout: stack lines vertically, 1px between, centered.
    inter_line_gap = 1
    line_h = _GLYPH_CELL_H
    total_h = len(lines) * line_h + (len(lines) - 1) * inter_line_gap
    y0 = max(0, (139 - total_h) // 2)

    white = Image.new("RGBA", (_GLYPH_CELL_W, _GLYPH_CELL_H), (255, 255, 255, 255))

    for li, line in enumerate(lines):
        # Use 10 px per cell; the cell's last column is the natural gap.
        # Total drawn width counts each glyph as 10 px; visually the
        # rightmost glyph's blank column hangs in the gap but that's fine.
        line_w = len(line) * _GLYPH_CELL_W - (1 if line else 0)
        x = max(0, (139 - line_w) // 2)
        y = y0 + li * (line_h + inter_line_gap)
        for ch in line:
            if ch == " ":
                x += _GLYPH_CELL_W
                continue
            idx = _glyph_index(ch)
            if idx is None:
                x += _GLYPH_CELL_W
                continue
            cell = sheet.crop((
                idx * _GLYPH_CELL_W, 0,
                idx * _GLYPH_CELL_W + _GLYPH_CELL_W, _GLYPH_CELL_H,
            ))
            mask = cell.split()[-1]  # alpha channel of the glyph
            # Paint white wherever the glyph is opaque; black background
            # already covers the rest.
            panel.paste(white, (x, y), mask=mask)
            x += _GLYPH_CELL_W

    return panel


def compose_preview_framebuffer(card_image):
    """Build the 12480-byte Playdate framebuffer used by the "Preview"
    button.

    The card_image (whatever the dialog currently shows -- user
    override, cached auto-build, or the bare LauncherCard.png) is
    centered on a 400x240 canvas whose background is alternating
    horizontal rows of black and white. The *bottom* row (y=239) is
    black per spec, so even-indexed rows are white and odd-indexed
    rows are black.

    Returns 12480 raw bytes, MSB-first, stride 52, 1 = white.
    """
    from PIL import Image, ImageDraw

    # 1) Build the 400x240 canvas with the alternating-row pattern.
    canvas = Image.new("1", (400, 240), 0)  # 0 = black
    draw = ImageDraw.Draw(canvas)
    for y in range(240):
        # y=239 (bottom) must be black; 239 % 2 == 1, so odd -> black,
        # even -> white. That keeps the pattern symmetric and lets the
        # invariant be expressed simply.
        if y % 2 == 0:
            draw.line([(0, y), (399, y)], fill=1)

    # 2) Paste the card centered, using its alpha as a paste mask so
    #    transparent pixels in the card reveal the alternating-row
    #    background underneath instead of clobbering it with white.
    if card_image is None:
        return _fb_pack(canvas)

    img = card_image
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    card_w, card_h = img.size

    # Convert the visible (RGB) channels to 1-bit. The card has already
    # been dithered upstream when it came out of `compose_launcher_card`,
    # so a hard threshold is correct here (don't dither again).
    rgb_1bit = img.convert("RGB").convert("L").point(
        lambda v: 1 if v >= 128 else 0, mode="1"
    )

    # Alpha channel thresholded to a binary mask. Anything below 128 is
    # treated as fully transparent and lets the background pattern show
    # through.
    mask = img.split()[-1].point(lambda v: 255 if v >= 128 else 0, mode="L")

    x = (400 - card_w) // 2
    y = (240 - card_h) // 2
    canvas.paste(rgb_1bit, (x, y), mask)

    return _fb_pack(canvas)


def _fb_pack(img_1bit):
    """Pack a 400x240 PIL "1"-mode image into 12000 bytes (stride 50).

    PIL's `tobytes` on a 400x240 "1" image emits exactly this: 50 bytes
    per row (400 bits MSB-first), 240 rows, no padding -- 12000 bytes
    total.

    Note this is the *wire* format for the firmware's `bitmap` serial
    command, which packs each row tightly. It is NOT the same as the
    in-memory framebuffer the SDK exposes via `playdate->graphics->
    getFrame()`, which uses a 52-byte stride (LCD_ROWSIZE) for hardware
    alignment.
    """
    raw = img_1bit.tobytes()
    expected = 50 * 240
    if len(raw) != expected:
        raise ValueError(
            f"unexpected packed size {len(raw)} (want {expected})"
        )
    return raw


def compose_launcher_card(
    *,
    title: str,
    rom_info: Optional[dict] = None,
    base_card=None,
    log=None,
):
    """Build the launcher card image for a forwarder.

    Args:
        title: the human-readable display title (same as pdxinfo's
            `name=`). Used for the text-fallback when no boxart is
            available.
        rom_info: optional dict from `database.lookup(crc32)`, providing
            the libretro `long` name. When omitted, we go straight to
            the text fallback.
        base_card: optional PIL.Image override of the 350x155 backing
            card. Defaults to `assets/LauncherCard.png`.
        log: optional callable for progress / diagnostic lines.

    Returns a 32-bit RGBA PIL image of size 350x155, ready to encode as
    PDI.
    """
    from PIL import Image

    if base_card is None:
        base_card = _load_pil(_assets_dir() / "LauncherCard.png")
    elif base_card.mode != "RGBA":
        base_card = base_card.convert("RGBA")
    if base_card.size != CARD_SIZE:
        base_card = base_card.resize(CARD_SIZE, Image.LANCZOS)

    # 1) Try libretro boxart -> grayscale.
    cover = None
    long_name = (rom_info or {}).get("long") if rom_info else None
    if long_name:
        cover = _fetch_libretro_boxart(long_name, log=log)
        if cover is None:
            cover = _fetch_crankboy_cover(long_name, log=log)

    if cover is not None:
        fitted = _fit_card_image(cover)
        if fitted is None:
            cover = None
        else:
            # Dither, paste centered (bias right) at y=3.
            dithered = _dither_pil(fitted)
            cw, ch = base_card.size
            iw, ih = dithered.size
            x = max(0, (cw - iw + 1) // 2)
            y = CARD_Y
            if log:
                log(f"  card: pasting cover at ({x}, {y}) size {iw}x{ih}")
            base_card.paste(dithered.convert("RGBA"), (x, y))
            return base_card

    # 2) No cover -> text fallback in the centered 139x139 area.
    if log:
        log("  card: no cover available, using text fallback")
    text_panel = _compose_text_fallback(title, log=log)
    cw, ch = base_card.size
    tw, th = text_panel.size
    x = max(0, (cw - tw + 1) // 2)
    y = max(0, (ch - th) // 2)
    base_card.paste(text_panel, (x, y))
    return base_card
