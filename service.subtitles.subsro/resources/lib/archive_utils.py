# -*- coding: utf-8 -*-
"""
Archive extraction for subtitle files: ZIP (native), RAR (rarfile), plain SRT.
"""

import os
import zipfile
import io

from resources.lib import logger

SUBTITLE_EXTENSIONS = (".srt", ".sub", ".ass", ".ssa", ".txt", ".smi")

try:
    import rarfile
    HAS_RARFILE = True
except ImportError:
    HAS_RARFILE = False


def _find_subtitle_in_names(names):
    candidates = []
    for name in names:
        if "__MACOSX" in name or os.path.basename(name).startswith("."):
            continue
        _, ext = os.path.splitext(os.path.basename(name).lower())
        if ext in SUBTITLE_EXTENSIONS:
            candidates.append((name, ext))
    if not candidates:
        return None
    priority = {".srt": 0, ".sub": 1, ".ass": 2, ".ssa": 3, ".txt": 4, ".smi": 5}
    candidates.sort(key=lambda item: priority.get(item[1], 99))
    return candidates[0][0]


def _ensure_dest_dir(dest_dir):
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)


def _try_detect_encoding(data):
    try:
        import chardet
        detected = chardet.detect(data)
        if detected and detected.get("encoding"):
            return data.decode(detected["encoding"])
    except (ImportError, Exception):
        pass
    for enc in ["utf-8", "utf-8-sig", "cp1250", "iso-8859-2", "iso-8859-1", "latin-1"]:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _write_subtitle_file(data, dest_path):
    try:
        text = _try_detect_encoding(data)
        with open(dest_path, "w", encoding="utf-8-sig") as f:
            f.write(text)
        return dest_path
    except Exception as e:
        logger.error("Write subtitle failed: {err}".format(err=str(e)))
        try:
            with open(dest_path, "wb") as f:
                f.write(data)
            return dest_path
        except Exception:
            return None


def _extract_from_zip(archive_bytes, dest_dir):
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            target = _find_subtitle_in_names(zf.namelist())
            if not target:
                return None
            data = zf.read(target)
            return _write_subtitle_file(data, os.path.join(dest_dir, os.path.basename(target)))
    except zipfile.BadZipFile:
        return None
    except Exception as e:
        logger.error("ZIP error: {err}".format(err=str(e)))
        return None


def _extract_from_rar(archive_bytes, dest_dir):
    if not HAS_RARFILE:
        logger.warning("RAR not supported: install rarfile + unrar")
        return None
    temp_rar = os.path.join(dest_dir, "_temp.rar")
    try:
        with open(temp_rar, "wb") as f:
            f.write(archive_bytes)
        with rarfile.RarFile(temp_rar) as rf:
            target = _find_subtitle_in_names(rf.namelist())
            if not target:
                return None
            return _write_subtitle_file(rf.read(target), os.path.join(dest_dir, os.path.basename(target)))
    except Exception as e:
        logger.error("RAR error: {err}".format(err=str(e)))
        return None
    finally:
        try:
            if os.path.exists(temp_rar):
                os.remove(temp_rar)
        except Exception:
            pass


def _try_as_plain_subtitle(data, dest_dir):
    preview = data[:500].decode("utf-8", errors="replace").strip()
    if "-->" in preview or (preview and preview[0].isdigit()):
        return _write_subtitle_file(data, os.path.join(dest_dir, "subtitle.srt"))
    if "[Script Info]" in preview:
        return _write_subtitle_file(data, os.path.join(dest_dir, "subtitle.ass"))
    return None


def extract_subtitle(archive_bytes, dest_dir):
    """Extract subtitle from bytes. Returns path or None."""
    if not archive_bytes:
        return None
    _ensure_dest_dir(dest_dir)
    return (_extract_from_zip(archive_bytes, dest_dir)
            or _extract_from_rar(archive_bytes, dest_dir)
            or _try_as_plain_subtitle(archive_bytes, dest_dir))
