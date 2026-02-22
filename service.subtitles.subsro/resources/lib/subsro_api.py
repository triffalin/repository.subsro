# -*- coding: utf-8 -*-
"""
subs.ro API client for Kodi subtitle addon.
API docs: https://subs.ro/api
"""

import json
from urllib.request import Request, urlopen
from urllib.parse import quote, urlencode
from urllib.error import HTTPError, URLError

from resources.lib import logger


class SubsRoAPIError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class QuotaExceededError(SubsRoAPIError):
    pass


class SubsRoAPI:
    BASE_URL = "https://api.subs.ro/v1.0"
    TIMEOUT = 15
    VALID_SEARCH_FIELDS = ("imdbid", "tmdbid", "title", "release")
    VALID_LANGUAGES = ("ro", "en", "ita", "fra", "ger", "ung", "gre", "por", "spa", "alt")

    def __init__(self, api_key):
        if not api_key:
            raise SubsRoAPIError("API key is required")
        self.api_key = api_key
        self._headers = {
            "X-Subs-Api-Key": api_key,
            "Accept": "application/json",
            "User-Agent": "Kodi/service.subtitles.subsro v1.0.0",
        }

    def _request(self, endpoint, expect_json=True):
        url = self.BASE_URL + endpoint
        logger.debug("GET " + url)
        req = Request(url, headers=self._headers, method="GET")
        try:
            response = urlopen(req, timeout=self.TIMEOUT)
            data = response.read()
            if expect_json:
                return json.loads(data.decode("utf-8"))
            return data
        except HTTPError as e:
            if e.code == 429:
                raise QuotaExceededError("Daily quota exceeded", status_code=429)
            elif e.code == 401:
                raise SubsRoAPIError("Invalid API key", status_code=401)
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            raise SubsRoAPIError("HTTP {code}: {body}".format(code=e.code, body=body), status_code=e.code)
        except URLError as e:
            raise SubsRoAPIError("Network error: {r}".format(r=str(e.reason)))
        except Exception as e:
            raise SubsRoAPIError("Unexpected error: {err}".format(err=str(e)))

    def search(self, search_field, value, language=None):
        if search_field not in self.VALID_SEARCH_FIELDS or not value:
            return []
        endpoint = "/search/{field}/{value}".format(field=search_field, value=quote(str(value), safe=""))
        if language and language in self.VALID_LANGUAGES:
            endpoint += "?" + urlencode({"language": language})
        try:
            response = self._request(endpoint)
            results = response.get("results", [])
            logger.info("Search [{field}={value}]: {n} results".format(field=search_field, value=value, n=len(results)))
            return results
        except SubsRoAPIError as e:
            logger.error("Search failed: {err}".format(err=str(e)))
            return []

    def get_subtitle(self, subtitle_id):
        if not subtitle_id:
            return None
        try:
            response = self._request("/subtitle/" + quote(str(subtitle_id), safe=""))
            results = response.get("results", [])
            return results[0] if isinstance(results, list) and results else None
        except SubsRoAPIError as e:
            logger.error("get_subtitle failed: {err}".format(err=str(e)))
            return None

    def download(self, subtitle_id):
        if not subtitle_id:
            raise SubsRoAPIError("Subtitle ID required")
        return self._request("/subtitle/{id}/download".format(id=quote(str(subtitle_id), safe="")), expect_json=False)

    def check_quota(self):
        try:
            return self._request("/quota")
        except SubsRoAPIError as e:
            logger.error("Quota check failed: {err}".format(err=str(e)))
            return None
