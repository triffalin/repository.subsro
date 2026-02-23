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

        try:
            self.subsro = SubsroProvider(self.api_key)
        except ConfigurationError as e:
            error(__name__, 32002, e)

    def handle_action(self):
        log(__name__, "action '%s' called" % self.params["action"])
        if self.params["action"] == "manualsearch":
            self.search(self.params.get("searchstring", ""))
        elif self.params["action"] == "search":
            self.search()
        elif self.params["action"] == "download":
            self.download()

    def search(self, query=""):
        file_data = get_file_data(get_file_path())
        language_data = get_language_data(self.params)

        log(__name__, "file_data '%s' " % file_data)
        log(__name__, "language_data '%s' " % language_data)

        # if there's query passed we use it, don't try to pull media data from VideoPlayer
        if query:
            media_data = {"query": query}
        else:
            media_data = get_media_data()
            # Only use basename as fallback if no query was set by media data collection
            if "basename" in file_data and not media_data.get("query"):
                media_data["query"] = file_data["basename"]
                log(__name__, "Using basename as query fallback: {}".format(file_data["basename"]))
            elif media_data.get("query"):
                log(__name__, "Using parsed query from media_data: {}".format(media_data["query"]))
            log(__name__, "media_data '%s' " % media_data)

        self.query = {**media_data, **file_data, **language_data}

        try:
            self.subtitles = self.subsro.search_subtitles(self.query)
        except (TooManyRequests, ServiceUnavailable, ProviderError, ValueError) as e:
            error(__name__, 32001, e)

        if self.subtitles and len(self.subtitles):
            log(__name__, len(self.subtitles))
            self.list_subtitles()
        else:
            log(__name__, "No subtitle found")

    def download(self):
        valid = 1
        subtitle_id = self.params.get("id", "")
        language = self.params.get("language", "ro")

        try:
            archive_content = self.subsro.download_subtitle(subtitle_id)
            log(__name__, "Downloaded archive: {} bytes".format(len(archive_content)))
        except AuthenticationError as e:
            error(__name__, 32003, e)
            valid = 0
        except DownloadLimitExceeded as e:
            log(__name__, "Download limit exceeded: {}".format(e))
            error(__name__, 32004, e)
            valid = 0
        except (TooManyRequests, ServiceUnavailable, ProviderError, ValueError) as e:
            error(__name__, 32001, e)
            valid = 0
            archive_content = None

        try:
            dir_path = xbmcvfs.translatePath("special://temp/subsro")
        except Exception:
            dir_path = xbmc.translatePath("special://temp/subsro")

        if xbmcvfs.exists(dir_path):
            dirs, files = xbmcvfs.listdir(dir_path)
            for f in files:
                xbmcvfs.delete(os.path.join(dir_path, f))

        if not xbmcvfs.exists(dir_path):
            xbmcvfs.mkdir(dir_path)

        subtitle_path = os.path.join(dir_path, "TempSubtitle.{}.{}".format(language, self.sub_format))

        log(__name__, "Subtitle path: {}".format(subtitle_path))

        if valid == 1 and archive_content:
            try:
                extracted_path = extract_subtitle(archive_content, dir_path)
                if extracted_path:
                    subtitle_path = extracted_path
                    log(__name__, "Subtitle extracted: {}".format(subtitle_path))
                else:
                    log(__name__, "Failed to extract subtitle from archive")
                    valid = 0
            except Exception as e:
                log(__name__, "Archive extraction failed: {}".format(e))
                valid = 0

        list_item = xbmcgui.ListItem(label=subtitle_path)
        xbmcplugin.addDirectoryItem(handle=self.handle, url=subtitle_path, listitem=list_item, isFolder=False)

    def list_subtitles(self):
        """Display subtitle results as Kodi ListItems."""
        if self.subtitles:
            for subtitle in self.subtitles:
                language_code = subtitle.get("language", "ro")
                language_name = SUBSRO_TO_LANG.get(language_code, "Romanian")
                flag_code = SUBSRO_TO_FLAG.get(language_code, "ro")

                # Build display name
                title = subtitle.get("title", "")
                release = subtitle.get("release", "")
                translator = subtitle.get("translator", "")
                year = subtitle.get("year", "")

                clean_name = clean_feature_release_name(title, release)
                if translator:
                    clean_name = "{} [{}]".format(clean_name, translator)
                if year:
                    clean_name = "{} ({})".format(clean_name, year)

                list_item = xbmcgui.ListItem(
                    label=language_name,
                    label2=clean_name
                )

                # Rating icon (0-5 scale for Kodi)
                rating = subtitle.get("ratings") or subtitle.get("downloads") or 0
                try:
                    rating_icon = str(min(5, max(0, int(round(float(rating) / 2)))))
                except (ValueError, TypeError):
                    rating_icon = "5"

                list_item.setArt({
                    "icon": rating_icon,
                    "thumb": flag_code
                })

                list_item.setProperty("sync", "false")  # subs.ro does not support moviehash
                list_item.setProperty("hearing_imp", "false")

                subtitle_id = subtitle.get("id", "")
                url = "plugin://{}/?action=download&id={}&language={}".format(
                    __scriptid__, subtitle_id, language_code
                )

                log(__name__, "Adding: {} - {}".format(language_name, clean_name))
                xbmcplugin.addDirectoryItem(
                    handle=self.handle, url=url, listitem=list_item, isFolder=False
                )

        xbmcplugin.endOfDirectory(self.handle)
