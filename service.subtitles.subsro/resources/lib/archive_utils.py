# -*- coding: utf-8 -*-
"""
Archive extraction utilities for subtitle files.

Supports ZIP archives natively and RAR archives when the rarfile
library is available. Falls back to treating raw data as a plain
subtitle file if it is not a recognized archive format.

v1.0.5: Added season/episode-aware subtitle selection for TV show archives.
v1.0.11: Strict validation in _try_as_plain_subtitle() to prevent HTML or
         binary archive data from being saved as .srt files.
"""

import os
import re
import zipfile
import io

from resources.lib.utilities import log

# Subtitle file extensions we look for inside archives
SUBTITLE_EXTENSIONS = (".srt", ".sub", ".ass", ".ssa", ".txt", ".smi")

# Try to import rarfile; it may not be available
try:
    import rarfile
    HAS_RARFILE = True
    log(__name__, "rarfile library available - RAR support enabled")
except ImportError:
    HAS_RARFILE = False
    log(__name__, "rarfile library not available - RAR support disabled")


def _find_subtitle_in_names(names, season=None, episode=None):
    """
    Find the best subtitle file from a list of archive member names.

    v1.0.5: When season/episode are provided, prefer files that match
    the requested episode (e.g., S02E05 in the filename).
    """
    candidates = []
    for name in names:
        if "__MACOSX" in name or name.startswith("."):
            continue
        basename = os.path.basename(name)
        if not basename:
            continue
        _, ext = os.path.splitext(basename.lower())
        if ext in SUBTITLE_EXTENSIONS:
            candidates.append((name, ext, basename))

    if not candidates:
        return None

    # If season/episode provided, try to find matching file
    if season is not None and episode is not None:
        try:
            season_int = int(season)
            episode_int = int(episode)
        except (ValueError, TypeError):
            season_int = None
            episode_int = None

        if season_int is not None and episode_int is not None:
            # Build patterns to match the specific episode in filenames
            episode_patterns = [
                # S01E05, s01e05, S1E5
                re.compile(r"[Ss]0?{s}[Ee]0?{e}(?:\b|[^0-9])".format(
                    s=season_int, e=episode_int)),
                # 1x05, 01x05
                re.compile(r"\b0?{s}[xX]0?{e:02d}\b".format(
                    s=season_int, e=episode_int)),
                # E05 (episode only, when archive is season-specific)
                re.compile(r"(?:^|[^0-9])[Ee]0?{e}(?:\b|[^0-9])".format(
                    e=episode_int)),
            ]

            # Score each candidate for episode matching
            episode_matches = []
            season_matches = []
            for name, ext, basename in candidates:
                searchable = basename
                matched_episode = False
                for pattern in episode_patterns:
                    if pattern.search(searchable):
                        episode_matches.append((name, ext, basename))
                        matched_episode = True
                        break

                if not matched_episode:
                    # Check if it at least matches the season
                    season_pattern = re.compile(
                        r"[Ss]0?{s}[Ee]|[Ss]ezon(?:ul)?\s*0?{s}\b|\b0?{s}[xX]\d".format(
                            s=season_int))
                    if season_pattern.search(searchable):
                        season_matches.append((name, ext, basename))

            if episode_matches:
                log(__name__, "Found {} episode-matching subtitle(s) for S{:02d}E{:02d}".format(
                    len(episode_matches), season_int, episode_int))
                # Sort by extension priority
                priority = {".srt": 0, ".sub": 1, ".ass": 2, ".ssa": 3, ".txt": 4, ".smi": 5}
                episode_matches.sort(key=lambda item: priority.get(item[1], 99))
                log(__name__, "Selected: {}".format(episode_matches[0][2]))
                return episode_matches[0][0]

            if season_matches:
                log(__name__, "No exact episode match, found {} season-matching subtitle(s)".format(
                    len(season_matches)))
                # Fall through to default selection from season matches
                priority = {".srt": 0, ".sub": 1, ".ass": 2, ".ssa": 3, ".txt": 4, ".smi": 5}
                season_matches.sort(key=lambda item: priority.get(item[1], 99))
                return season_matches[0][0]

            log(__name__, "No episode/season match found in archive filenames, using default selection")

    # Default: sort by extension priority and return first
    priority = {".srt": 0, ".sub": 1, ".ass": 2, ".ssa": 3, ".txt": 4, ".smi": 5}
    candidates.sort(key=lambda item: priority.get(item[1], 99))

    return candidates[0][0]


def _ensure_dest_dir(dest_dir):
    """Create destination directory if it does not exist."""
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)


def _try_detect_encoding(data):
    """Attempt to detect text encoding of subtitle data."""
    try:
        import chardet
        detected = chardet.detect(data)
        if detected and detected.get("encoding"):
            encoding = detected["encoding"]
            log(__name__, "chardet detected encoding: {enc}".format(enc=encoding))
            return data.decode(encoding)
    except ImportError:
        pass
    except Exception:
        pass

    encodings = ["utf-8", "utf-8-sig", "cp1250", "iso-8859-2", "iso-8859-1", "latin-1"]
    for enc in encodings:
        try:
            decoded = data.decode(enc)
            log(__name__, "Successfully decoded with: {enc}".format(enc=enc))
            return decoded
        except (UnicodeDecodeError, LookupError):
            continue

    return data.decode("utf-8", errors="replace")


def _write_subtitle_file(data, dest_path):
    """Write subtitle data to disk, re-encoding to UTF-8 if needed."""
    try:
        text = _try_detect_encoding(data)
        with open(dest_path, "w", encoding="utf-8-sig") as f:
            f.write(text)
        log(__name__, "Subtitle written to: {path}".format(path=dest_path))
        return dest_path
    except Exception as e:
        log(__name__, "Failed to write subtitle to {path}: {err}".format(
            path=dest_path, err=str(e)))
        try:
            with open(dest_path, "wb") as f:
                f.write(data)
            return dest_path
        except Exception:
            return None


def _extract_from_zip(archive_bytes, dest_dir, season=None, episode=None):
    """Extract subtitle from a ZIP archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            names = zf.namelist()
            log(__name__, "ZIP contains {n} files: {files}".format(
                n=len(names), files=", ".join(names[:10])
            ))

            target = _find_subtitle_in_names(names, season=season, episode=episode)
            if not target:
                log(__name__, "No subtitle file found in ZIP archive")
                return None

            data = zf.read(target)
            basename = os.path.basename(target)
            dest_path = os.path.join(dest_dir, basename)
            return _write_subtitle_file(data, dest_path)

    except zipfile.BadZipFile:
        log(__name__, "Not a valid ZIP file")
        return None
    except Exception as e:
        log(__name__, "ZIP extraction error: {err}".format(err=str(e)))
        return None


def _extract_from_rar(archive_bytes, dest_dir, season=None, episode=None):
    """Extract subtitle from a RAR archive using the rarfile library."""
    if not HAS_RARFILE:
        log(__name__, "RAR archive detected but rarfile library is not available.")
        return None

    temp_rar = os.path.join(dest_dir, "_temp_archive.rar")
    try:
        with open(temp_rar, "wb") as f:
            f.write(archive_bytes)

        with rarfile.RarFile(temp_rar) as rf:
            names = rf.namelist()
            log(__name__, "RAR contains {n} files: {files}".format(
                n=len(names), files=", ".join(names[:10])
            ))

            target = _find_subtitle_in_names(names, season=season, episode=episode)
            if not target:
                log(__name__, "No subtitle file found in RAR archive")
                return None

            data = rf.read(target)
            basename = os.path.basename(target)
            dest_path = os.path.join(dest_dir, basename)
            return _write_subtitle_file(data, dest_path)

    except Exception as e:
        log(__name__, "RAR extraction error: {err}".format(err=str(e)))
        return None
    finally:
        try:
            if os.path.exists(temp_rar):
                os.remove(temp_rar)
        except Exception:
            pass


def _try_as_plain_subtitle(data, dest_dir):
    """Treat raw bytes as a plain subtitle file (not inside an archive).

    v1.0.11: Strict validation - rejects binary archives and HTML content.
    HTML pages (login redirects) contain --> in JS comments and would
    previously pass the old check, causing garbage .srt files and
    'Unable to create subtitle parser' errors in Kodi.
    """
    # Reject known binary archive magic bytes immediately
    if data[:4] == b'PK\x03\x04':
        log(__name__, "Data is a ZIP archive (magic PK) - not a plain subtitle")
        return None
    if len(data) >= 6 and data[:6] == b'Rar!\x1a\x07':
        log(__name__, "Data is a RAR archive - not a plain subtitle")
        return None
    if data[:4] == b'7z\xbc\xaf':
        log(__name__, "Data is a 7z archive - not a plain subtitle")
        return None

    try:
        text_preview = data[:1000].decode("utf-8", errors="replace").strip()
    except Exception:
        return None

    # v1.0.11: Reject HTML content (login redirect pages)
    # These contain --> in JS comments like <!-- ... --> and would fool the old check
    text_lower = text_preview[:200].lower()
    if text_lower.startswith(("<!doctype", "<html", "<?xml", "<?php")):
        log(__name__, "Data starts with HTML/XML marker - login redirect, not a subtitle")
        return None
    # Count angle brackets -- subtitle files have very few (maybe <i>, <b> tags)
    angle_count = text_preview[:500].count("<")
    if angle_count > 8:
        log(__name__, "Data has {} angle brackets - likely HTML page, not subtitle".format(
            angle_count))
        return None

    # Require proper SRT timestamp format: HH:MM:SS,mmm --> HH:MM:SS,mmm
    if re.search(r'\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}', text_preview):
        log(__name__, "Data appears to be a plain subtitle file (has SRT timestamps)")
        dest_path = os.path.join(dest_dir, "subtitle.srt")
        return _write_subtitle_file(data, dest_path)

    if "[Script Info]" in text_preview or "{0}" in text_preview:
        ext = ".ass" if "[Script Info]" in text_preview else ".sub"
        dest_path = os.path.join(dest_dir, "subtitle" + ext)
        return _write_subtitle_file(data, dest_path)

    return None


def extract_subtitle(archive_bytes, dest_dir, season=None, episode=None):
    """
    Extract a subtitle file from archive bytes.

    Attempts extraction in this order:
    1. ZIP archive (native Python support)
    2. RAR archive (requires rarfile + unrar)
    3. Plain subtitle file (not archived)

    Args:
        archive_bytes: Raw bytes of the downloaded archive or subtitle file.
        dest_dir: Directory to extract the subtitle into. Created if needed.
        season: Season number (str or int) for TV episode matching. Optional.
        episode: Episode number (str or int) for TV episode matching. Optional.

    Returns:
        Absolute path to the extracted subtitle file, or None on failure.
    """
    if not archive_bytes:
        log(__name__, "No archive data to extract")
        return None

    _ensure_dest_dir(dest_dir)

    log(__name__, "Attempting to extract subtitle from {size} bytes to {dir} (S={s} E={e})".format(
        size=len(archive_bytes), dir=dest_dir, s=season, e=episode
    ))

    # v1.0.11: Log the archive format based on magic bytes for diagnostics
    if archive_bytes[:4] == b'PK\x03\x04':
        log(__name__, "Archive format: ZIP (magic PK)")
    elif len(archive_bytes) >= 6 and archive_bytes[:6] == b'Rar!\x1a\x07':
        log(__name__, "Archive format: RAR - rarfile support: {}".format(HAS_RARFILE))
        if not HAS_RARFILE:
            log(__name__, "WARNING: RAR archive cannot be extracted (rarfile library not installed)")
    elif archive_bytes[:2] in (b'\xd0\xcf', b'ID3'):
        log(__name__, "Archive format: unknown binary ({})".format(archive_bytes[:4]))
    else:
        # Try to decode first 50 bytes as text to see what it is
        preview = archive_bytes[:50].decode("utf-8", errors="replace")
        log(__name__, "Archive first bytes (text): {}".format(repr(preview[:50])))

    result = _extract_from_zip(archive_bytes, dest_dir, season=season, episode=episode)
    if result:
        return result

    result = _extract_from_rar(archive_bytes, dest_dir, season=season, episode=episode)
    if result:
        return result

    result = _try_as_plain_subtitle(archive_bytes, dest_dir)
    if result:
        return result

    log(__name__, "Could not extract any subtitle from the downloaded data")
    return None
