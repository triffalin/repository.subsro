#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Subs.ro Subtitle Addon for Kodi
Main entry point — routes search, manualsearch, download actions.
"""

import sys
import os
import time
import urllib.parse

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
TEMP_DIR = xbmcvfs.translatePath("special://temp/")

sys.path.insert(0, ADDON_PATH)

from resources.lib import logger
from resources.lib.subsro_api import SubsRoAPI, SubsRoAPIError, QuotaExceededError
from resources.lib.archive_utils import extract_subtitle
from resources.lib.kodi_utils import (
    get_media_info,
    get_kodi_languages,
    create_subtitle_listitem,
    notify,
    show_settings,
)

PLUGIN_URL = sys.argv[0]
HANDLE = int(sys.argv[1])
PARAMS = dict(urllib.parse.parse_qsl(sys.argv[2].lstrip("?")))


def _get_api():
    api_key = ADDON.getSetting("api_key")
    if not api_key or not api_key.strip():
        logger.warning("API key not configured")
        notify(
            ADDON.getLocalizedString(32031) or "Please set your subs.ro API key in addon settings.",
            time=5000,
            icon=xbmcgui.NOTIFICATION_WARNING,
        )
        show_settings()
        return None
    return SubsRoAPI(api_key.strip())


def _filter_by_year(results, year):
    if not year:
        return results
    try:
        target_year = int(year)
    except (ValueError, TypeError):
        return results
    filtered = [r for r in results if r.get("year") and abs(int(r.get("year", 0)) - target_year) <= 1]
    return filtered if filtered else results


def _filter_tv_results(results, season, episode):
    if not season or not episode:
        return results
    try:
        s_num, e_num = int(season), int(episode)
    except (ValueError, TypeError):
        return results
    patterns = [
        "S{s:02d}E{e:02d}".format(s=s_num, e=e_num),
        "s{s:02d}e{e:02d}".format(s=s_num, e=e_num),
        "{s}x{e:02d}".format(s=s_num, e=e_num),
        "Season {s}".format(s=s_num),
        "Sezonul {s}".format(s=s_num),
    ]
    episode_matches = [r for r in results if any(
        p.lower() in (r.get("title", "") + r.get("description", "")).lower() for p in patterns[:3])]
    season_matches = [r for r in results if any(
        p.lower() in (r.get("title", "") + r.get("description", "")).lower() for p in patterns[3:])]
    return episode_matches or season_matches or results


def search(params):
    api = _get_api()
    if not api:
        xbmcplugin.endOfDirectory(HANDLE)
        return
    media = get_media_info()
    languages = get_kodi_languages(params)
    logger.info("Search: title={title}, imdb={imdb}, year={year}, tv={is_tv}".format(**media))
    all_results = []
    for lang in (languages if languages else [None]):
        results = []
        if media["imdb_id"]:
            results = api.search("imdbid", media["imdb_id"], language=lang)
        if not results and media["title"]:
            results = _filter_by_year(api.search("title", media["title"], language=lang), media["year"])
        if not results and media["original_title"] and media["original_title"] != media["title"]:
            results = _filter_by_year(api.search("title", media["original_title"], language=lang), media["year"])
        if not results and media["filename"]:
            release_name = os.path.splitext(os.path.basename(media["filename"]))[0]
            if len(release_name) > 3:
                results = api.search("release", release_name, language=lang)
        if media["is_tv"] and results:
            results = _filter_tv_results(results, media["season"], media["episode"])
        seen_ids = {r.get("id") for r in all_results}
        all_results.extend(r for r in results if r.get("id") not in seen_ids)
    if all_results:
        for subtitle in all_results:
            try:
                create_subtitle_listitem(subtitle, HANDLE, PLUGIN_URL)
            except Exception as e:
                logger.error("ListItem error: {err}".format(err=str(e)))
    else:
        notify(ADDON.getLocalizedString(32032) or "No subtitles found", time=2000)
    xbmcplugin.endOfDirectory(HANDLE)


def manual_search(params):
    api = _get_api()
    if not api:
        xbmcplugin.endOfDirectory(HANDLE)
        return
    search_string = params.get("searchstring", "")
    if not search_string:
        xbmcplugin.endOfDirectory(HANDLE)
        return
    languages = get_kodi_languages(params)
    all_results = []
    for lang in (languages if languages else [None]):
        results = api.search("title", search_string, language=lang) or api.search("release", search_string, language=lang)
        seen_ids = {r.get("id") for r in all_results}
        all_results.extend(r for r in results if r.get("id") not in seen_ids)
    if all_results:
        for subtitle in all_results:
            try:
                create_subtitle_listitem(subtitle, HANDLE, PLUGIN_URL)
            except Exception as e:
                logger.error("ListItem error: {err}".format(err=str(e)))
    else:
        notify(ADDON.getLocalizedString(32032) or "No subtitles found", time=2000)
    xbmcplugin.endOfDirectory(HANDLE)


def download(params):
    api = _get_api()
    if not api:
        return
    subtitle_id = params.get("id", "")
    if not subtitle_id:
        notify(ADDON.getLocalizedString(32033) or "Download failed", icon=xbmcgui.NOTIFICATION_ERROR)
        return
    logger.info("Downloading subtitle ID: {id}".format(id=subtitle_id))
    dest_dir = os.path.join(TEMP_DIR, "subsro_{ts}".format(ts=int(time.time() * 1000)))
    try:
        archive_bytes = api.download(subtitle_id)
        if not archive_bytes:
            notify(ADDON.getLocalizedString(32033) or "Download failed", icon=xbmcgui.NOTIFICATION_ERROR)
            return
        subtitle_path = extract_subtitle(archive_bytes, dest_dir)
        if not subtitle_path:
            notify(ADDON.getLocalizedString(32035) or "Could not extract subtitle", icon=xbmcgui.NOTIFICATION_ERROR)
            return
        listitem = xbmcgui.ListItem(label=os.path.basename(subtitle_path))
        xbmcplugin.addDirectoryItem(handle=HANDLE, url=subtitle_path, listitem=listitem, isFolder=False)
        xbmcplugin.endOfDirectory(HANDLE)
    except QuotaExceededError:
        notify(ADDON.getLocalizedString(32034) or "Daily quota exceeded", time=5000, icon=xbmcgui.NOTIFICATION_WARNING)
    except (SubsRoAPIError, Exception) as e:
        logger.error("Download error: {err}".format(err=str(e)))
        notify(ADDON.getLocalizedString(32033) or "Download failed", icon=xbmcgui.NOTIFICATION_ERROR)


def main():
    action = PARAMS.get("action", "search")
    logger.info("action={action}".format(action=action))
    if action == "search":
        search(PARAMS)
    elif action == "manualsearch":
        manual_search(PARAMS)
    elif action == "download":
        download(PARAMS)
    else:
        xbmcplugin.endOfDirectory(HANDLE)


if __name__ == "__main__":
    main()
