# -*- coding: utf-8 -*-
"""
subs.ro web scraper -- fallback when API returns no results.

Scrapes the subs.ro website directly, similar to how POV player finds
subtitles that are uploaded but not yet properly indexed in the API.

This module is a SILENT fallback: all exceptions are caught internally
and never propagated to the caller. If scraping fails for any reason
(rate limit, network error, HTML structure change), it returns [].

Uses only standard library + requests (already a Kodi addon dependency).
Does NOT use BeautifulSoup or lxml -- parses HTML with regex only.

Website structure discovered via analysis:
- Browse movies: https://subs.ro/subtitrari/filme/pagina/1
- Browse series: https://subs.ro/subtitrari/seriale/pagina/1
- Subtitle page: https://subs.ro/subtitrare/{slug}/{id}
- Download link: https://subs.ro/subtitrare/descarca/{slug}/{id}
- IMDB link on page: //imdb.com/title/{tt_id}
- Flag images: https://cdn.subs.ro/img/flags/flag-{lang}-big.png
- AJAX search: https://subs.ro/ajax/search/ (POST)
"""

import re

from resources.lib.utilities import log

SUBS_RO_BASE = "https://subs.ro"
SCRAPE_TIMEOUT = 15
SCRAPE_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Map subs.ro flag codes to our internal language codes
FLAG_TO_LANG = {
    "rom": "ro",
    "ro": "ro",
    "eng": "en",
    "en": "en",
    "ita": "ita",
    "fra": "fra",
    "ger": "ger",
    "hun": "ung",
    "ung": "ung",
    "gre": "gre",
    "por": "por",
    "spa": "spa",
}


def _log(msg):
    return log(__name__, msg)


class SubsroScraper:
    """
    Web scraper for subs.ro website.

    Falls back to scraping the website when the API returns no results.
    All methods are safe -- they catch all exceptions and return [] on failure.
    """

    def __init__(self, session=None):
        """
        Initialize scraper.

        Args:
            session: Optional requests.Session to reuse (for connection pooling).
                     If None, creates a new session without API key headers.
        """
        if session:
            # Create a separate session for scraping (different headers)
            from requests import Session
            self.session = Session()
            # Do NOT send API key headers to website -- only to API
            self.session.headers = {
                "User-Agent": SCRAPE_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
            }
        else:
            from requests import Session
            self.session = Session()
            self.session.headers = {
                "User-Agent": SCRAPE_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
            }

    def search(self, query):
        """
        Search for subtitles by scraping subs.ro website.

        Tries multiple strategies:
        1. Search by IMDB ID (browse the film/show page)
        2. Search by title via AJAX search endpoint
        3. Search by title via Google-style URL guess

        Args:
            query: Dict with media_data (same format as provider.py receives)

        Returns:
            List of subtitle result dicts in same format as API returns, or [].
        """
        try:
            return self._search_internal(query)
        except Exception as e:
            _log("Scraper search failed: {}".format(e))
            return []

    def _search_internal(self, query):
        """Internal search implementation."""
        results = []

        # Try IMDB ID first (most reliable)
        imdb_id = self._get_imdb_id(query)
        if imdb_id:
            _log("Scraper: trying IMDB ID {}".format(imdb_id))
            results = self._search_by_imdb(imdb_id)
            if results:
                return results

        # Try title search via browsing
        title = query.get("query", "")
        if title:
            _log("Scraper: trying title search '{}'".format(title))
            results = self._search_by_title(title)
            if results:
                return results

        return results

    def _get_imdb_id(self, query):
        """Extract IMDB ID from query in tt-prefixed format."""
        for key in ("parent_imdb_id", "imdb_id", "episode_imdb_id"):
            val = query.get(key)
            if val:
                s = str(val).strip()
                if s.startswith("tt"):
                    return s
                if s.isdigit():
                    return "tt{}".format(s.zfill(7))
        return None

    def _search_by_imdb(self, imdb_id):
        """
        Search subs.ro by IMDB ID.

        Tries multiple URL patterns:
        1. https://subs.ro/subtitrari/imdbid/{tt_id} (discovered in HTML links)
        2. https://subs.ro/film/{tt_id} (alternative pattern)
        """
        results = []

        # Try multiple URL patterns
        urls_to_try = [
            "{}/subtitrari/imdbid/{}".format(SUBS_RO_BASE, imdb_id),
        ]

        for url in urls_to_try:
            _log("Scraper: fetching {}".format(url))
            html = self._fetch_page(url)
            if html:
                parsed = self._parse_subtitle_listing(html)
                if parsed:
                    results.extend(parsed)
                    _log("Scraper: found {} results from {}".format(len(parsed), url))
                    break

        return results

    def _search_by_title(self, title):
        """
        Search subs.ro by title.

        Uses the AJAX search endpoint discovered on the advanced search page.
        Falls back to URL pattern guessing.
        """
        results = []

        # Strategy 1: Try AJAX search endpoint
        try:
            ajax_url = "{}/ajax/search/".format(SUBS_RO_BASE)
            _log("Scraper: AJAX search for '{}'".format(title))
            r = self.session.post(
                ajax_url,
                data={"q": title},
                timeout=SCRAPE_TIMEOUT,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "{}/cautare".format(SUBS_RO_BASE),
                }
            )
            if r.status_code == 200 and r.text:
                parsed = self._parse_ajax_search(r.text, title)
                if parsed:
                    return parsed
        except Exception as e:
            _log("Scraper: AJAX search failed: {}".format(e))

        # Strategy 2: Try URL slug pattern
        slug = self._title_to_slug(title)
        if slug:
            # Try browsing the subtitle page directly
            urls_to_try = [
                "{}/subtitrari/{}".format(SUBS_RO_BASE, slug),
            ]
            for url in urls_to_try:
                html = self._fetch_page(url)
                if html:
                    parsed = self._parse_subtitle_listing(html)
                    if parsed:
                        results.extend(parsed)
                        break

        return results

    def _fetch_page(self, url):
        """
        Fetch a web page. Returns HTML string or None.
        Silently handles all errors.
        """
        try:
            r = self.session.get(url, timeout=SCRAPE_TIMEOUT, allow_redirects=True)
            if r.status_code == 200:
                return r.text
            elif r.status_code == 429:
                _log("Scraper: rate limited (429) on {}".format(url))
            elif r.status_code == 404:
                _log("Scraper: 404 on {}".format(url))
            else:
                _log("Scraper: HTTP {} on {}".format(r.status_code, url))
        except Exception as e:
            _log("Scraper: fetch error: {}".format(e))
        return None

    def _parse_subtitle_listing(self, html):
        """
        Parse subtitle entries from an HTML page.

        Looks for the pattern discovered in website analysis:
        - Subtitle links: /subtitrare/{slug}/{id}
        - Download links: /subtitrare/descarca/{slug}/{id}
        - Language flags: flag-{lang}-big.png
        - IMDB links: //imdb.com/title/{tt_id}

        Returns list of dicts in same format as API response items.
        """
        results = []

        # Find all subtitle entry links: /subtitrare/{slug}/{id}
        # Pattern matches both the subtitle page link and download link
        subtitle_pattern = re.compile(
            r'href="(?:https?://subs\.ro)?/subtitrare/([^"/]+)/(\d+)"',
            re.IGNORECASE
        )

        # Find all download links: /subtitrare/descarca/{slug}/{id}
        download_pattern = re.compile(
            r'href="(?:https?://subs\.ro)?/subtitrare/descarca/([^"/]+)/(\d+)"',
            re.IGNORECASE
        )

        # Find language flags: flag-{lang}-big.png
        flag_pattern = re.compile(
            r'flag-(\w+)-big\.png',
            re.IGNORECASE
        )

        # Collect unique subtitle IDs with their metadata
        seen_ids = set()

        # Method 1: Find subtitle entries via download links (most reliable)
        for match in download_pattern.finditer(html):
            slug = match.group(1)
            sub_id = match.group(2)

            if sub_id in seen_ids:
                continue
            seen_ids.add(sub_id)

            # Extract title from slug (e.g., "shelter-2026" -> "Shelter (2026)")
            title = self._slug_to_title(slug)

            # Try to find language flag near this match
            # Look at surrounding context (500 chars before and after)
            start = max(0, match.start() - 500)
            end = min(len(html), match.end() + 500)
            context = html[start:end]

            language = "ro"  # Default to Romanian
            flag_match = flag_pattern.search(context)
            if flag_match:
                flag_code = flag_match.group(1).lower()
                language = FLAG_TO_LANG.get(flag_code, "ro")

            # Try to find translator in context
            translator = ""
            trans_match = re.search(
                r'(?:Traduc[aă]tor|Translator)\s*:\s*([^<\n]+)',
                context, re.IGNORECASE
            )
            if trans_match:
                translator = trans_match.group(1).strip()

            # Build result in same format as API
            result = {
                "id": int(sub_id),
                "title": title,
                "description": slug,
                "language": language,
                "translator": translator,
                "type": "movie",
                "downloadLink": "{}/subtitrare/descarca/{}/{}".format(
                    SUBS_RO_BASE, slug, sub_id),
                "link": "{}/subtitrare/{}/{}".format(
                    SUBS_RO_BASE, slug, sub_id),
                "_source": "scraper",
            }
            results.append(result)

        # Method 2: If no download links found, try subtitle page links
        if not results:
            for match in subtitle_pattern.finditer(html):
                slug = match.group(1)
                sub_id = match.group(2)

                if sub_id in seen_ids:
                    continue
                if slug == "descarca":
                    continue
                seen_ids.add(sub_id)

                title = self._slug_to_title(slug)

                # Look for language in nearby context
                start = max(0, match.start() - 300)
                end = min(len(html), match.end() + 300)
                context = html[start:end]

                language = "ro"
                flag_match = flag_pattern.search(context)
                if flag_match:
                    flag_code = flag_match.group(1).lower()
                    language = FLAG_TO_LANG.get(flag_code, "ro")

                result = {
                    "id": int(sub_id),
                    "title": title,
                    "description": slug,
                    "language": language,
                    "translator": "",
                    "type": "movie",
                    "link": "{}/subtitrare/{}/{}".format(
                        SUBS_RO_BASE, slug, sub_id),
                    "_source": "scraper",
                }
                results.append(result)

        if results:
            _log("Scraper: parsed {} subtitle entries from HTML".format(len(results)))

        return results

    def _parse_ajax_search(self, response_text, search_title):
        """
        Parse AJAX search response.

        The AJAX endpoint may return HTML fragments or JSON.
        We try to parse both formats.
        """
        results = []

        # Try as HTML fragment first
        if "<" in response_text:
            parsed = self._parse_subtitle_listing(response_text)
            if parsed:
                return parsed

        # Try as JSON
        try:
            import json
            data = json.loads(response_text)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        # Normalize to our format
                        result = {
                            "id": item.get("id", 0),
                            "title": item.get("title", ""),
                            "description": item.get("description", ""),
                            "language": item.get("language", "ro"),
                            "translator": item.get("translator", ""),
                            "type": item.get("type", "movie"),
                            "_source": "scraper_ajax",
                        }
                        if result["id"]:
                            results.append(result)
            elif isinstance(data, dict) and "items" in data:
                for item in data["items"]:
                    if isinstance(item, dict):
                        item["_source"] = "scraper_ajax"
                        results.append(item)
        except (ValueError, KeyError):
            pass

        return results

    def _title_to_slug(self, title):
        """Convert a title to a URL slug (e.g., 'Breaking Bad' -> 'breaking-bad')."""
        if not title:
            return None
        slug = title.lower().strip()
        # Remove special characters
        slug = re.sub(r"[^a-z0-9\s-]", "", slug)
        # Replace spaces with hyphens
        slug = re.sub(r"\s+", "-", slug)
        # Remove multiple hyphens
        slug = re.sub(r"-+", "-", slug)
        slug = slug.strip("-")
        return slug if slug else None

    def _slug_to_title(self, slug):
        """
        Convert a URL slug back to a readable title.

        E.g., "shelter-2026" -> "Shelter (2026)"
             "breaking-bad" -> "Breaking Bad"
        """
        if not slug:
            return ""

        # Check if slug ends with a year
        year_match = re.search(r"-(\d{4})$", slug)
        if year_match:
            year = year_match.group(1)
            name_part = slug[:year_match.start()]
            title_words = name_part.replace("-", " ").title()
            return "{} ({})".format(title_words, year)
        else:
            return slug.replace("-", " ").title()
