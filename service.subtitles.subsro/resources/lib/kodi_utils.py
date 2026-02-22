# -*- coding: utf-8 -*-
import os
import re
import urllib.parse

import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon

from resources.lib import logger

ADDON_ID = "service.subtitles.subsro"

KODI_LANG_TO_SUBSRO = {
    "Romanian": "ro", "English": "en", "Italian": "ita",
    "French": "fra", "German": "ger", "Hungarian": "ung",
    "Greek": "gre", "Portuguese": "por", "Spanish": "spa",
}
SUBSRO_LANG_TO_KODI = {v: k for k, v in KODI_LANG_TO_SUBSRO.items()}
SUBSRO_LANG_TO_FLAG = {
    "ro": "ro", "en": "en", "ita": "it", "fra": "fr",
    "ger": "de", "ung": "hu", "gre": "el", "por": "pt", "spa": "es",
}


def get_media_info():
    imdb_id = xbmc.getInfoLabel("VideoPlayer.IMDBNumber")
    title = xbmc.getInfoLabel("VideoPlayer.Title")
    year = xbmc.getInfoLabel("VideoPlayer.Year")
    season = xbmc.getInfoLabel("VideoPlayer.Season")
    episode = xbmc.getInfoLabel("VideoPlayer.Episode")
    tvshow_title = xbmc.getInfoLabel("VideoPlayer.TVshowtitle")
    original_title = xbmc.getInfoLabel("VideoPlayer.OriginalTitle")
    filename = ""
    try:
        player = xbmc.Player()
        if player.isPlaying():
            filename = player.getPlayingFile()
    except Exception:
        pass
    is_tv = bool(tvshow_title)
    if imdb_id and not re.match(r"^tt\d+$", imdb_id):
        if imdb_id.isdigit():
            imdb_id = ""
    return {
        "imdb_id": imdb_id or "",
        "title": (tvshow_title if is_tv else title) or original_title or "",
        "year": year or "",
        "season": season or "",
        "episode": episode or "",
        "tvshow_title": tvshow_title or "",
        "original_title": original_title or "",
        "filename": filename or "",
        "is_tv": is_tv,
    }


def get_kodi_languages(params):
    languages = []
    langs_str = params.get("languages", "")
    if langs_str:
        for lang_name in langs_str.split(","):
            code = KODI_LANG_TO_SUBSRO.get(lang_name.strip())
            if code:
                languages.append(code)
    pref = params.get("preferredlanguage", "")
    if pref:
        code = KODI_LANG_TO_SUBSRO.get(pref)
        if code and code not in languages:
            languages.insert(0, code)
    if not languages:
        try:
            pref_lang = xbmcaddon.Addon(ADDON_ID).getSetting("preferred_language")
            if pref_lang:
                languages.append(pref_lang)
        except Exception:
            pass
    return languages


def create_subtitle_listitem(subtitle, handle, plugin_url):
    sub_id = subtitle.get("id", "")
    title = subtitle.get("title", "Unknown")
    language = subtitle.get("language", "ro")
    translator = subtitle.get("translator", "")
    year = subtitle.get("year", "")
    description = subtitle.get("description", "")
    label_parts = [title]
    if year:
        label_parts.append("({})".format(year))
    if translator:
        label_parts.append("[{}]".format(translator))
    if description:
        label_parts.append("- {}".format(description[:60] + "..." if len(description) > 60 else description))
    listitem = xbmcgui.ListItem(label=SUBSRO_LANG_TO_KODI.get(language, "Romanian"), label2=" ".join(label_parts))
    listitem.setArt({"icon": "5", "thumb": SUBSRO_LANG_TO_FLAG.get(language, "ro")})
    listitem.setProperty("sync", "false")
    listitem.setProperty("hearing_impaired", "false")
    download_url = "{base}?{params}".format(
        base=plugin_url,
        params=urllib.parse.urlencode({"action": "download", "id": sub_id,
            "filename": "{}.{}.srt".format(_sanitize_filename(title), language)}),
    )
    xbmcplugin.addDirectoryItem(handle=handle, url=download_url, listitem=listitem, isFolder=False)


def notify(message, time=3000, icon=xbmcgui.NOTIFICATION_INFO):
    try:
        addon_name = xbmcaddon.Addon(ADDON_ID).getAddonInfo("name")
    except Exception:
        addon_name = "Subs.ro"
    xbmcgui.Dialog().notification(addon_name, message, icon, time)


def show_settings():
    try:
        xbmcaddon.Addon(ADDON_ID).openSettings()
    except Exception as e:
        logger.error("Settings error: {err}".format(err=str(e)))


def _sanitize_filename(name):
    sanitized = re.sub(r'[\\/*?:"<>|]', ".", name).replace(" ", ".")
    return re.sub(r"\.{2,}", ".", sanitized).strip(".")
