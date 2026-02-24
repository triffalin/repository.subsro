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

USER_AGENT = "Kodi Subs.ro v1.0.6"
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

# Language display priority for ranking (lower = better)
LANGUAGE_PRIORITY = {
    "ro": 0,    # Romanian always first
    "en": 1,    # English second
    "ita": 5,
    "fra": 5,
    "ger": 5,
    "ung": 5,
    "gre": 5,
    "por": 5,
    "spa": 5,
    "alt": 10,
}


def iso_to_subsro(iso_code):
    """Convert ISO 639-1 code to subs.ro language code."""
    if not iso_code:
        return None
    iso_lower = iso_code.lower().strip()
    return ISO_TO_SUBSRO.get(iso_lower)  # None if unsupported -- filtered out by caller


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
        Search for subtitles using multi-strategy cascading search.

        v1.0.6: Research-based improvements inspired by VLC/BSPlayer/a4kSubtitles.
        Instead of a single search with fallbacks, we try up to 10 strategies
        in priority order and collect all results, then rank and deduplicate.

        Strategies for TV shows:
          1. Parent show IMDB ID (with language filter)
          2. Episode-specific IMDB ID (with language filter)
          3. Parent show TMDB ID (with language filter)
          4. Episode-specific TMDB ID (with language filter)
          5. Title + S01E05 pattern (with language filter)
          6. Plain title search (with language filter)
          7. Episode name/title search (with language filter)
          8. Original title search (with language filter)
          9. Release name search (with language filter)
          10. No-language fallback: repeat best ID strategy without language filter

        For movies: IMDB ID -> TMDB ID -> title -> original title -> release name

        Args:
            query: Dict containing media_data + file_data + language_data.

        Returns:
            List of subtitle result dicts, or None if none found.
        """
        logging("=== v1.0.6 Multi-Strategy Search ===")
        logging("Query: %s" % query)

        is_tv_show = bool(query.get("tv_show_title"))
        season = query.get("season_number", "")
        episode = query.get("episode_number", "")

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

        # Gather all available search parameters
        parent_imdb = _ensure_tt_prefix(query.get("parent_imdb_id"))
        episode_imdb = _ensure_tt_prefix(query.get("episode_imdb_id") or query.get("imdb_id"))
        parent_tmdb = str(query.get("parent_tmdb_id")) if query.get("parent_tmdb_id") else None
        episode_tmdb = str(query.get("tmdb_id")) if query.get("tmdb_id") else None
        title = str(query.get("query", "")).strip() if query.get("query") else None
        original_title = str(query.get("original_title", "")).strip() if query.get("original_title") else None
        episode_title = str(query.get("episode_title", "")).strip() if query.get("episode_title") else None
        file_basename = str(query.get("basename", "")).strip() if query.get("basename") else None

        # Avoid duplicate IMDb IDs (sometimes episode_imdb == parent_imdb)
        if episode_imdb and episode_imdb == parent_imdb:
            episode_imdb = None

        # Build ordered list of search strategies
        strategies = []

        if is_tv_show:
            # Strategy 1: Parent show IMDB ID
            if parent_imdb:
                strategies.append(("imdbid", parent_imdb, "parent_imdb"))

            # Strategy 2: Episode-specific IMDB ID
            if episode_imdb:
                strategies.append(("imdbid", episode_imdb, "episode_imdb"))

            # Strategy 3: Parent show TMDB ID
            if parent_tmdb:
                strategies.append(("tmdbid", parent_tmdb, "parent_tmdb"))

            # Strategy 4: Episode-specific TMDB ID
            if episode_tmdb:
                strategies.append(("tmdbid", episode_tmdb, "episode_tmdb"))

            # Strategy 5: Title + S01E05 pattern
            if title and season and episode:
                try:
                    title_with_se = "{} S{:02d}E{:02d}".format(
                        title, int(season), int(episode)
                    )
                    strategies.append(("title", title_with_se, "title_with_SE"))
                except (ValueError, TypeError):
                    pass

            # Strategy 6: Plain title search
            if title:
                strategies.append(("title", title, "plain_title"))

            # Strategy 7: Episode name/title search (e.g., "Ozymandias")
            if episode_title and episode_title != title:
                strategies.append(("title", episode_title, "episode_title"))
                # Also try show + episode name combo
                if title:
                    combo = "{} {}".format(title, episode_title)
                    strategies.append(("title", combo, "title_plus_episode_name"))

            # Strategy 8: Original title (if different from display title)
            if original_title and original_title != title:
                strategies.append(("title", original_title, "original_title"))
                # Also try original title + S01E05
                if season and episode:
                    try:
                        orig_with_se = "{} S{:02d}E{:02d}".format(
                            original_title, int(season), int(episode)
                        )
                        strategies.append(("title", orig_with_se, "original_title_with_SE"))
                    except (ValueError, TypeError):
                        pass

            # Strategy 9: Release/filename search
            if file_basename:
                strategies.append(("release", file_basename, "release_name"))

        else:
            # MOVIE search strategies
            if query.get("imdb_id"):
                strategies.append(("imdbid", _ensure_tt_prefix(query["imdb_id"]), "movie_imdb"))
            if query.get("tmdb_id"):
                strategies.append(("tmdbid", str(query["tmdb_id"]), "movie_tmdb"))
            if title:
                strategies.append(("title", title, "movie_title"))
            if original_title and original_title != title:
                strategies.append(("title", original_title, "movie_original_title"))
            if file_basename:
                strategies.append(("release", file_basename, "movie_release"))

        if not strategies:
            logging("No valid search strategies could be built")
            return None

        logging("Built {} search strategies".format(len(strategies)))

        # Execute strategies in order -- stop once we get results
        all_results = []
        successful_strategy = None

        for field, value, strategy_name in strategies:
            if not value:
                continue

            logging("--- Strategy '{}': {field}={value} ---".format(
                strategy_name, field=field, value=value))

            results = self._fetch_with_languages(field, value, subsro_languages)

            if results:
                logging("Strategy '{}' returned {} results".format(
                    strategy_name, len(results)))
                all_results = results
                successful_strategy = strategy_name
                break
            else:
                logging("Strategy '{}' returned 0 results".format(strategy_name))

        # Strategy 10: No-language fallback
        # If all language-specific strategies returned nothing, retry the best
        # ID-based strategy without any language filter to find ALL available subtitles
        if not all_results and subsro_languages:
            logging("=== No-language fallback: retrying all strategies without language filter ===")
            for field, value, strategy_name in strategies:
                if not value:
                    continue

                logging("--- No-lang fallback '{}': {field}={value} ---".format(
                    strategy_name, field=field, value=value))

                results = self._search_api(field, value)
                if results:
                    logging("No-lang fallback '{}' returned {} results".format(
                        strategy_name, len(results)))
                    all_results = results
                    successful_strategy = strategy_name + "_nolang"
                    break

        if not all_results:
            logging("No subtitles found across all {} strategies".format(len(strategies)))
            return None

        logging("Results from strategy '{}': {} total".format(
            successful_strategy, len(all_results)))

        # Deduplicate results by subtitle ID
        all_results = self._deduplicate(all_results)
        logging("After deduplication: {} results".format(len(all_results)))

        # Filter by season/episode for TV shows
        if is_tv_show and season and episode:
            all_results = self._filter_tv_results(
                all_results, season, episode,
                episode_title=episode_title
            )
            logging("After TV filtering: {} results".format(len(all_results)))

        # Rank results: language match > episode match > downloads > date
        all_results = self._rank_results(all_results, subsro_languages)
        logging("Final ranked results: {}".format(len(all_results)))

        return all_results if all_results else None

    def _fetch_with_languages(self, field, value, subsro_languages):
        """
        Search API with language-specific queries, collecting results from all languages.
        Falls back to no language filter if language-specific searches return nothing.
        """
        all_results = []

        if subsro_languages:
            for lang in subsro_languages:
                results = self._search_api(field, value, language=lang)
                if results:
                    all_results.extend(results)

        if not all_results:
            # Try without language filter as final fallback within this strategy
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
            elif status_code == 404:
                # Not found is normal for search -- not an error
                logging("404 - no results for this search")
                return []
            else:
                raise ProviderError("HTTP {}: {}".format(status_code, e))

        try:
            result = r.json()
            logging("Search response JSON keys: {}".format(
                list(result.keys()) if isinstance(result, dict) else type(result).__name__))
        except ValueError:
            raise ProviderError("Invalid JSON response from subs.ro")

        # subs.ro API returns subtitle list in the "items" field (not "results")
        # Also handle "results" as fallback for forward-compatibility
        if isinstance(result, dict):
            results = result.get("items") or result.get("results") or []
        elif isinstance(result, list):
            results = result
        else:
            results = []

        logging("API returned {} results".format(len(results)))

        # Log first result for debugging (helps identify response structure)
        if results and isinstance(results[0], dict):
            logging("First result keys: {}".format(list(results[0].keys())))
            for i, r_item in enumerate(results[:3]):
                logging("Result[{}]: title='{}' description='{}' type='{}' lang='{}'".format(
                    i,
                    r_item.get("title", ""),
                    (r_item.get("description", "") or "")[:120],
                    r_item.get("type", ""),
                    r_item.get("language", "")
                ))

        return results

    def _deduplicate(self, results):
        """Remove duplicate results by subtitle ID."""
        seen_ids = set()
        unique = []
        for r in results:
            sub_id = r.get("id")
            if sub_id and sub_id in seen_ids:
                continue
            if sub_id:
                seen_ids.add(sub_id)
            unique.append(r)
        return unique

    def _rank_results(self, results, requested_languages):
        """
        Rank subtitle results by relevance.

        Priority order:
        1. Language match (requested languages first, Romanian always preferred)
        2. Download count (higher = better, more popular = likely better quality)
        3. Upload date (newer = better)
        """
        requested_set = set(requested_languages) if requested_languages else set()
        # Always prioritize Romanian
        if not requested_set:
            requested_set = {"ro", "en"}

        def sort_key(item):
            lang = item.get("language", "")

            # Language priority: requested > other
            if lang in requested_set:
                lang_score = LANGUAGE_PRIORITY.get(lang, 5)
            else:
                lang_score = 50  # Non-requested languages ranked last

            # Download count (negate for descending sort)
            try:
                downloads = -int(item.get("downloads", 0))
            except (ValueError, TypeError):
                downloads = 0

            return (lang_score, downloads)

        results.sort(key=sort_key)
        return results

    def _filter_tv_results(self, results, season, episode, episode_title=None):
        """
        Filter TV show results by season/episode number.

        v1.0.6: Enhanced with episode title matching.
        When S01E05 patterns are not found in results, also try matching
        by the episode's actual name (e.g., "Ozymandias" for Breaking Bad S05E14).

        The subs.ro API does NOT return 'season', 'episode', or 'release' fields.
        Available fields: id, title, year, description, link, downloadLink,
                         imdbid, tmdbid, poster, translator, language, type.

        Strategy:
        1. Check 'title' and 'description' for S01E05 / 1x05 patterns
        2. Check for episode title/name match in title or description
        3. Check for season-only match (e.g., "Season 1" or "S01")
        4. If no matches at any level, return ALL results (better than nothing)
        """
        import re
        try:
            season_int = int(season)
            episode_int = int(episode)
        except (ValueError, TypeError):
            logging("TV filter: invalid season/episode, returning all results")
            return results

        logging("TV filter: looking for S{:02d}E{:02d} in {} results{}".format(
            season_int, episode_int, len(results),
            " (episode_title='{}')".format(episode_title) if episode_title else ""))

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
        # Match "E05" or "Ep05" or "Ep.05" standalone (for season-specific archives)
        exact_episode_patterns.append(
            r"(?:^|[^0-9a-zA-Z])[Ee](?:p\.?)?\s*0?{e}(?:\b|[^0-9])".format(
                e=episode_int)
        )

        # Build patterns for season-only match (less precise)
        season_only_patterns = [
            r"[Ss]0?{s}[Ee]".format(s=season_int),          # S01E (any episode)
            r"[Ss]ezon(?:ul)?\s*0?{s}\b".format(s=season_int),  # Sezonul 1
            r"[Ss]eason\s*0?{s}\b".format(s=season_int),    # Season 1
            r"\b0?{s}[xX]\d".format(s=season_int),          # 1x (any episode)
        ]

        # Prepare episode title for matching (if available)
        episode_title_lower = episode_title.lower().strip() if episode_title else None
        # Only use episode title matching if title is long enough to be meaningful
        use_episode_title = episode_title_lower and len(episode_title_lower) >= 3

        exact_matches = []
        episode_name_matches = []
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

            # 1. Try exact episode match (S01E05 pattern)
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

            # 2. Try episode title/name match (e.g., "Ozymandias" in description)
            if use_episode_title:
                searchable_lower = searchable_text.lower()
                if episode_title_lower in searchable_lower:
                    episode_name_matches.append(result)
                    logging("TV filter: EPISODE NAME match '{}' in '{}'".format(
                        episode_title_lower, result.get("title", "")[:80]))
                    continue

            # 3. Try season-only match
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

            # 4. No season/episode info detected - could be a full-series pack
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

        # 2. Episode name matches (good - matched by episode title)
        if episode_name_matches:
            logging("TV filter: returning {} episode name matches".format(len(episode_name_matches)))
            return episode_name_matches

        # 3. Season matches (decent - user can pick the right one)
        if season_matches:
            logging("TV filter: returning {} season matches".format(len(season_matches)))
            return season_matches

        # 4. Untagged results (might be complete packs)
        if no_season_info:
            logging("TV filter: returning {} untagged results (possible full packs)".format(
                len(no_season_info)))
            return no_season_info

        # 5. ALL results as last resort (better than nothing)
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
