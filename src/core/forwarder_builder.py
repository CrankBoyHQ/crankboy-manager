"""Build a CrankBoy forwarder .pdx directory.

A forwarder is a tiny .pdx that runs pdboot's pdex.bin shim, which loads
a `crankboy.bin` payload (shared from /Shared/.forwarder/<id>/ or local
to the .pdx) and starts CrankBoy with a single ROM.
"""

import json
import os
import re
import shutil
import sys
import zlib
from pathlib import Path


def _pdboot_bin_path() -> Path:
    """Return the bundled pdboot shim binary path (dev or PyInstaller bundle).

    Stored as `pdboot.bin` in the manager assets to avoid confusion with
    the .pdx-bound name; it gets renamed to `pdex.bin` when copied into
    the forwarder .pdx at build time.
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "src" / "assets" / "pdboot.bin"
    return Path(__file__).parent.parent / "assets" / "pdboot.bin"


def read_rom_header_name(rom_path):
    """Read the cartridge title from ROM header bytes 0x134..0x143 (16 bytes).

    Returns a string with trailing NUL/spaces stripped. Returns "" if the
    file is too short to read the header.
    """
    try:
        with open(rom_path, "rb") as f:
            f.seek(0x134)
            raw = f.read(16)
    except (IOError, OSError):
        return ""
    # Stop at first NUL
    nul = raw.find(b"\x00")
    if nul >= 0:
        raw = raw[:nul]
    try:
        return raw.decode("ascii", errors="replace").strip()
    except Exception:
        return ""


_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_-]")


# Mirrors CrankBoy's articles list in src/utility.c
# (`articles[]` -> common_article_form). Move trailing ", The" / ", La"
# / ", Der" etc. back to the front of the title so the library display
# reads naturally.
_TRAILING_ARTICLES = (
    ", The", ", Las", ", A", ", Le", ", La", ", Los", ", An",
    ", Les", ", Der", ", Die", ", Das", ", Un", ", Une",
)


def common_article_form(input_):
    """Port of CrankBoy's `common_article_form` (see src/utility.c:1629).

    Rewrites titles like "Black Onyx, The (Japan)" -> "The Black Onyx (Japan)".
    Splits the title at the first " - " or " (" (whichever comes first);
    if the head ends with one of the recognised articles, the article is
    moved to the front of the head.
    """
    if not input_:
        return input_
    s = input_
    # Find first " - " or " (", whichever comes first.
    dash = s.find(" - ")
    paren = s.find(" (")
    candidates = [i for i in (dash, paren) if i >= 0]
    split_at = min(candidates) if candidates else len(s)
    head, tail = s[:split_at], s[split_at:]
    for art in _TRAILING_ARTICLES:
        if head.endswith(art):
            head = head[:-len(art)]
            # art is ", The" -> strip the leading ", " to get "The"
            return f"{art[2:]} {head}{tail}"
    return s


def strip_decorations(name):
    """Strip everything from the first " (" or " [" onward.

    e.g. `Pokemon Red Version (USA, Europe) [!]` -> `Pokemon Red Version`.
    This is what the DB's `short` field captures pre-baked; we apply it
    at runtime to filenames (which still have the dump tags) so the
    fallback title matches the DB-derived one stylistically.
    """
    if not name:
        return name
    paren = name.find(" (")
    brack = name.find(" [")
    candidates = [i for i in (paren, brack) if i >= 0]
    if not candidates:
        return name
    return name[:min(candidates)].rstrip()


def crankboy_display_title(rom_info, fallback):
    """Return the title CrankBoy would show for this ROM.

    Matches CrankBoy's name_short_leading_article: prefer the DB's
    parenthetical-stripped `short` field, then apply common_article_form
    so leading articles end up at the front. Falls back to the supplied
    `fallback` (typically the ROM filename without its extension) when
    the ROM isn't in the DB; the fallback gets the same treatment —
    decorations stripped then articles moved.
    """
    if rom_info:
        short = rom_info.get('short') or rom_info.get('long')
        if short:
            return common_article_form(short)
    if fallback:
        return common_article_form(strip_decorations(fallback))
    return fallback


def sanitize_romname(name):
    """Sanitize ROMNAME for use in paths/identifiers: keep [A-Za-z0-9_-],
    replace everything else with '_'. Collapse runs of underscores. Strip
    leading/trailing underscores. Returns a non-empty string ("ROM" fallback).
    """
    if not name:
        return "ROM"
    cleaned = _SANITIZE_RE.sub("_", name)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "ROM"


def compute_crc32(path):
    """CRC32 (unsigned) of a file's contents."""
    h = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h = zlib.crc32(chunk, h)
    return h & 0xFFFFFFFF


def make_pdxinfo(*, name, rom_name_sanitized, bundle_id, crankboy_pdxinfo):
    """Construct the forwarder .pdx pdxinfo file content.

    `crankboy_pdxinfo` is a dict of the running CrankBoy's pdxinfo fields,
    used as the source for version/buildNumber/pdxversion/buildtime.
    Missing fields default to empty strings.
    """
    def k(field):
        return crankboy_pdxinfo.get(field, "") if crankboy_pdxinfo else ""

    return (
        f"name={name}\n"
        f"author=CrankBoy Manager\n"
        f"description=A bundled CrankBoy forwarder for \"{rom_name_sanitized}\"\n"
        f"bundleID={bundle_id}\n"
        f"version={k('version')}\n"
        f"buildNumber={k('buildNumber')}\n"
        f"imagePath=launcher\n"
        f"pdxversion={k('pdxversion')}\n"
        f"buildtime={k('buildtime')}\n"
    )


DEFAULT_SHARED_BASE_DIR = "/Shared/Emulation/gb"


def build_forwarder_pdx(
    *,
    rom_path,
    out_parent_dir,
    share_crankboy_bin,
    share_rom,
    crankboy_pdxinfo,
    fwd_install_path,
    crankboy_bin_source,
    db_title=None,
    shared_base_dir=DEFAULT_SHARED_BASE_DIR,
    launcher_icon=None,
    launcher_card=None,
    log=None,
):
    """Assemble the forwarder .pdx in `out_parent_dir`.

    Args:
        rom_path: path to the source ROM file.
        out_parent_dir: where the .pdx directory will be created.
        share_crankboy_bin: if True, the .pdx contains no crankboy.bin
            and the `pdboot` config points at fwd_install_path.
        share_rom: if True, the .pdx contains no ROM file; bundle.json
            references the shared ROM path under /Shared/Emulation/gb.
        crankboy_pdxinfo: dict of CrankBoy's own pdxinfo fields.
        fwd_install_path: "/Shared/.forwarder/<bundleID>" (only used when
            share_crankboy_bin is True).
        crankboy_bin_source: filesystem path to crankboy.bin (only used
            when share_crankboy_bin is False; typically read off the data
            disk while it is mounted).
        db_title: optional friendly name (e.g. from the manager DB) used
            for the pdxinfo "name=" field. Falls back to ROM header name.

    Returns the absolute path to the created .pdx directory.
    """
    rom_name = read_rom_header_name(rom_path)
    rom_name_sanitized = sanitize_romname(rom_name)
    crc32 = compute_crc32(rom_path)
    crc_hex = f"{crc32:08x}"

    pdx_dirname = f"CrankBoy_fwd_{crc_hex}_{rom_name_sanitized}.pdx"
    bundle_id = f"app.crankboyhq.fwd_{crc_hex}_{rom_name_sanitized}"

    out_dir = Path(out_parent_dir) / pdx_dirname
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # 1) pdex.bin -- the pdboot shim (stored locally as pdboot.bin,
    #    renamed at copy-time to the name the Playdate firmware expects
    #    as a .pdx entrypoint).
    shutil.copy(_pdboot_bin_path(), out_dir / "pdex.bin")

    # 2) pdboot config: either point at the shared crankboy.bin or the
    #    local one we're about to drop in.
    if share_crankboy_bin:
        if not fwd_install_path:
            raise ValueError("share_crankboy_bin=True requires fwd_install_path")
        pdboot_line = f"data:{fwd_install_path}/crankboy.bin\n"
    else:
        pdboot_line = "pdx:crankboy.bin\n"
    (out_dir / "pdboot").write_text(pdboot_line, encoding="utf-8")

    # 3) crankboy.bin (only when not sharing).
    if not share_crankboy_bin:
        if not crankboy_bin_source or not os.path.isfile(crankboy_bin_source):
            raise FileNotFoundError(
                f"crankboy_bin_source not readable: {crankboy_bin_source!r}"
            )
        shutil.copy(crankboy_bin_source, out_dir / "crankboy.bin")

    # 4) bundle.json
    bundle = {}
    if share_rom:
        # CrankBoy on the device places ROMs under <shared_base_dir>/games.
        # The worker is expected to copy the ROM there during the data-disk
        # window; bundle.json points at the absolute shared path so the
        # forwarder still resolves even if directory.txt changes later.
        rom_basename = os.path.basename(rom_path)
        bundle["rom"] = f"{shared_base_dir.rstrip('/')}/games/{rom_basename}"
        bundle["shared"] = True
    else:
        rom_basename = os.path.basename(rom_path)
        bundle["rom"] = rom_basename
        shutil.copy(rom_path, out_dir / rom_basename)
    if share_crankboy_bin and fwd_install_path:
        bundle["fwd"] = fwd_install_path

    (out_dir / "bundle.json").write_text(
        json.dumps(bundle, indent=2) + "\n", encoding="utf-8"
    )

    # 5) pdxinfo
    # Run the final name through crankboy_display_title even when the
    # caller already cleaned it -- belt-and-suspenders, so a stray
    # "Tetris (World)" never escapes into pdxinfo. The function is
    # idempotent on already-stripped input.
    name = db_title or rom_name or rom_name_sanitized
    name = crankboy_display_title(None, name) or name
    (out_dir / "pdxinfo").write_text(
        make_pdxinfo(
            name=name,
            rom_name_sanitized=rom_name_sanitized,
            bundle_id=bundle_id,
            crankboy_pdxinfo=crankboy_pdxinfo,
        ),
        encoding="utf-8",
    )

    # 6) launcher/{icon.pdi, card.pdi} -- auto-compose if the caller
    #    didn't supply overrides.
    from src.core import icon_builder
    from src.core.pdi import pil_to_pdi
    if launcher_icon is None:
        launcher_icon = icon_builder.compose_launcher_icon_for_title(name)
    launcher_dir = out_dir / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir / "icon.pdi").write_bytes(pil_to_pdi(launcher_icon))

    if launcher_card is None:
        # Look up the ROM in the manager DB to find the libretro `long`
        # name; that's what drives the cover-fetch URL. Best-effort:
        # if the DB or the network is unavailable, the composer falls
        # through to the text fallback.
        rom_info = None
        try:
            from src.core.database import database as _rom_db
            rom_info = _rom_db.lookup(crc32)
        except Exception as e:
            if log:
                log(f"  card: DB lookup raised {e!r}")
        launcher_card = icon_builder.compose_launcher_card(
            title=name,
            rom_info=rom_info,
            log=log,
        )
    (launcher_dir / "card.pdi").write_bytes(pil_to_pdi(launcher_card))

    return str(out_dir), bundle_id, crc_hex, rom_name_sanitized
