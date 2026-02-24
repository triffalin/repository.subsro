import os
import shutil
import sys

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib.data_collector import (
    get_language_data, get_media_data, get_file_path,
    convert_language, clean_feature_release_name, get_flag
)
from resources.lib.exceptions import (
    AuthenticationError, ConfigurationError, DownloadLimitExceeded,
    ProviderError, ServiceUnavailable, TooManyRequests
)
from resources.lib.file_operations import get_file_data
from resources.lib.subsro.provider import SubsroProvider, SUBSRO_TO_LANG, SUBSRO_TO_FLAG
from resources.lib.utilities import get_params, log, error
from resources.lib.archive_utils import extract_subtitle

__addon__ = xbmcaddon.Addon()
__scriptid__ = __addon__.getAddonInfo("id")

__profile__ = xbmcvfs.translatePath(__addon__.getAddonInfo("profile"))
__temp__ = xbmcvfs.translatePath(os.path.join(__profile__, "temp", ""))

if xbmcvfs.exists(__temp__):
    shutil.rmtree(__temp__)
xbmcvfs.mkdirs(__temp__)


class SubtitleDownloader:

    def __init__(self):

        self.api_key = __addon__.getSetting("APIKey")

        log(__name__, sys.argv)

        self.sub_format = "srt"
        self.handle = int(sys.argv[1])
        self.params = get_params()
        self.query = {}
        self.subtitles = {}
        self.file = {}
        self.subsro = None

        try:
            self.subsro = SubsroProvider(self.api_key)
        except ConfigurationError as e:
            error(__name__, 32002, e)

    def handle_action(self):
        log(__name__, "action '%s' called" % self.params.get("action", ""))
        if self.params.get("action") == "manualsearch":
            self.search(self.params.get("searchstring", ""))
        elif self.params.get("action") == "search":
            self.search()
        elif self.params.get("action") == "download":
            self.download()

    def search(self, query=""):
        if not self.subsro:
            log(__name__, "No provider - API key not configured")
            return

        file_data = get_file_data(get_file_path())
        language_data = get_language_data(self.params)

        log(__name__, "file_data '%s' " % file_data)
        log(__name__, "language_data '%s' " % language_data)

        if query:
            media_data = {"query": query}
        else:
            media_data = get_media_data()
            if "basename" in file_data and not media_data.get("query"):
                media_data["query"] = file_data["basename"]
            log(__name__, "media_data '%s' " % media_data)

        self.query = {**media_data, **file_data, **language_data}

        try:
            self.subtitles = self.subsro.search_subtitles(self.query)
        except AuthenticationError as e:
            error(__name__, 32003, e)
            return
        except (TooManyRequests, ServiceUnavailable, ProviderError, ValueError) as e:
            error(__name__, 32001, e)
            return

        if self.subtitles and len(self.subtitles):
            log(__name__, len(self.subtitles))
            self.list_subtitles()
        else:
            log(__name__, "No subtitle found")

    def download(self):
        subtitle_id = self.params.get("id", "")
        language = self.params.get("language", "ro")
        # v1.0.5: Pass season/episode to extraction for TV show episode matching
        season = self.params.get("season")
        episode = self.params.get("episode")

        log(__name__, "Download request: id={}, language={}, season={}, episode={}".format(
            subtitle_id, language, season, episode))

        if not self.subsro:
            log(__name__, "No provider - API key not configured")
            return

        if not subtitle_id:
            log(__name__, "No subtitle ID provided - cannot download")
            return

        # v1.0.8: Initialize archive_content before try block to prevent
        # UnboundLocalError if AuthenticationError or DownloadLimitExceeded is raised
        archive_content = None

        try:
            archive_content = self.subsro.download_subtitle(subtitle_id)
            log(__name__, "Downloaded archive: {} bytes".format(len(archive_content)))
        except AuthenticationError as e:
            error(__name__, 32003, e)
            return
        except DownloadLimitExceeded as e:
            log(__name__, "Download limit exceeded: {}".format(e))
            error(__name__, 32004, e)
            return
        except (TooManyRequests, ServiceUnavailable, ProviderError, ValueError) as e:
            log(__name__, "Download failed: {}".format(e))
            error(__name__, 32001, e)
            return

        if not archive_content:
            log(__name__, "No archive content received")
            return

        # v1.0.8: Use os.path for directory operations instead of xbmcvfs.exists()
        # which requires trailing separator for directories and is unreliable
        try:
            dir_path = xbmcvfs.translatePath("special://temp/subsro")
        except Exception:
            dir_path = xbmc.translatePath("special://temp/subsro")

        # v1.0.8: Use os.path.isdir() instead of xbmcvfs.exists() for directories
        # xbmcvfs.exists() requires trailing path separator for directories and
        # returns False without it, causing cleanup to be skipped
        if os.path.isdir(dir_path):
            # Clean up old files
            try:
                for f in os.listdir(dir_path):
                    fpath = os.path.join(dir_path, f)
                    if os.path.isfile(fpath):
                        os.remove(fpath)
            except Exception as e:
                log(__name__, "Cleanup failed: {}".format(e))

        if not os.path.isdir(dir_path):
            os.makedirs(dir_path)

        log(__name__, "Extraction dir: {}".format(dir_path))

        try:
            # v1.0.5: Pass season/episode for TV show episode-aware extraction
            extracted_path = extract_subtitle(
                archive_content, dir_path,
                season=season, episode=episode
            )
            if extracted_path:
                log(__name__, "Subtitle extracted successfully: {}".format(extracted_path))
                list_item = xbmcgui.ListItem(label=extracted_path)
                xbmcplugin.addDirectoryItem(
                    handle=self.handle, url=extracted_path,
                    listitem=list_item, isFolder=False
                )
            else:
                # v1.0.8: If extraction returns None, try saving raw content as SRT
                # (some subtitles on subs.ro are plain SRT, not archived)
                log(__name__, "Extraction returned None - archive may be empty or corrupt")
                fallback_path = os.path.join(
                    dir_path, "TempSubtitle.{}.{}".format(language, self.sub_format)
                )
                try:
                    with open(fallback_path, "wb") as f:
                        f.write(archive_content)
                    log(__name__, "Saved raw content as fallback: {}".format(fallback_path))
                    list_item = xbmcgui.ListItem(label=fallback_path)
                    xbmcplugin.addDirectoryItem(
                        handle=self.handle, url=fallback_path,
                        listitem=list_item, isFolder=False
                    )
                except Exception as e:
                    log(__name__, "Fallback save failed: {}".format(e))
        except Exception as e:
            log(__name__, "Archive extraction failed: {}".format(e))
            # v1.0.8: Do NOT call addDirectoryItem with non-existent path
            # when extraction fails -- this causes Kodi "Attempt failed" error.
            # Instead, just return and let endOfDirectory signal no subtitle.

    def list_subtitles(self):
        """Display subtitle results as Kodi ListItems."""
        if self.subtitles:
            # v1.0.5: Get season/episode from query for passing to download URL
            season = self.query.get("season_number", "")
            episode = self.query.get("episode_number", "")

            for subtitle in self.subtitles:
                language_code = subtitle.get("language", "ro")
                language_name = SUBSRO_TO_LANG.get(language_code, "Romanian")
                flag_code = SUBSRO_TO_FLAG.get(language_code, "ro")

                title = subtitle.get("title", "")
                release = subtitle.get("release", "")
                description = subtitle.get("description", "")
                translator = subtitle.get("translator", "")
                year = subtitle.get("year", "")

                # v1.0.5: Use description as fallback when release is empty
                # (subs.ro API returns description but not release for TV shows)
                display_release = release or description
                clean_name = clean_feature_release_name(title, display_release)
                if translator:
                    clean_name = "{} [{}]".format(clean_name, translator)
                if year:
                    clean_name = "{} ({})".format(clean_name, year)

                list_item = xbmcgui.ListItem(
                    label=language_name,
                    label2=clean_name
                )

                rating = subtitle.get("ratings") or subtitle.get("downloads") or 0
                try:
                    rating_icon = str(min(5, max(0, int(round(float(rating) / 2)))))
                except (ValueError, TypeError):
                    rating_icon = "5"

                list_item.setArt({
                    "icon": rating_icon,
                    "thumb": flag_code
                })

                list_item.setProperty("sync", "false")
                list_item.setProperty("hearing_imp", "false")

                subtitle_id = subtitle.get("id", "")
                # v1.0.5: Include season/episode in download URL for archive extraction
                url = "plugin://{}/?action=download&id={}&language={}".format(
                    __scriptid__, subtitle_id, language_code
                )
                if season and episode:
                    url += "&season={}&episode={}".format(season, episode)

                log(__name__, "Adding: {} - {} (id={})".format(
                    language_name, clean_name, subtitle_id))
                xbmcplugin.addDirectoryItem(
                    handle=self.handle, url=url, listitem=list_item, isFolder=False
                )
        # endOfDirectory is called by service.py -- NOT here
