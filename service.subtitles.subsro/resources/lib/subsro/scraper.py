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
- IMDB browse: https://subs.ro/subtitrari/imdbid/{numeric_imdb_id}

v1.0.10: Major rewrite:
- Extract actual title text from HTML anchor tags (not just URL slugs)
- html.unescape() applied to ALL extracted text fields
- Season/episode context filtering in search results
- Download count extraction for proper ranking
- Proper downloadLink field for fallback download
"""

import html
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


def _unescape(text):
    """Decode HTML entities and clean up text.

    Handles both named (&amp;) and numeric (&#x22; &#39;) entities.
    Returns clean plain text safe for display in Kodi.
    """
    if not text:
        return text
    try:
        # html.unescape handles all standard HTML entities:
        # &#x22; -> "   &#x27; -> '   &amp; -> &   &lt; -> <   etc.
        decoded = html.unescape(str(text))
        # Strip leading/trailing whitespace
        return decoded.strip()
    except Exception:
        return str(text).strip()


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
        # Always create a separate session for scraping (different headers than API)
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
        2. Search by title via URL slug guess

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

        # Try IMDB ID first (most reliable -- confirmed working URL pattern)
        imdb_id = self._get_imdb_id(query)
        if imdb_id:
            # v1.0.10: The website URL uses numeric IMDB ID WITHOUT 'tt' prefix
            # https://subs.ro/subtitrari/imdbid/0413573 (NOT tt0413573)
            numeric_imdb = imdb_id
            if numeric_imdb.startswith("tt"):
                numeric_imdb = numeric_imdb[2:]
            _log("Scraper: trying IMDB ID {} (numeric: {})".format(imdb_id, numeric_imdb))
            results = self._search_by_imdb(numeric_imdb)
            if results:
                _log("Scraper: IMDB search returned {} results".format(len(results)))
                return results

        # Try title search via URL slug
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

    def _search_by_imdb(self, numeric_imdb_id):
        """
        Search subs.ro by numeric IMDB ID.

        Confirmed URL pattern: https://subs.ro/subtitrari/imdbid/{numeric_id}
        Example: https://subs.ro/subtitrari/imdbid/0413573 for Grey's Anatomy
        """
        url = "{}/subtitrari/imdbid/{}".format(SUBS_RO_BASE, numeric_imdb_id)
        _log("Scraper: fetching {}".format(url))
        page_html = self._fetch_page(url)
        if page_html:
            parsed = self._parse_subtitle_listing(page_html)
            if parsed:
                _log("Scraper: found {} results from {}".format(len(parsed), url))
                return parsed
        return []

    def _search_by_title(self, title):
        """
        Search subs.ro by title using URL slug pattern.

        v1.0.10: Removed AJAX search (endpoint returns "He's dead, Jim!" error).
        Only uses URL slug browsing which is confirmed to work.
        """
        results = []

        slug = self._title_to_slug(title)
        if slug:
            urls_to_try = [
                "{}/subtitrari/{}".format(SUBS_RO_BASE, slug),
            ]
            for url in urls_to_try:
                _log("Scraper: fetching {}".format(url))
                page_html = self._fetch_page(url)
                if page_html:
                    parsed = self._parse_subtitle_listing(page_html)
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

    def _parse_subtitle_listing(self, page_html):
        """
        Parse subtitle entries from an HTML page.

        v1.0.10: Complete rewrite to extract ACTUAL title text from HTML,
        not just URL slugs. Also extracts download counts for ranking.

        The subs.ro listing page has this structure per subtitle entry:
        - View link: <a href="/subtitrare/{slug}/{id}">Title Text</a>
        - Download link: <a href="/subtitrare/descarca/{slug}/{id}">...</a>
        - Language flag: <img src="...flag-{lang}-big.png">
        - Translator: "Traducator: Name"
        - Downloads count: numeric text near the entry

        Returns list of dicts in same format as API response items.
        """
        results = []
        seen_ids = set()

        # v1.0.10: Extract title text from subtitle page links (NOT download links)
        # Pattern: <a href="/subtitrare/{slug}/{id}" ...>{title text}</a>
        # This captures the ACTUAL displayed title including proper characters.
        # We use a two-step approach:
        # 1. Find all /subtitrare/{slug}/{id} links and extract {slug} and {id}
        # 2. Try to find the displayed title text near these links
        # 3. Also find /subtitrare/descarca/{slug}/{id} download links

        # First, collect all subtitle IDs and their slugs from download links
        download_pattern = re.compile(
            r'href="(?:https?://subs\.ro)?/subtitrare/descarca/([^"/]+)/(\d+)"',
            re.IGNORECASE
        )

        # Also collect subtitle page links with their anchor text
        # This regex captures: href="..." followed by optional attributes, then >title text</a>
        title_link_pattern = re.compile(
            r'<a[^>]*href="(?:https?://subs\.ro)?/subtitrare/([^"/]+)/(\d+)"[^>]*>'
            r'([^<]*(?:<(?!/a)[^<]*)*)</a>',
            re.IGNORECASE | re.DOTALL
        )

        # Language flags: flag-{lang}-big.png
        flag_pattern = re.compile(
            r'flag-(\w+)-big\.png',
            re.IGNORECASE
        )

        # Download count pattern (number with optional comma separator)
        downloads_pattern = re.compile(
            r'(\d[\d,.]*)\s*(?:descarcari|desc\.|downloads?)',
            re.IGNORECASE
        )

        # Build a map of subtitle_id -> {slug, title_text, download_url}
        # from all the links found on the page
        subtitle_data = {}

        # Step 1: Find all title links (subtitle page links with anchor text)
        for match in title_link_pattern.finditer(page_html):
            slug = match.group(1)
            sub_id = match.group(2)

            # Skip download links (slug = "descarca")
            if slug == "descarca":
                continue

            # Extract and clean anchor text
            raw_title = match.group(3)
            # Remove any inner HTML tags
            clean_title = re.sub(r'<[^>]+>', '', raw_title).strip()
            # Decode HTML entities
            clean_title = _unescape(clean_title)

            if sub_id not in subtitle_data:
                subtitle_data[sub_id] = {
                    "slug": slug,
                    "title": clean_title,
                    "has_download": False,
                }
            elif clean_title and not subtitle_data[sub_id].get("title"):
                subtitle_data[sub_id]["title"] = clean_title

        # Step 2: Find all download links and mark which IDs have download URLs
        for match in download_pattern.finditer(page_html):
            slug = match.group(1)
            sub_id = match.group(2)

            if sub_id not in subtitle_data:
                subtitle_data[sub_id] = {
                    "slug": slug,
                    "title": "",
                    "has_download": True,
                }
            else:
                subtitle_data[sub_id]["has_download"] = True

        _log("Scraper: found {} unique subtitle IDs in HTML".format(len(subtitle_data)))

        # Step 3: For each subtitle, extract context metadata (language, translator, downloads)
        for sub_id, data in subtitle_data.items():
            if sub_id in seen_ids:
                continue
            seen_ids.add(sub_id)

            slug = data["slug"]
            title = data.get("title", "") or self._slug_to_title(slug)

            # Find the position of this subtitle ID in the HTML for context extraction
            # Search for the subtitle link or download link
            id_pattern = re.compile(
                r'/subtitrare/(?:descarca/)?[^"/]+/{}'.format(re.escape(sub_id)),
                re.IGNORECASE
            )
            id_match = id_pattern.search(page_html)

            language = "ro"  # Default to Romanian
            translator = ""
            downloads = 0

            if id_match:
                # Extract context: 800 chars before and after the match
                start = max(0, id_match.start() - 800)
                end = min(len(page_html), id_match.end() + 800)
                context = page_html[start:end]

                # Language from flag
                flag_match = flag_pattern.search(context)
                if flag_match:
                    flag_code = flag_match.group(1).lower()
                    language = FLAG_TO_LANG.get(flag_code, "ro")

                # Translator
                trans_match = re.search(
                    r'(?:Traduc[aă]tor|Translator)\s*:\s*([^<\n]+)',
                    context, re.IGNORECASE
                )
                if trans_match:
                    translator = _unescape(trans_match.group(1).strip())

                # Download count
                dl_match = downloads_pattern.search(context)
                if dl_match:
                    try:
                        downloads = int(dl_match.group(1).replace(",", "").replace(".", ""))
                    except (ValueError, TypeError):
                        downloads = 0

            # Build download URL
            download_url = "{}/subtitrare/descarca/{}/{}".format(
                SUBS_RO_BASE, slug, sub_id)

            # Extract year from slug or title
            year = ""
            year_match = re.search(r'\((\d{4})\)', title)
            if year_match:
                year = year_match.group(1)
            else:
                slug_year = re.search(r'-(\d{4})$', slug)
                if slug_year:
                    year = slug_year.group(1)

            # v1.0.10: Build description from title for TV filter matching
            # The TV filter in provider.py searches title + description for
            # season/episode patterns like "Sezonul 1", "S01E01", etc.
            description = _unescape(title)

            # Build result in same format as API
            result = {
                "id": int(sub_id),
                "title": title,
                "description": description,
                "language": language,
                "translator": translator,
                "downloads": downloads,
                "year": year,
                "type": "subtitle",
                "downloadLink": download_url,
                "link": "{}/subtitrare/{}/{}".format(
                    SUBS_RO_BASE, slug, sub_id),
                "_source": "scraper",
            }
            results.append(result)

        if results:
            _log("Scraper: parsed {} subtitle entries from HTML".format(len(results)))
            # Log first few for debugging
            for i, r in enumerate(results[:3]):
                _log("Scraper result[{}]: id={} title='{}' lang='{}' dl={}".format(
                    i, r["id"], r["title"][:80], r["language"], r["downloads"]))

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
        Only used as fallback when anchor text extraction fails.

        v1.0.10: Also handles slugs with encoded entities like
        'x22grey-x27s-anatomy-x22-2005' by stripping x22/x27 prefixes.

        E.g., "shelter-2026" -> "Shelter (2026)"
             "grey-s-anatomy-sezonul-1-2005" -> "Grey S Anatomy Sezonul 1 (2005)"
        """
        if not slug:
            return ""

        # v1.0.10: Clean up URL-encoded entity fragments
        # Some slugs contain 'x22' (") and 'x27' (') from HTML entities
        # e.g., "x22grey-x27s-anatomy-x22-2005"
        cleaned = slug
        # Remove x22 (double quote entity fragment) at word boundaries
        cleaned = re.sub(r'(?:^|(?<=-))x22(?=-|$)', '', cleaned)
        # Replace x27 (apostrophe entity fragment) with nothing
        cleaned = re.sub(r'x27', '', cleaned)
        # Clean up resulting double/triple hyphens
        cleaned = re.sub(r'-+', '-', cleaned).strip('-')

        if not cleaned:
            cleaned = slug

        # Check if slug ends with a year
        year_match = re.search(r"-(\d{4})$", cleaned)
        if year_match:
            year = year_match.group(1)
            name_part = cleaned[:year_match.start()]
            title_words = name_part.replace("-", " ").title()
            return "{} ({})".format(title_words, year)
        else:
            return cleaned.replace("-", " ").title()
