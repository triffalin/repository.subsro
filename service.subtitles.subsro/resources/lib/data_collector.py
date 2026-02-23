from urllib.parse import unquote
from difflib import SequenceMatcher
import json
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon

from resources.lib.utilities import log, normalize_string

_library_cache = {}
_cache_max_age = 300  # 5 minutes


def _get_cache_key(method, params):
    import hashlib
    cache_str = "{}:{}".format(method, json.dumps(params, sort_keys=True) if params else "None")
    return hashlib.md5(cache_str.encode()).hexdigest()


def _is_cache_valid(cache_entry):
    import time
    return time.time() - cache_entry.get("timestamp", 0) < _cache_max_age


def _get_from_cache(method, params):
    cache_key = _get_cache_key(method, params)
    if cache_key in _library_cache:
        cache_entry = _library_cache[cache_key]
        if _is_cache_valid(cache_entry):
            log(__name__, "Cache hit for {}".format(method))
            return cache_entry["result"]
        else:
            del _library_cache[cache_key]
    return None


def _store_in_cache(method, params, result):
    import time
    cache_key = _get_cache_key(method, params)
    _library_cache[cache_key] = {"result": result, "timestamp": time.time()}
    log(__name__, "Cached result for {}".format(method))


__addon__ = xbmcaddon.Addon()


def get_file_path():
    return xbmc.Player().getPlayingFile()


def _strip_imdb_tt(value):
    if not value:
        return None
    s = str(value).strip()
    if s.startswith("tt"):
        s = s[2:]
    return s if s.isdigit() else None


def _extract_basic_tv_info(filename):
    """Extract basic TV show info from filename using simple regex"""
    import re
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    patterns = [
        r"[Ss](\d{1,2})[Ee](\d{1,2})",
        r"(\d{1,2})x(\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            season_num = match.group(1)
            episode_num = match.group(2)
            show_title = name[:match.start()].strip()
            show_title = re.sub(r"[._-]", " ", show_title).strip()
            show_title = re.sub(r"\s+", " ", show_title)
            return show_title, season_num, episode_num
    return None, None, None


def _query_kodi_library_for_movie(movie_title, year=None, dbid=None):
    """Query Kodi library for movie IDs"""
    if not movie_title and not dbid:
        return None, None, None
    try:
        if dbid and str(dbid).isdigit():
            result = _jsonrpc("VideoLibrary.GetMovieDetails", {
                "movieid": int(dbid),
                "properties": ["imdbnumber", "uniqueid", "title", "year"]
            }, use_cache=False)
            if result and "moviedetails" in result:
                return _extract_movie_ids(result["moviedetails"])
        if movie_title:
            result = _jsonrpc("VideoLibrary.GetMovies", {
                "properties": ["imdbnumber", "uniqueid", "title", "year"],
                "limits": {"end": 100}
            }, use_cache=False)
            if result and "movies" in result and result["movies"]:
                matching = [m for m in result["movies"]
                            if movie_title.lower() in m.get("title", "").lower()
                            or m.get("title", "").lower() in movie_title.lower()]
                if matching:
                    best = _select_best_movie_match(matching, movie_title, year)
                    if best:
                        return _extract_movie_ids(best)
    except Exception as e:
        log(__name__, "Failed to query library for movie: {}".format(e))
    return None, None, None


def _select_best_movie_match(movies, search_title, search_year=None):
    if not movies:
        return None
    if len(movies) == 1:
        return movies[0]
    best_score = 0
    best_movie = None
    for movie in movies:
        score = 0
        mt = movie.get("title", "")
        my = movie.get("year")
        if search_title:
            score += SequenceMatcher(None, search_title.lower(), mt.lower()).ratio() * 100
            if search_title.lower() == mt.lower():
                score += 50
        if search_year and my:
            diff = abs(int(search_year) - my)
            if diff == 0:
                score += 25
            elif diff <= 1:
                score += 15
        if score > best_score:
            best_score = score
            best_movie = movie
    return best_movie


def _extract_movie_ids(movie):
    """Extract IMDb and TMDb IDs from movie data"""
    movie_imdb = None
    movie_tmdb = None
    file_path = movie.get("file", "")
    imdb_digits = _strip_imdb_tt(movie.get("imdbnumber", ""))
    if imdb_digits and 6 <= len(imdb_digits) <= 8:
        movie_imdb = int(imdb_digits)
        log(__name__, "Found Movie IMDb: {}".format(movie_imdb))
    uniqueids = movie.get("uniqueid", {})
    if isinstance(uniqueids, dict):
        tmdb_raw = uniqueids.get("tmdb", "")
        if tmdb_raw and str(tmdb_raw).isdigit():
            movie_tmdb = int(tmdb_raw)
            log(__name__, "Found Movie TMDb: {}".format(movie_tmdb))
    return movie_imdb, movie_tmdb, file_path


def _query_kodi_library_for_show(show_title, year=None):
    """Query Kodi library for TV show IDs"""
    if not show_title:
        return None, None, None
    try:
        result = _jsonrpc("VideoLibrary.GetTVShows", {
            "properties": ["imdbnumber", "uniqueid", "title", "episodeguide"],
            "limits": {"end": 50}
        }, use_cache=False)
        if result and "tvshows" in result and result["tvshows"]:
            matching = [s for s in result["tvshows"]
                        if show_title.lower() in s.get("title", "").lower()
                        or s.get("title", "").lower() in show_title.lower()]
            if matching:
                best = _select_best_show_match(matching, show_title, year)
                if best:
                    return _extract_show_ids(best)
    except Exception as e:
        log(__name__, "Failed to query library for show: {}".format(e))
    return None, None, None


def _select_best_show_match(tvshows, search_title, search_year=None):
    if not tvshows:
        return None
    if len(tvshows) == 1:
        return tvshows[0]
    best_score = 0
    best_show = None
    for show in tvshows:
        score = 0
        st = show.get("title", "")
        sot = show.get("originaltitle", "")
        sy = show.get("year")
        if search_title:
            sim = SequenceMatcher(None, search_title.lower(), st.lower()).ratio() * 100
            if sot:
                sim = max(sim, SequenceMatcher(None, search_title.lower(), sot.lower()).ratio() * 100)
            score += sim
            if search_title.lower() == st.lower() or search_title.lower() == sot.lower():
                score += 50
        if search_year and sy:
            diff = abs(int(search_year) - sy)
            if diff == 0:
                score += 25
            elif diff <= 2:
                score += 10
        if score > best_score:
            best_score = score
            best_show = show
    return best_show


def _extract_show_ids(tvshow):
    """Extract IMDb and TMDb IDs from TV show data"""
    parent_imdb = None
    parent_tmdb = None
    tvshow_id = tvshow.get("tvshowid")
    imdb_digits = _strip_imdb_tt(tvshow.get("imdbnumber", ""))
    if imdb_digits and 6 <= len(imdb_digits) <= 8:
        parent_imdb = int(imdb_digits)
        log(__name__, "Found Parent IMDb: {}".format(parent_imdb))
    uniqueids = tvshow.get("uniqueid", {})
    if isinstance(uniqueids, dict):
        tmdb_raw = uniqueids.get("tmdb", "")
        if tmdb_raw and str(tmdb_raw).isdigit():
            parent_tmdb = int(tmdb_raw)
            log(__name__, "Found Parent TMDb: {}".format(parent_tmdb))
    if not parent_tmdb:
        eg = tvshow.get("episodeguide", "")
        if eg:
            try:
                import re
                m = re.search(r'tmdb["\']?[:\s]*([0-9]+)', eg, re.IGNORECASE)
                if m:
                    parent_tmdb = int(m.group(1))
                    log(__name__, "Found Parent TMDb from episodeguide: {}".format(parent_tmdb))
            except Exception:
                pass
    return parent_imdb, parent_tmdb, tvshow_id


def _jsonrpc(method, params=None, use_cache=True):
    """JSON-RPC call with caching and error handling"""
    if use_cache and method.startswith("VideoLibrary."):
        cached_result = _get_from_cache(method, params)
        if cached_result is not None:
            return cached_result
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params:
            payload["params"] = params
        resp = xbmc.executeJSONRPC(json.dumps(payload))
        data = json.loads(resp)
        if "error" in data:
            log(__name__, "JSON-RPC error in {}: {}".format(method, data["error"].get("message", "Unknown")))
            return None
        result = data.get("result")
        if use_cache and method.startswith("VideoLibrary.") and result:
            _store_in_cache(method, params, result)
        return result
    except Exception as e:
        log(__name__, "JSON-RPC error in {}: {}".format(method, e))
        return None


def get_media_data():
    item = {
        "query": None,
        "year": xbmc.getInfoLabel("VideoPlayer.Year"),
        "season_number": str(xbmc.getInfoLabel("VideoPlayer.Season")),
        "episode_number": str(xbmc.getInfoLabel("VideoPlayer.Episode")),
        "tv_show_title": normalize_string(xbmc.getInfoLabel("VideoPlayer.TVshowtitle")),
        "original_title": normalize_string(xbmc.getInfoLabel("VideoPlayer.OriginalTitle")),
        "parent_tmdb_id": None,
        "parent_imdb_id": None,
        "imdb_id": None,
        "tmdb_id": None
    }
    log(__name__, "Initial media data from InfoLabels: {}".format(item))

    if not any([item["tv_show_title"], item["original_title"], item["year"],
                item["season_number"], item["episode_number"]]):
        log(__name__, "All InfoLabels empty - non-library file playback")
        try:
            playing_file = get_file_path()
            if playing_file:
                import os
                filename = os.path.basename(playing_file)
                log(__name__, "Filename to parse: {}".format(filename))
                show_title, season_num, episode_num = _extract_basic_tv_info(filename)
                if show_title and season_num and episode_num:
                    parent_imdb, parent_tmdb, tvshow_id = _query_kodi_library_for_show(show_title)
                    item["tv_show_title"] = show_title
                    item["season_number"] = season_num
                    item["episode_number"] = episode_num
                    if parent_imdb:
                        item["parent_imdb_id"] = parent_imdb
                    if parent_tmdb:
                        item["parent_tmdb_id"] = parent_tmdb
                    if tvshow_id:
                        item["tvshowid"] = str(tvshow_id)
                    log(__name__, "TV show: {} S{}E{}".format(show_title, season_num, episode_num))
        except Exception as e:
            log(__name__, "Failed to parse filename: {}".format(e))

    # ---------------- TV SHOW (Episode) ----------------
    if item["tv_show_title"]:
        item["tvshowid"] = xbmc.getInfoLabel("VideoPlayer.TvShowDBID")
        item["query"] = item["tv_show_title"]
        item["year"] = None

        try:
            parent_imdb_raw = (xbmc.getInfoLabel("ListItem.Property(TvShow.IMDBNumber)")
                               or xbmc.getInfoLabel("VideoPlayer.TvShow.IMDBNumber"))
            imdb_digits = _strip_imdb_tt(parent_imdb_raw)
            if imdb_digits and 6 <= len(imdb_digits) <= 8:
                item["parent_imdb_id"] = int(imdb_digits)
                log(__name__, "TRUE Parent Show IMDb ID: {}".format(item["parent_imdb_id"]))
            parent_tmdb_raw = xbmc.getInfoLabel("VideoPlayer.TvShow.UniqueID(tmdb)")
            if parent_tmdb_raw and parent_tmdb_raw.isdigit():
                item["parent_tmdb_id"] = int(parent_tmdb_raw)
                log(__name__, "TRUE Parent Show TMDb ID: {}".format(item["parent_tmdb_id"]))
        except Exception as e:
            log(__name__, "Failed to read true parent IDs from InfoLabels: {}".format(e))

        if not item.get("parent_imdb_id") and not item.get("parent_tmdb_id"):
            try:
                possible_episode_imdb = (xbmc.getInfoLabel("VideoPlayer.UniqueID(imdb)")
                                         or xbmc.getInfoLabel("VideoPlayer.IMDBNumber")
                                         or xbmc.getInfoLabel("ListItem.IMDBNumber"))
                imdb_digits = _strip_imdb_tt(possible_episode_imdb)
                if imdb_digits and 6 <= len(imdb_digits) <= 8:
                    item["imdb_id"] = int(imdb_digits)
                    log(__name__, "Episode-specific IMDb ID: {}".format(item["imdb_id"]))
                possible_episode_tmdb = xbmc.getInfoLabel("VideoPlayer.UniqueID(tmdb)")
                if possible_episode_tmdb and possible_episode_tmdb.isdigit():
                    item["tmdb_id"] = int(possible_episode_tmdb)
                    log(__name__, "Episode-specific TMDb ID: {}".format(item["tmdb_id"]))
            except Exception as e:
                log(__name__, "Failed to read episode IDs from InfoLabels: {}".format(e))

        tvshowid_str = str(item.get("tvshowid", ""))
        if len(tvshowid_str) != 0 and (not item["parent_tmdb_id"] or not item["parent_imdb_id"]):
            try:
                TVShowDetails = xbmc.executeJSONRPC(
                    '{ "jsonrpc": "2.0", "id":"1", "method": "VideoLibrary.GetTVShowDetails", '
                    '"params":{"tvshowid":' + tvshowid_str +
                    ', "properties": ["episodeguide", "imdbnumber", "uniqueid"]} }'
                )
                TVShowDetails_dict = json.loads(TVShowDetails)
                if "result" in TVShowDetails_dict and "tvshowdetails" in TVShowDetails_dict["result"]:
                    tvshow_details = TVShowDetails_dict["result"]["tvshowdetails"]
                    if not item["parent_imdb_id"]:
                        imdb_raw = str(tvshow_details.get("imdbnumber") or "")
                        imdb_digits = _strip_imdb_tt(imdb_raw)
                        if imdb_digits and 6 <= len(imdb_digits) <= 8:
                            item["parent_imdb_id"] = int(imdb_digits)
                            log(__name__, "Parent IMDb via JSON-RPC: {}".format(item["parent_imdb_id"]))
                    if not item["parent_tmdb_id"]:
                        uniqueids = tvshow_details.get("uniqueid", {})
                        if isinstance(uniqueids, dict):
                            tmdb_raw = uniqueids.get("tmdb", "")
                            if tmdb_raw and str(tmdb_raw).isdigit():
                                item["parent_tmdb_id"] = int(tmdb_raw)
                                log(__name__, "Parent TMDb via JSON-RPC: {}".format(item["parent_tmdb_id"]))
                        if not item["parent_tmdb_id"]:
                            episodeguideXML = tvshow_details.get("episodeguide")
                            if episodeguideXML:
                                try:
                                    episodeguide = ET.fromstring(episodeguideXML)
                                    if episodeguide.text:
                                        guide_json = json.loads(episodeguide.text)
                                        tmdb = guide_json.get("tmdb")
                                        if tmdb and str(tmdb).isdigit():
                                            item["parent_tmdb_id"] = int(tmdb)
                                            log(__name__, "Parent TMDb via episodeguide: {}".format(item["parent_tmdb_id"]))
                                except (ET.ParseError, json.JSONDecodeError, ValueError):
                                    pass
            except (json.JSONDecodeError, ET.ParseError, ValueError, KeyError) as e:
                log(__name__, "Failed to extract TV show IDs via JSON-RPC: {}".format(e))

        try:
            ep_tmdb = xbmc.getInfoLabel("VideoPlayer.UniqueID(tmdbepisode)")
            if ep_tmdb and ep_tmdb.isdigit():
                item["tmdb_id"] = int(ep_tmdb)
                log(__name__, "Dedicated Episode TMDb ID: {}".format(item["tmdb_id"]))
            ep_imdb = xbmc.getInfoLabel("VideoPlayer.UniqueID(imdbepisode)")
            ep_imdb_digits = _strip_imdb_tt(ep_imdb)
            if ep_imdb_digits and ep_imdb_digits.isdigit():
                item["imdb_id"] = int(ep_imdb_digits)
                log(__name__, "Dedicated Episode IMDb ID: {}".format(item["imdb_id"]))
        except Exception as e:
            log(__name__, "Failed to read dedicated episode IDs: {}".format(e))

    # ---------------- MOVIE ----------------
    elif item["original_title"]:
        item["query"] = item["original_title"]
        movie_dbid = xbmc.getInfoLabel("VideoPlayer.DBID")

        try:
            imdb_raw = (xbmc.getInfoLabel("VideoPlayer.UniqueID(imdb)")
                        or xbmc.getInfoLabel("VideoPlayer.IMDBNumber"))
            imdb_digits = _strip_imdb_tt(imdb_raw)
            if imdb_digits and 6 <= len(imdb_digits) <= 8:
                item["imdb_id"] = int(imdb_digits)
                log(__name__, "Found IMDB ID for movie: {}".format(item["imdb_id"]))
            tmdb_raw = xbmc.getInfoLabel("VideoPlayer.UniqueID(tmdb)")
            if tmdb_raw and str(tmdb_raw).isdigit():
                tmdb_id = int(tmdb_raw)
                if tmdb_id > 0:
                    item["tmdb_id"] = tmdb_id
                    log(__name__, "Found TMDB ID for movie: {}".format(item["tmdb_id"]))
        except (ValueError, KeyError) as e:
            log(__name__, "Failed to extract movie IDs from InfoLabels: {}".format(e))

        if not item.get("imdb_id") and not item.get("tmdb_id") and movie_dbid and movie_dbid.isdigit():
            log(__name__, "No IDs from InfoLabels, trying library query with DBID: {}".format(movie_dbid))
            mi, mt, fp = _query_kodi_library_for_movie(None, None, movie_dbid)
            if mi:
                item["imdb_id"] = mi
            if mt:
                item["tmdb_id"] = mt

        if not item.get("imdb_id") and not item.get("tmdb_id"):
            log(__name__, "No IDs, searching library by title: {}".format(item["original_title"]))
            mi, mt, fp = _query_kodi_library_for_movie(item["original_title"], item.get("year"))
            if mi:
                item["imdb_id"] = mi
            if mt:
                item["tmdb_id"] = mt

    # ---------- Cleanup & precedence ----------
    for k in ("parent_tmdb_id", "parent_imdb_id", "tmdb_id", "imdb_id"):
        v = item.get(k)
        if v in (0, "0", "", None):
            item[k] = None

    if item.get("parent_tmdb_id") and item.get("parent_imdb_id"):
        log(__name__, "Both parent IDs found, preferring IMDB ID: {}".format(item["parent_imdb_id"]))
        item["parent_tmdb_id"] = None

    if item.get("tmdb_id") and item.get("imdb_id"):
        log(__name__, "Both item IDs found, preferring IMDB ID: {}".format(item["imdb_id"]))
        item["tmdb_id"] = None

    # ---------- Final ID Strategy Selection (TV Episodes Only) ----------
    if item.get("tv_show_title"):
        if item.get("parent_imdb_id"):
            item["parent_tmdb_id"] = None
            item["imdb_id"] = None
            item["tmdb_id"] = None
            log(__name__, "Final Strategy: parent_imdb_id={} + season/episode".format(item["parent_imdb_id"]))
        elif item.get("parent_tmdb_id"):
            item["parent_imdb_id"] = None
            item["imdb_id"] = None
            item["tmdb_id"] = None
            log(__name__, "Final Strategy: parent_tmdb_id={} + season/episode".format(item["parent_tmdb_id"]))
        elif item.get("imdb_id"):
            item["parent_imdb_id"] = None
            item["parent_tmdb_id"] = None
            item["tmdb_id"] = None
            log(__name__, "Final Strategy: episode imdb_id={}".format(item["imdb_id"]))
        elif item.get("tmdb_id"):
            item["parent_imdb_id"] = None
            item["parent_tmdb_id"] = None
            item["imdb_id"] = None
            log(__name__, "Final Strategy: episode tmdb_id={}".format(item["tmdb_id"]))

    if not item.get("query"):
        fallback_title = normalize_string(xbmc.getInfoLabel("VideoPlayer.Title"))
        if fallback_title:
            item["query"] = fallback_title
        else:
            try:
                pf = get_file_path()
                if pf:
                    import os
                    item["query"] = os.path.basename(pf)
            except Exception:
                item["query"] = "Unknown"

    # Specials handling
    if isinstance(item.get("episode_number"), str) and item["episode_number"] and item["episode_number"].lower().find("s") > -1:
        item["season_number"] = "0"
        item["episode_number"] = item["episode_number"][-1:]

    if "tvshowid" in item:
        del item["tvshowid"]

    log(__name__, "Media data result: {} - IMDb:{} TMDb:{}".format(
        item.get("query"),
        item.get("imdb_id") or item.get("parent_imdb_id"),
        item.get("tmdb_id") or item.get("parent_tmdb_id")
    ))
    return item


def get_language_data(params):
    search_languages = unquote(params.get("languages", "")).split(",")
    search_languages_str = ""
    preferred_language = params.get("preferredlanguage", "")

    if preferred_language and preferred_language not in search_languages \
            and preferred_language != "Unknown" and preferred_language != "Undetermined":
        search_languages.append(preferred_language)
        search_languages_str = search_languages_str + "," + preferred_language

    for language in search_languages:
        lang = convert_language(language)
        if lang:
            log(__name__, "Language found: '{}' search_languages_str:'{}'".format(lang, search_languages_str))
            if search_languages_str == "":
                search_languages_str = lang
            else:
                search_languages_str = search_languages_str + "," + lang
        else:
            log(__name__, "Language code not found: '{}'".format(language))

    return {"languages": search_languages_str}


def convert_language(language, reverse=False):
    language_list = {
        "English": "en",
        "Portuguese (Brazil)": "pt-br",
        "Portuguese": "pt-pt",
        "Chinese": "zh-cn",
        "Chinese (simplified)": "zh-cn",
        "Chinese (traditional)": "zh-tw"
    }
    reverse_language_list = {v: k for k, v in list(language_list.items())}

    if reverse:
        iterated_list = reverse_language_list
        xbmc_param = xbmc.ENGLISH_NAME
    else:
        iterated_list = language_list
        xbmc_param = xbmc.ISO_639_1

    if language in iterated_list:
        return iterated_list[language]
    else:
        return xbmc.convertLanguage(language, xbmc_param)


def get_flag(language_code):
    language_list = {
        "pt-pt": "pt",
        "pt-br": "pb",
        "zh-cn": "zh",
        "zh-tw": "-"
    }
    return language_list.get(language_code.lower(), language_code)


def clean_feature_release_name(title, release, movie_name=""):
    if not title:
        if not movie_name:
            if not release:
                return ""
            return release
        else:
            if not movie_name[0:4].isnumeric():
                name = movie_name
            else:
                name = movie_name[7:]
    else:
        name = title

    if not release:
        return name

    match_ratio = SequenceMatcher(None, name, release).ratio()
    log(__name__, "name: {}, release: {}, match_ratio: {}".format(name, release, match_ratio))
    if name in release:
        return release
    elif match_ratio > 0.3:
        return release
    else:
        return "{} {}".format(name, release)
