from requests import Session, ConnectionError, HTTPError, ReadTimeout, Timeout

from resources.lib.exceptions import (
    AuthenticationError, ConfigurationError, DownloadLimitExceeded,
    ProviderError, ServiceUnavailable, TooManyRequests
)
from resources.lib.cache import Cache
from resources.lib.utilities import log

API_URL = "https://api.subs.ro/v1.0"
API_SEARCH = "/search/{field}/{value}"
API_DOWNLOAD = "/subtitle/{id}/download"
API_QUOTA = "/quota"

USER_AGENT = "Kodi Subs.ro v1.0.0"
CONTENT_TYPE = "application/json"
REQUEST_TIMEOUT = 30

# Mapping from ISO 639-1 codes (returned by Kodi/data_collector) to subs.ro codes
ISO_TO_SUBSRO = {
    "ro": "ro",
    "en": "en",
    "it": "ita",
    "fr": "fra",
    "de": "ger",
    "hu": "ung",
    "el": "gre",
    "pt": "por",
    "es": "spa",
    "pt-br": "por",
    "pt-pt": "por",
    "pb": "por",
    "zh-cn": "alt",
    "zh-tw": "alt",
}

# subs.ro language codes to Kodi language names (for display)
SUBSRO_TO_LANG = {
    "ro": "Romanian",
    "en": "English",
    "ita": "Italian",
    "fra": "French",
    "ger": "German",
    "ung": "Hungarian",
    "gre": "Greek",
    "por": "Portuguese",
    "spa": "Spanish",
    "alt": "Unknown",
}

# subs.ro language codes to ISO 639-1 flag codes (for Kodi flag display)
SUBSRO_TO_FLAG = {
    "ro": "ro",
    "en": "en",
    "ita": "it",
    "fra": "fr",
    "ger": "de",
    "ung": "hu",
    "gre": "el",
    "por": "pt",
    "spa": "es",
    "alt": "un",
}


def iso_to_subsro(iso_code):
    """Convert ISO 639-1 code to subs.ro language code."""
    if not iso_code:
        return None
    iso_lower = iso_code.lower().strip()
    return ISO_TO_SUBSRO.get(iso_lower)  # None if unsupported — filtered out by caller


def logging(msg):
    return log(__name__, msg)


class SubsroProvider:

    def __init__(self, api_key):

        if not api_key:
            raise ConfigurationError("Api_key must be specified")

        self.api_key = api_key

        self.request_headers = {
            "X-Subs-Api-Key": self.api_key,
            "User-Agent": USER_AGENT,
            "Content-Type": CONTENT_TYPE,
            "Accept": CONTENT_TYPE,
        }

        self.session = Session()
        self.session.headers = self.request_headers

        self.cache = Cache(key_prefix="subsro")

    def search_subtitles(self, query):
        """
        Search for subtitles using the subs.ro API.

        Args:
            query: Dict containing media_data + file_data + language_data.
                   Keys: query, year, season_number, episode_number,
                         tv_show_title, imdb_id, parent_imdb_id,
                         tmdb_id, parent_tmdb_id, languages (comma-separated ISO codes)

        Returns:
            List of subtitle result dicts, or None if none found.
        """
        logging("Searching subtitles with query: %s" % query)

        # Determine primary search field and value
        search_field = None
        search_value = None

        if query.get("imdb_id") and not query.get("tv_show_title"):
            search_field = "imdbid"
            search_value = str(query["imdb_id"])
        elif query.get("parent_imdb_id") and query.get("tv_show_title"):
            search_field = "imdbid"
            search_value = str(query["parent_imdb_id"])
        elif query.get("tmdb_id") and not query.get("tv_show_title"):
            search_field = "tmdbid"
            search_value = str(query["tmdb_id"])
        elif query.get("parent_tmdb_id") and query.get("tv_show_title"):
            search_field = "tmdbid"
            search_value = str(query["parent_tmdb_id"])
        elif query.get("query"):
            search_field = "title"
            search_value = str(query["query"])

        if not search_field or not search_value:
            logging("No valid search parameters found")
            return None

        logging("Search: {}={}".format(search_field, search_value))

        # Convert requested ISO languages to subs.ro codes
        languages_str = query.get("languages", "")
        subsro_languages = []
        if languages_str:
            for lang in languages_str.split(","):
                lang = lang.strip()
                if lang:
                    subsro_lang = iso_to_subsro(lang)
                    if subsro_lang and subsro_lang not in subsro_languages:
                        subsro_languages.append(subsro_lang)
        logging("Requested subs.ro languages: {}".format(subsro_languages))

        # Fetch results — search per language for precise results
        all_results = []
        if subsro_languages:
            for lang in subsro_languages:
                results = self._search_api(search_field, search_value, language=lang)
                if results:
                    all_results.extend(results)
        else:
            results = self._search_api(search_field, search_value)
            if results:
                all_results.extend(results)

        if not all_results:
            logging("No subtitles found")
            return None

        # Filter by season/episode for TV shows
        if query.get("tv_show_title"):
            season = query.get("season_number", "")
            episode = query.get("episode_number", "")
            if season and episode:
                all_results = self._filter_tv_results(all_results, season, episode)

        logging("Total results: {}".format(len(all_results)))
        return all_results if all_results else None

    def _search_api(self, field, value, language=None):
        """Make a search API call to subs.ro."""
        from urllib.parse import quote, urlencode

        encoded_value = quote(str(value), safe="")
        url = "{}/search/{}/{}".format(API_URL, field, encoded_value)

        params = {}
        if language:
            params["language"] = language
        if params:
            url += "?" + urlencode(params)

        logging("API search: GET {}".format(url))

        try:
            r = self.session.get(url, timeout=REQUEST_TIMEOUT)
            logging("Search response status: {}".format(r.status_code))
            r.raise_for_status()
        except (ConnectionError, Timeout, ReadTimeout) as e:
            raise ServiceUnavailable("Connection error: {}".format(e))
        except HTTPError as e:
            status_code = e.response.status_code
            logging("HTTP error during search: {}".format(status_code))
            if status_code in (401, 403):
                raise AuthenticationError("Invalid API key: {}".format(e))
            elif status_code == 429:
                raise TooManyRequests()
            elif status_code == 503:
                raise ServiceUnavailable("Service unavailable: {}".format(e))
            else:
                raise ProviderError("HTTP {}: {}".format(status_code, e))

        try:
            result = r.json()
            logging("Search response JSON keys: {}".format(list(result.keys()) if result else None))
        except ValueError:
            raise ProviderError("Invalid JSON response from subs.ro")

        results = result.get("results", [])
        logging("API returned {} results".format(len(results)))
        return results

    def _filter_tv_results(self, results, season, episode):
        """Filter TV show results by season/episode number."""
        import re
        filtered = []
        try:
            season_int = int(season)
            episode_int = int(episode)
        except (ValueError, TypeError):
            return results

        for result in results:
            res_season = result.get("season")
            res_episode = result.get("episode")

            if res_season is not None and res_episode is not None:
                try:
                    if int(res_season) == season_int and int(res_episode) == episode_int:
                        filtered.append(result)
                        continue
                except (ValueError, TypeError):
                    pass

            release = result.get("release", "") or result.get("title", "")
            if release:
                pattern = r"[Ss]{s:02d}[Ee]{e:02d}|{s}x{e:02d}".format(
                    s=season_int, e=episode_int
                )
                if re.search(pattern, release, re.IGNORECASE):
                    filtered.append(result)
                    continue

            if res_season is None and res_episode is None:
                filtered.append(result)

        return filtered if filtered else results

    def download_subtitle(self, subtitle_id):
        """
        Download subtitle archive from subs.ro.

        Args:
            subtitle_id: The subs.ro subtitle ID.

        Returns:
            Raw bytes of the archive (zip/rar).

        Raises:
            DownloadLimitExceeded: If daily quota is exceeded.
            AuthenticationError: If API key is invalid.
            ProviderError: On other errors.
        """
        from urllib.parse import quote

        url = "{}/subtitle/{}/download".format(API_URL, quote(str(subtitle_id), safe=""))
        logging("Downloading subtitle {}: GET {}".format(subtitle_id, url))

        try:
            r = self.session.get(url, timeout=REQUEST_TIMEOUT)
            logging("Download response status: {}".format(r.status_code))
            r.raise_for_status()
        except (ConnectionError, Timeout, ReadTimeout) as e:
            raise ServiceUnavailable("Connection error: {}".format(e))
        except HTTPError as e:
            status_code = e.response.status_code
            if status_code in (401, 403):
                raise AuthenticationError("Invalid API key: {}".format(e))
            elif status_code == 429:
                raise DownloadLimitExceeded("Daily download quota exceeded")
            elif status_code == 503:
                raise ServiceUnavailable("Service unavailable: {}".format(e))
            else:
                raise ProviderError("HTTP {}: {}".format(status_code, e))

        content = r.content
        if not content:
            raise ProviderError("Empty response from download endpoint")

        logging("Downloaded {} bytes".format(len(content)))
        return content
