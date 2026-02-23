# -*- coding: utf-8 -*-
"""
Kodi utility functions for the subs.ro subtitle addon.

Provides helpers for extracting media metadata from the Kodi player,
creating subtitle ListItems for the Kodi subtitle search UI, and
displaying notifications to the user.
"""

import os
import re
import urllib.parse

import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon

from resources.lib import logger

ADDON_ID = "service.subtitles.subsro"

# Mapping from Kodi's full language names to subs.ro language codes
KODI_LANG_TO_SUBSRO = {
    "Romanian": "ro",
    "English": "en",
    "Italian": "ita",
    "French": "fra",
    "German": "ger",
    "Hungarian": "ung",
    "Greek": "gre",
    "Portuguese": "por",
    "Spanish": "spa",
}

# Reverse mapping for display
SUBSRO_LANG_TO_KODI = {v: k for k, v in KODI_LANG_TO_SUBSRO.items()}

# Language flags (ISO 639-1 codes for Kodi icon display)
SUBSRO_LANG_TO_FLAG = {
    "ro": "ro",
    "en": "en",
    "ita": "it",
    "fra": "fr",
    "ger": "de",
    "ung": "hu",
    "gre": "el",
    "por": "pt",
    "spa": "es",
}


def get_media_info():
    """
    Extract metadata about the currently playing media from the Kodi player.

    Returns:
        Dict with keys:
            imdb_id (str): IMDB ID like "tt1234567", or empty string
            title (str): Movie or episode title
            year (str): Release year
            season (str): Season number (empty for movies)
            episode (str): Episode number (empty for movies)
            tvshow_title (str): TV show title (empty for movies)
            original_title (str): Original title if different
            filename (str): Name of the playing file
            is_tv (bool): True if playing a TV show episode
    """
    imdb_id = xbmc.getInfoLabel("VideoPlayer.IMDBNumber")
    title = xbmc.getInfoLabel("VideoPlayer.Title")
    year = xbmc.getInfoLabel("VideoPlayer.Year")
    season = xbmc.getInfoLabel("VideoPlayer.Season")
    episode = xbmc.getInfoLabel("VideoPlayer.Episode")
    tvshow_title = xbmc.getInfoLabel("VideoPlayer.TVshowtitle")
    original_title = xbmc.getInfoLabel("VideoPlayer.OriginalTitle")

    # Get the playing filename
    filename = ""
    try:
        player = xbmc.Player()
        if player.isPlaying():
            filename = player.getPlayingFile()
    except Exception:
        pass

    # Determine if this is a TV show
    is_tv = bool(tvshow_title)

    # Clean up IMDB ID - ensure it starts with "tt"
    # Some Kodi sources pass TMDB IDs (numeric only) in the IMDBNumber field
    if imdb_id and not imdb_id.startswith("tt"):
        if imdb_id.isdigit():
            logger.debug("IMDBNumber appears to be TMDB ID: {id}".format(id=imdb_id))
            imdb_id = ""

    # For TV shows, use the show title instead of episode title for search
    effective_title = tvshow_title if is_tv else title
    if not effective_title:
        effective_title = original_title or title

    info = {
        "imdb_id": imdb_id or "",
        "title": effective_title or "",
        "year": year or "",
        "season": season or "",
        "episode": episode or "",
        "tvshow_title": tvshow_title or "",
        "original_title": original_title or "",
        "filename": filename or "",
        "is_tv": is_tv,
    }

    logger.debug("Media info: {info}".format(info=str(info)))
    return info


def get_kodi_languages(params):
    """
    Parse the languages requested by Kodi from the action parameters.

    Args:
        params: Dict of parsed query parameters from sys.argv[2].

    Returns:
        List of subs.ro language codes (e.g., ["ro", "en"]).
    """
    languages = []
    langs_str = params.get("languages", "")

    if langs_str:
        for lang_name in langs_str.split(","):
            lang_name = lang_name.strip()
            code = KODI_LANG_TO_SUBSRO.get(lang_name)
            if code:
                languages.append(code)

    # Also check preferredlanguage
    pref = params.get("preferredlanguage", "")
    if pref:
        code = KODI_LANG_TO_SUBSRO.get(pref)
        if code and code not in languages:
            languages.insert(0, code)

    # If no languages resolved, check addon setting
    if not languages:
        try:
            addon = xbmcaddon.Addon(ADDON_ID)
            pref_lang = addon.getSetting("preferred_language")
            if pref_lang:
                languages.append(pref_lang)
        except Exception:
            pass

    logger.debug("Requested languages: {langs}".format(langs=languages))
    return languages


def create_subtitle_listitem(subtitle, handle, plugin_url):
    """
    Create a Kodi ListItem for a subtitle search result.

    Args:
        subtitle: Dict from subs.ro API with keys like id, title, language, etc.
        handle: Kodi plugin handle (int).
        plugin_url: Base plugin URL for constructing the download action URL.
    """
    sub_id = subtitle.get("id", "")
    title = subtitle.get("title", "Unknown")
    language = subtitle.get("language", "ro")
    sub_type = subtitle.get("type", "movie")
    translator = subtitle.get("translator", "")
    year = subtitle.get("year", "")
    description = subtitle.get("description", "")

    # Build display label
    label_parts = [title]
    if year:
        label_parts.append("({year})".format(year=year))
    if translator:
        label_parts.append("[{translator}]".format(translator=translator))
    if description:
        # Truncate long descriptions
        desc_short = description[:60] + "..." if len(description) > 60 else description
        label_parts.append("- {desc}".format(desc=desc_short))

    label = " ".join(label_parts)

    # Get the Kodi language name and flag code
    kodi_lang_name = SUBSRO_LANG_TO_KODI.get(language, "Romanian")
    flag_code = SUBSRO_LANG_TO_FLAG.get(language, "ro")

    # Create ListItem
    listitem = xbmcgui.ListItem(label=kodi_lang_name, label2=label)

    # Set the language flag icon (Kodi rating field repurposed for subtitles)
    # For subtitle addons, setArt icon is used for rating display (0-8 scale)
    # We'll set a default rating of 5 (good)
    listitem.setArt({"icon": "5", "thumb": flag_code})

    # Subtitle properties
    listitem.setProperty("sync", "false")
    listitem.setProperty("hearing_impaired", "false")

    # Build download URL
    download_url = "{base}?{params}".format(
        base=plugin_url,
        params=urllib.parse.urlencode({
            "action": "download",
            "id": sub_id,
            "filename": "{title}.{lang}.srt".format(
                title=_sanitize_filename(title), lang=language
            ),
        }),
    )

    xbmcplugin.addDirectoryItem(
        handle=handle, url=download_url, listitem=listitem, isFolder=False
    )


def notify(message, time=3000, icon=xbmcgui.NOTIFICATION_INFO):
    """
    Display a notification popup in Kodi.

    Args:
        message: The notification text.
        time: Display duration in milliseconds.
        icon: Notification icon constant.
    """
    try:
        addon = xbmcaddon.Addon(ADDON_ID)
        addon_name = addon.getAddonInfo("name")
    except Exception:
        addon_name = "Subs.ro"

    xbmcgui.Dialog().notification(addon_name, message, icon, time)


def show_settings():
    """Open the addon settings dialog."""
    try:
        addon = xbmcaddon.Addon(ADDON_ID)
        addon.openSettings()
    except Exception as e:
        logger.error("Failed to open settings: {err}".format(err=str(e)))


def _sanitize_filename(name):
    """
    Remove invalid filename characters from a string.

    Args:
        name: The raw filename string.

    Returns:
        Sanitized string safe for use as a filename.
    """
    # Replace invalid chars with dots
    sanitized = re.sub(r'[\\/*?:"<>|]', ".", name)
    # Replace spaces with dots
    sanitized = sanitized.replace(" ", ".")
    # Collapse multiple dots
    sanitized = re.sub(r"\.{2,}", ".", sanitized)
    return sanitized.strip(".")
