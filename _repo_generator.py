#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Kodi Repository Generator for repository.subsro

Run after making changes to regenerate zips, addons.xml and addons.xml.md5.
Usage: python _repo_generator.py
"""
import os
import zipfile
import hashlib
import xml.etree.ElementTree as ET

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ZIPS_OUT = os.path.join(SCRIPT_DIR, "zips")

ADDONS = [
    ("repository.subsro", os.path.join(SCRIPT_DIR, "repository.subsro")),
    ("service.subtitles.subsro", os.path.join(SCRIPT_DIR, "service.subtitles.subsro")),
]


def get_version(source_dir):
    return ET.parse(os.path.join(source_dir, "addon.xml")).getroot().get("version", "1.0.0")


def create_zip(addon_id, version, source_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith(addon_id) and f.endswith(".zip"):
            os.remove(os.path.join(out_dir, f))
    zip_path = os.path.join(out_dir, "{}-{}.zip".format(addon_id, version))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source_dir):
            dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
            for fname in files:
                if fname.endswith(".pyc"):
                    continue
                full_path = os.path.join(root, fname)
                arcname = os.path.join(addon_id, os.path.relpath(full_path, source_dir)).replace("\\", "/")
                zf.write(full_path, arcname)
    print("[OK] {}-{}.zip ({} bytes)".format(addon_id, version, os.path.getsize(zip_path)))


def generate_addons_xml():
    lines = ["<?xml version='1.0' encoding='utf-8'?>", "<addons>"]
    for _, source_dir in ADDONS:
        content = ET.tostring(ET.parse(os.path.join(source_dir, "addon.xml")).getroot(), encoding="unicode")
        lines.append(content)
    lines.append("</addons>")
    return "\n".join(lines)


def main():
    print("=== Kodi Repo Generator ===\n")
    os.makedirs(ZIPS_OUT, exist_ok=True)
    for addon_id, source_dir in ADDONS:
        create_zip(addon_id, get_version(source_dir), source_dir, os.path.join(ZIPS_OUT, addon_id))
    addons_xml = generate_addons_xml()
    with open(os.path.join(ZIPS_OUT, "addons.xml"), "w", encoding="utf-8") as f:
        f.write(addons_xml)
    md5 = hashlib.md5(addons_xml.encode("utf-8")).hexdigest()
    with open(os.path.join(ZIPS_OUT, "addons.xml.md5"), "w") as f:
        f.write(md5)
    print("[OK] addons.xml + md5 ({})\n".format(md5))
    print("Done! git add zips/ && git commit -m 'chore: update repo' && git push")


if __name__ == "__main__":
    main()
