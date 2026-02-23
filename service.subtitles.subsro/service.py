#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Subs.ro Subtitle Addon for Kodi

Main entry point called by Kodi's subtitle search system.
Based on the official opensubtitles.com addon structure.

Kodi calls this script with:
    sys.argv[0] = plugin URL (e.g., "plugin://service.subtitles.subsro/")
    sys.argv[1] = handle (int for xbmcplugin calls)
    sys.argv[2] = query string (e.g., "?action=search&languages=English,Romanian")
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

# Addon references
ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
ADDON_PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
TEMP_DIR = xbmcvfs.translatePath("special://temp/")

# Add addon root to sys.path so resources.lib imports work
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

# Parse Kodi arguments
PLUGIN_URL = sys.argv[0]
HANDLE = int(sys.argv[1])
PARAMS = dict(urllib.parse.parse_qsl(sys.argv[2].lstrip("?")))


def _get_api():
    """
    Create and return a SubsRoAPI instance using the configured API key.
    Returns None if no API key is set (shows settings dialog).
    """
    api_key = ADDON.getSetting("api_key")
    if not api_key or not api_key.strip():
        logger.warning("API key not configured")
        notify(
            ADDON.getLocalizedString(32031)
            or "Please set your subs.ro API key in addon settings.",
            time=5000,
            icon=xbmcgui.NOTIFICATION_WARNING,
        )
        show_settings()
        return None
    return SubsRoAPI(api_key.strip())


def _filter_by_year(results, year):
    """Filter search results by year (±1 tolerance)."""
    if not year:
        return results
    try:
        target_year = int(year)
    except (ValueError, TypeError):
        return results
    filtered = [
        r for r in results
        if r.get("year") and abs(int(r.get("year", 0)) - target_year) <= 1
    ]
    return filtered if filtered else results


def _filter_tv_results(results, season, episode):
    """
    For TV shows, filter results matching the specific season/episode.
    Falls back to season matches, then all results.
    """
    if not season or not episode:
        return results
    try:
        s_num = int(season)
        e_num = int(episode)
    except (ValueError, TypeError):
        return results

    ep_patterns = [
        "S{s:02d}E{e:02d}".format(s=s_num, e=e_num).lower(),
        "{s}x{e:02d}".format(s=s_num, e=e_num),
    ]
    season_patterns = [
        "season {s}".format(s=s_num),
        "sezonul {s}".format(s=s_num),
    ]

    episode_matches = []
    season_matches = []

    for result in results:
        text = " ".join([
            result.get("title", ""),
            result.get("description", ""),
        ]).lower()

        if any(p in text for p in ep_patterns):
            episode_matches.append(result)
        elif any(p in text for p in season_patterns):
            season_matches.append(result)

    if episode_matches:
        logger.debug("TV filter S{s:02d}E{e:02d}: {n} episode matches".format(
            s=s_num, e=e_num, n=len(episode_matches)
        ))
        return episode_matches
    if season_matches:
        logger.debug("TV filter S{s:02d}: {n} season matches".format(
            s=s_num, n=len(season_matches)
        ))
        return season_matches
    return results


def search(params):
    """
    Search for subtitles based on currently playing media.

    Search strategy:
    1. IMDB ID (most accurate)
    2. Title + year filter
    3. Original title + year filter
    4. Release name from filename
    For TV: filter results by season/episode.
    """
    api = _get_api()
    if not api:
        xbmcplugin.endOfDirectory(HANDLE)
        return

    media = get_media_info()
    languages = get_kodi_languages(params)

    logger.info(
        "Search: title={title}, imdb={imdb}, year={year}, "
        "tv={is_tv}, S{season}E{episode}".format(
            title=media["title"],
            imdb=media["imdb_id"],
            year=media["year"],
            is_tv=media["is_tv"],
            season=media["season"],
            episode=media["episode"],
        )
    )

    all_results = []
    search_languages = languages if languages else [None]

    for lang in search_languages:
        results = []

        # 1. IMDB ID search
        if media["imdb_id"]:
            results = api.search("imdbid", media["imdb_id"], language=lang)
            if results:
                logger.info("IMDB [{imdb}] lang={lang}: {n} results".format(
                    imdb=media["imdb_id"], lang=lang, n=len(results)
                ))

        # 2. Title search
        if not results and media["title"]:
            results = api.search("title", media["title"], language=lang)
            if results:
                results = _filter_by_year(results, media["year"])

        # 3. Original title
        if not results and media["original_title"] and media["original_title"] != media["title"]:
            results = api.search("title", media["original_title"], language=lang)
            if results:
                results = _filter_by_year(results, media["year"])

        # 4. Release name from filename
        if not results and media["filename"]:
            basename = os.path.splitext(os.path.basename(media["filename"]))[0]
            if basename and len(basename) > 3:
                results = api.search("release", basename, language=lang)

        # TV filter
        if media["is_tv"] and results:
            results = _filter_tv_results(results, media["season"], media["episode"])

        # Merge unique results
        seen_ids = {r.get("id") for r in all_results}
        for result in results:
            if result.get("id") not in seen_ids:
                all_results.append(result)
                seen_ids.add(result.get("id"))

    if all_results:
        logger.info("Displaying {n} results".format(n=len(all_results)))
        for subtitle in all_results:
            try:
                create_subtitle_listitem(subtitle, HANDLE, PLUGIN_URL)
            except Exception as e:
                logger.error("ListItem error for id={id}: {err}".format(
                    id=subtitle.get("id", "?"), err=str(e)
                ))
    else:
        logger.info("No subtitles found")
        notify(ADDON.getLocalizedString(32032) or "No subtitles found", time=2000)

    xbmcplugin.endOfDirectory(HANDLE)


def manual_search(params):
    """Search for subtitles using a manually entered title/query."""
    api = _get_api()
    if not api:
        xbmcplugin.endOfDirectory(HANDLE)
        return

    search_string = params.get("searchstring", "")
    if not search_string:
        logger.warning("Manual search called with empty search string")
        xbmcplugin.endOfDirectory(HANDLE)
        return

    languages = get_kodi_languages(params)
    logger.info("Manual search: query={q}, languages={langs}".format(
        q=search_string, langs=languages
    ))

    all_results = []
    search_languages = languages if languages else [None]

    for lang in search_languages:
        results = api.search("title", search_string, language=lang)
        if not results:
            results = api.search("release", search_string, language=lang)

        seen_ids = {r.get("id") for r in all_results}
        for result in results:
            if result.get("id") not in seen_ids:
                all_results.append(result)
                seen_ids.add(result.get("id"))

    if all_results:
        logger.info("Manual search: {n} results".format(n=len(all_results)))
        for subtitle in all_results:
            try:
                create_subtitle_listitem(subtitle, HANDLE, PLUGIN_URL)
            except Exception as e:
                logger.error("ListItem error: {err}".format(err=str(e)))
    else:
        notify(ADDON.getLocalizedString(32032) or "No subtitles found", time=2000)

    xbmcplugin.endOfDirectory(HANDLE)


def download(params):
    """
    Download the selected subtitle and return its path to Kodi.

    Flow: download archive → extract subtitle → return path via ListItem.
    """
    api = _get_api()
    if not api:
        xbmcplugin.endOfDirectory(HANDLE)
        return

    subtitle_id = params.get("id", "")
    if not subtitle_id:
        logger.error("Download called without subtitle ID")
        notify(
            ADDON.getLocalizedString(32033) or "Download failed",
            icon=xbmcgui.NOTIFICATION_ERROR,
        )
        xbmcplugin.endOfDirectory(HANDLE)
        return

    logger.info("Downloading subtitle ID: {id}".format(id=subtitle_id))

    timestamp = int(time.time() * 1000)
    dest_dir = os.path.join(TEMP_DIR, "subsro_{ts}".format(ts=timestamp))

    try:
        archive_bytes = api.download(subtitle_id)

        if not archive_bytes:
            logger.error("Empty response for subtitle {id}".format(id=subtitle_id))
            notify(
                ADDON.getLocalizedString(32033) or "Download failed",
                icon=xbmcgui.NOTIFICATION_ERROR,
            )
            xbmcplugin.endOfDirectory(HANDLE)
            return

        subtitle_path = extract_subtitle(archive_bytes, dest_dir)

        if not subtitle_path:
            logger.error("Could not extract subtitle from archive, id={id}".format(id=subtitle_id))
            notify(
                ADDON.getLocalizedString(32035) or "Could not extract subtitle from archive",
                icon=xbmcgui.NOTIFICATION_ERROR,
            )
            xbmcplugin.endOfDirectory(HANDLE)
            return

        logger.info("Subtitle extracted to: {path}".format(path=subtitle_path))

        listitem = xbmcgui.ListItem(label=os.path.basename(subtitle_path))
        xbmcplugin.addDirectoryItem(
            handle=HANDLE,
            url=subtitle_path,
            listitem=listitem,
            isFolder=False,
        )

    except QuotaExceededError:
        logger.warning("Daily download quota exceeded")
        notify(
            ADDON.getLocalizedString(32034) or "Daily quota exceeded",
            time=5000,
            icon=xbmcgui.NOTIFICATION_WARNING,
        )
    except SubsRoAPIError as e:
        logger.error("API error during download: {err}".format(err=str(e)))
        notify(
            ADDON.getLocalizedString(32033) or "Download failed",
            icon=xbmcgui.NOTIFICATION_ERROR,
        )
    except Exception as e:
        logger.error("Unexpected error during download: {err}".format(err=str(e)))
        notify(
            ADDON.getLocalizedString(32033) or "Download failed",
            icon=xbmcgui.NOTIFICATION_ERROR,
        )

    xbmcplugin.endOfDirectory(HANDLE)


def main():
    """Route to the appropriate action handler based on the 'action' parameter."""
    action = PARAMS.get("action", "search")
    logger.info("action={action}, params={params}".format(action=action, params=PARAMS))

    if action == "search":
        search(PARAMS)
    elif action == "manualsearch":
        manual_search(PARAMS)
    elif action == "download":
        download(PARAMS)
    else:
        logger.warning("Unknown action: {action}".format(action=action))
        xbmcplugin.endOfDirectory(HANDLE)


if __name__ == "__main__":
    main()
