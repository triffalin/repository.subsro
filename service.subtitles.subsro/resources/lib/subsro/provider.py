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

USER_AGENT = "Kodi Subs.ro v1.0.5"
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


def _ensure_tt_prefix(imdb_id):
    """
    Ensure IMDB ID has the 'tt' prefix required by subs.ro API.

    The subs.ro API requires IMDB IDs in the format 'tt1234567'.
    Kodi/data_collector strips the 'tt' prefix and stores just the numeric part,
    so we need to add it back before sending to the API.
    """
    if not imdb_id:
        return None
    s = str(imdb_id).strip()
    if s.startswith("tt"):
        return s
    # Pad to at least 7 digits (IMDB standard)
    if s.isdigit():
        return "tt{}".format(s.zfill(7))
    return None


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
                         episode_imdb_id, tmdb_id, parent_tmdb_id,
                         languages (comma-separated ISO codes)

        Returns:
            List of subtitle result dicts, or None if none found.
        """
        logging("Searching subtitles with query: %s" % query)

        is_tv_show = bool(query.get("tv_show_title"))
        season = query.get("season_number", "")
        episode = query.get("episode_number", "")

        # Determine primary search field and value
        search_field = None
        search_value = None

        if is_tv_show:
            # TV SHOW SEARCH STRATEGY:
            # 1. Primary: parent (show) IMDB ID - returns all subtitles for the show
            # 2. Fallback: episode-specific IMDB ID
            # 3. Fallback: parent TMDB ID
            # 4. Fallback: title search
            if query.get("parent_imdb_id"):
                search_field = "imdbid"
                search_value = _ensure_tt_prefix(query["parent_imdb_id"])
            elif query.get("episode_imdb_id"):
                search_field = "imdbid"
                search_value = _ensure_tt_prefix(query["episode_imdb_id"])
            elif query.get("imdb_id"):
                search_field = "imdbid"
                search_value = _ensure_tt_prefix(query["imdb_id"])
            elif query.get("parent_tmdb_id"):
                search_field = "tmdbid"
                search_value = str(query["parent_tmdb_id"])
            elif query.get("query"):
                search_field = "title"
                search_value = str(query["query"])
        else:
            # MOVIE SEARCH STRATEGY (unchanged from v1.0.4)
            if query.get("imdb_id"):
                search_field = "imdbid"
                search_value = _ensure_tt_prefix(query["imdb_id"])
            elif query.get("tmdb_id"):
                search_field = "tmdbid"
                search_value = str(query["tmdb_id"])
            elif query.get("query"):
                search_field = "title"
                search_value = str(query["query"])

        if not search_field or not search_value:
            logging("No valid search parameters found")
            return None

        logging("Search: {}={} (is_tv_show={})".format(search_field, search_value, is_tv_show))

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

        # Fetch results -- search per language for precise results
        all_results = self._fetch_with_language_fallback(
            search_field, search_value, subsro_languages
        )

        # FALLBACK 1: For TV shows, if parent IMDB search returned nothing,
        # try episode-specific IMDB ID
        if not all_results and is_tv_show and search_field == "imdbid":
            episode_imdb = query.get("episode_imdb_id") or query.get("imdb_id")
            if episode_imdb:
                ep_tt = _ensure_tt_prefix(episode_imdb)
                if ep_tt and ep_tt != search_value:
                    logging("TV fallback: trying episode IMDB ID: {}".format(ep_tt))
                    all_results = self._fetch_with_language_fallback(
                        "imdbid", ep_tt, subsro_languages
                    )

        # FALLBACK 2: If IMDB/TMDB search returned nothing, try title search
        if not all_results and search_field in ("imdbid", "tmdbid") and query.get("query"):
            title_query = str(query["query"])
            # For TV shows, append season info to title for better matching
            if is_tv_show and season and episode:
                try:
                    title_with_se = "{} S{:02d}E{:02d}".format(
                        title_query, int(season), int(episode)
                    )
                except (ValueError, TypeError):
                    title_with_se = title_query
                logging("Title fallback with S/E: {}".format(title_with_se))
                all_results = self._fetch_with_language_fallback(
                    "title", title_with_se, subsro_languages
                )

            # Try plain title if S/E title didn't work
            if not all_results:
                logging("Title fallback (plain): {}".format(title_query))
                all_results = self._fetch_with_language_fallback(
                    "title", title_query, subsro_languages
                )

        if not all_results:
            logging("No subtitles found")
            return None

        # Filter by season/episode for TV shows
        if is_tv_show and season and episode:
            all_results = self._filter_tv_results(all_results, season, episode)
            logging("After TV filtering: {} results".format(len(all_results)))

        logging("Total results: {}".format(len(all_results)))
        return all_results if all_results else None

    def _fetch_with_language_fallback(self, field, value, subsro_languages):
        """Search API with language-specific queries, falling back to no language filter."""
        all_results = []
        if subsro_languages:
            for lang in subsro_languages:
                results = self._search_api(field, value, language=lang)
                if results:
                    all_results.extend(results)

            # Fallback: if language-specific search returned nothing, try without language filter
            if not all_results:
                logging("No results with language filter, retrying without language parameter")
                results = self._search_api(field, value)
                if results:
                    all_results.extend(results)
        else:
            results = self._search_api(field, value)
            if results:
                all_results.extend(results)
        return all_results

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
            logging("Search response JSON keys: {}".format(list(result.keys()) if isinstance(result, dict) else type(result).__name__))
        except ValueError:
            raise ProviderError("Invalid JSON response from subs.ro")

        # subs.ro API returns subtitle list in the "items" field (not "results")
        # Also handle "results" as fallback for forward-compatibility
        if isinstance(result, dict):
            results = result.get("items") or result.get("results") or []
        elif isinstance(result, list):
            # In case the API returns a bare list
            results = result
        else:
            results = []

        logging("API returned {} results".format(len(results)))

        # Log first result for debugging (helps identify response structure)
        if results and isinstance(results[0], dict):
            logging("First result keys: {}".format(list(results[0].keys())))
            # Log title and description of first few results for TV debugging
            for i, r_item in enumerate(results[:3]):
                logging("Result[{}]: title='{}' description='{}' type='{}'".format(
                    i,
                    r_item.get("title", ""),
                    (r_item.get("description", "") or "")[:120],
                    r_item.get("type", "")
                ))

        return results

    def _filter_tv_results(self, results, season, episode):
        """
        Filter TV show results by season/episode number.

        The subs.ro API does NOT return 'season', 'episode', or 'release' fields.
        Available fields: id, title, year, description, link, downloadLink,
                         imdbid, tmdbid, poster, translator, language, type.

        Strategy:
        1. Check 'title' and 'description' for S01E05 / 1x05 patterns
        2. Check for season-only match (e.g., "Season 1" or "S01")
        3. If no matches at any level, return ALL results (better than nothing)

        This ensures the user always sees relevant subtitles.
        """
        import re
        try:
            season_int = int(season)
            episode_int = int(episode)
        except (ValueError, TypeError):
            logging("TV filter: invalid season/episode, returning all results")
            return results

        logging("TV filter: looking for S{:02d}E{:02d} in {} results".format(
            season_int, episode_int, len(results)))

        # Build regex patterns for exact episode match
        # Matches: S01E05, s01e05, S1E5, 1x05, 01x05
        exact_episode_patterns = [
            r"[Ss]0?{s}[Ee]0?{e}(?:\b|[^0-9])".format(s=season_int, e=episode_int),
            r"\b0?{s}[xX]0?{e:02d}\b".format(s=season_int, e=episode_int),
        ]
        # Also match Romanian-style: "Sezonul 1 Episodul 5", "Sezon 1 Ep 5"
        exact_episode_patterns.append(
            r"[Ss]ezon(?:ul)?\s*0?{s}\s*[Ee]pisod(?:ul)?\s*0?{e}".format(
                s=season_int, e=episode_int)
        )
        # Also match "Season 1 Episode 5"
        exact_episode_patterns.append(
            r"[Ss]eason\s*0?{s}\s*[Ee]pisode\s*0?{e}".format(
                s=season_int, e=episode_int)
        )

        # Build patterns for season-only match (less precise)
        season_only_patterns = [
            r"[Ss]0?{s}[Ee]".format(s=season_int),          # S01E (any episode)
            r"[Ss]ezon(?:ul)?\s*0?{s}\b".format(s=season_int),  # Sezonul 1
            r"[Ss]eason\s*0?{s}\b".format(s=season_int),    # Season 1
            r"\b0?{s}[xX]\d".format(s=season_int),          # 1x (any episode)
        ]

        exact_matches = []
        season_matches = []
        no_season_info = []

        for result in results:
            # Combine all text fields that might contain season/episode info
            searchable_parts = []
            for field in ("title", "description", "link", "downloadLink"):
                val = result.get(field, "")
                if val:
                    searchable_parts.append(str(val))
            searchable_text = " ".join(searchable_parts)

            # 1. Try exact episode match
            found_exact = False
            for pattern in exact_episode_patterns:
                if re.search(pattern, searchable_text):
                    exact_matches.append(result)
                    found_exact = True
                    logging("TV filter: EXACT match for S{:02d}E{:02d} in '{}'".format(
                        season_int, episode_int, result.get("title", "")[:80]))
                    break

            if found_exact:
                continue

            # 2. Try season-only match
            found_season = False
            for pattern in season_only_patterns:
                if re.search(pattern, searchable_text):
                    season_matches.append(result)
                    found_season = True
                    logging("TV filter: SEASON match for S{:02d} in '{}'".format(
                        season_int, result.get("title", "")[:80]))
                    break

            if found_season:
                continue

            # 3. No season/episode info detected - could be a full-series pack
            # Check if there's any S__E__ pattern at all; if none, it's untagged
            has_any_season = re.search(
                r"[Ss]\d+[Ee]\d+|\d+[xX]\d+|[Ss]ezon|[Ss]eason", searchable_text
            )
            if not has_any_season:
                no_season_info.append(result)
                logging("TV filter: NO season info in '{}' - may be full pack".format(
                    result.get("title", "")[:80]))

        # Return results with cascading priority:
        # 1. Exact episode matches (best)
        if exact_matches:
            logging("TV filter: returning {} exact episode matches".format(len(exact_matches)))
            return exact_matches

        # 2. Season matches (good - user can pick the right one)
        if season_matches:
            logging("TV filter: returning {} season matches".format(len(season_matches)))
            return season_matches

        # 3. Untagged results (might be complete packs)
        if no_season_info:
            logging("TV filter: returning {} untagged results (possible full packs)".format(
                len(no_season_info)))
            return no_season_info

        # 4. ALL results as last resort (better than nothing)
        logging("TV filter: no matches at any level, returning all {} results".format(
            len(results)))
        return results

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
