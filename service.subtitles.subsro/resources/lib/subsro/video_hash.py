# -*- coding: utf-8 -*-
"""
Video file hash calculation for subtitle matching.

NOTE: The OpenSubtitles hash is already computed in file_operations.py
(hash_file function) during the normal Kodi file data collection.
This module provides a standalone version for use outside of
file_operations.py, and an MD5 hash for additional matching.

Implements two hash algorithms:
1. OpenSubtitles hash (VLC-style): 64-bit hash from file size + first/last 64KB
   This is the same algorithm used by VLC, MPC-HC, and most subtitle addons.
2. MD5 of first 4KB (simpler, used by some providers)

The hash identifies the EXACT video release, enabling perfect subtitle matching
regardless of title/IMDB availability.

NOTE: Hash computation only works for LOCAL files. Remote streams
(Real-Debrid, HTTP, etc.) cannot be hashed directly. For those,
file_operations.py already handles it via Window properties.
"""

import os
import struct
import hashlib

from resources.lib.utilities import log


def _log(msg):
    return log(__name__, msg)


def compute_opensubtitles_hash(file_path):
    """
    Compute OpenSubtitles hash (same algorithm used by VLC).

    Algorithm:
    1. Take file size as initial hash value
    2. Read first 64KB, add each 8-byte long long to hash
    3. Read last 64KB, add each 8-byte long long to hash
    4. Return as 16-char hex string

    Args:
        file_path: Absolute path to a local video file.

    Returns:
        Tuple of (file_size, hash_hex_string) or (None, None) if not accessible.
    """
    if not file_path:
        return None, None

    try:
        # Try using xbmcvfs first (works with Kodi virtual file system)
        try:
            import xbmcvfs
            f = xbmcvfs.File(file_path)
            file_size = f.size()

            if file_size < 65536 * 2:
                f.close()
                _log("File too small for hash: {} bytes".format(file_size))
                return None, None

            hash_val = file_size

            # Read first 64KB
            buffer = f.readBytes(65536)
            # Read last 64KB
            f.seek(max(0, file_size - 65536), 0)
            buffer += f.readBytes(65536)
            f.close()

            long_long_format = "q"
            byte_size = struct.calcsize(long_long_format)

            for x in range(int(65536 / byte_size) * 2):
                size = x * byte_size
                (l_value,) = struct.unpack(long_long_format, buffer[size:size + byte_size])
                hash_val += l_value
                hash_val = hash_val & 0xFFFFFFFFFFFFFFFF

            return_hash = "%016x" % hash_val
            _log("OpenSubtitles hash: {} (size: {})".format(return_hash, file_size))
            return file_size, return_hash

        except ImportError:
            # xbmcvfs not available -- use standard file I/O
            pass

        # Fallback: standard Python file I/O
        if not os.path.isfile(file_path):
            _log("File not found: {}".format(file_path))
            return None, None

        file_size = os.path.getsize(file_path)
        if file_size < 65536 * 2:
            _log("File too small for hash: {} bytes".format(file_size))
            return None, None

        hash_val = file_size
        long_long_format = "q"
        byte_size = struct.calcsize(long_long_format)

        with open(file_path, "rb") as f:
            # Read first 64KB
            buffer = f.read(65536)
            # Read last 64KB
            f.seek(max(0, file_size - 65536), 0)
            buffer += f.read(65536)

        for x in range(int(65536 / byte_size) * 2):
            size = x * byte_size
            (l_value,) = struct.unpack(long_long_format, buffer[size:size + byte_size])
            hash_val += l_value
            hash_val = hash_val & 0xFFFFFFFFFFFFFFFF

        return_hash = "%016x" % hash_val
        _log("OpenSubtitles hash: {} (size: {})".format(return_hash, file_size))
        return file_size, return_hash

    except Exception as e:
        _log("Hash computation failed: {}".format(e))
        return None, None


def compute_md5_hash(file_path, chunk_size=4096):
    """
    Compute MD5 of first chunk of a file. Simpler hash for fallback matching.

    Args:
        file_path: Absolute path to a local video file.
        chunk_size: Number of bytes to read (default 4KB).

    Returns:
        MD5 hex string or None if not accessible.
    """
    if not file_path:
        return None

    try:
        try:
            import xbmcvfs
            f = xbmcvfs.File(file_path)
            data = f.readBytes(chunk_size)
            f.close()
            if data:
                return hashlib.md5(data).hexdigest()
            return None
        except ImportError:
            pass

        if not os.path.isfile(file_path):
            return None

        with open(file_path, "rb") as f:
            data = f.read(chunk_size)

        if data:
            return hashlib.md5(data).hexdigest()
        return None

    except Exception as e:
        _log("MD5 hash failed: {}".format(e))
        return None
