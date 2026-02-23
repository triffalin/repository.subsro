# -*- coding: utf-8 -*-
"""
Archive extraction utilities for subtitle files.

Supports ZIP archives natively and RAR archives when the rarfile
library is available. Falls back to treating raw data as a plain
subtitle file if it is not a recognized archive format.
"""

import os
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


def _find_subtitle_in_names(names):
    """Find the best subtitle file from a list of archive member names."""
    candidates = []
    for name in names:
        if "__MACOSX" in name or name.startswith("."):
            continue
        basename = os.path.basename(name)
        if not basename:
            continue
        _, ext = os.path.splitext(basename.lower())
        if ext in SUBTITLE_EXTENSIONS:
            candidates.append((name, ext))

    if not candidates:
        return None

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


def _extract_from_zip(archive_bytes, dest_dir):
    """Extract subtitle from a ZIP archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            names = zf.namelist()
            log(__name__, "ZIP contains {n} files: {files}".format(
                n=len(names), files=", ".join(names[:10])
            ))

            target = _find_subtitle_in_names(names)
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


def _extract_from_rar(archive_bytes, dest_dir):
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

            target = _find_subtitle_in_names(names)
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
    """Treat raw bytes as a plain subtitle file (not inside an archive)."""
    text_preview = data[:500].decode("utf-8", errors="replace").strip()
    if "-->" in text_preview or (text_preview and text_preview[0].isdigit()):
        log(__name__, "Data appears to be a plain SRT file (not archived)")
        dest_path = os.path.join(dest_dir, "subtitle.srt")
        return _write_subtitle_file(data, dest_path)

    if "[Script Info]" in text_preview or "{0}" in text_preview:
        ext = ".ass" if "[Script Info]" in text_preview else ".sub"
        dest_path = os.path.join(dest_dir, "subtitle" + ext)
        return _write_subtitle_file(data, dest_path)

    return None


def extract_subtitle(archive_bytes, dest_dir):
    """
    Extract a subtitle file from archive bytes.

    Attempts extraction in this order:
    1. ZIP archive (native Python support)
    2. RAR archive (requires rarfile + unrar)
    3. Plain subtitle file (not archived)

    Args:
        archive_bytes: Raw bytes of the downloaded archive or subtitle file.
        dest_dir: Directory to extract the subtitle into. Created if needed.

    Returns:
        Absolute path to the extracted subtitle file, or None on failure.
    """
    if not archive_bytes:
        log(__name__, "No archive data to extract")
        return None

    _ensure_dest_dir(dest_dir)

    log(__name__, "Attempting to extract subtitle from {size} bytes to {dir}".format(
        size=len(archive_bytes), dir=dest_dir
    ))

    result = _extract_from_zip(archive_bytes, dest_dir)
    if result:
        return result

    result = _extract_from_rar(archive_bytes, dest_dir)
    if result:
        return result

    result = _try_as_plain_subtitle(archive_bytes, dest_dir)
    if result:
        return result

    log(__name__, "Could not extract any subtitle from the downloaded data")
    return None
