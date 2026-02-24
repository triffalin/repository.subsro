import sys
import unicodedata

import xbmc
import xbmcaddon
import xbmcgui

from urllib.parse import parse_qsl

__addon__ = xbmcaddon.Addon()
__addon_name__ = __addon__.getAddonInfo("name")
__language__ = __addon__.getLocalizedString


def log(module, msg):
    xbmc.log(f"### [{__addon_name__}:{module}] - {msg}", level=xbmc.LOGDEBUG)


# prints out msg to log and gives Kodi message with msg_id to user if msg_id provided
def error(module, msg_id=None, msg=""):
    if msg:
        message = msg
    elif msg_id:
        message = __language__(msg_id)
    else:
        message = "Add-on error with empty message"
    log(module, message)
    if msg_id:
        xbmcgui.Dialog().ok(__addon_name__, f"{__language__(2103)}\n{__language__(msg_id)}")


def get_params(string=""):
    """
    Parse URL parameters from sys.argv[2] or a provided string.

    v1.0.9: Fixed critical bug where download action params were not parsed.

    Kodi subtitle services receive sys.argv[2] in two formats:
    - Search/manualsearch: "?action=search&languages=Romanian&..."
      (starts with '?', query string only)
    - Download: "plugin://service.subtitles.subsro/?action=download&id=123&..."
      (full plugin URL with query string after '?')

    The old code did sys.argv[2][1:] which only handled the first format.
    For download URLs, [1:] removes the 'p' from 'plugin://' instead of
    finding the '?' delimiter, causing parse_qsl to produce garbage keys
    like 'lugin://service.subtitles.subsro/?action' instead of 'action'.

    This was THE root cause of the "Attempt failed" download error:
    params.get("action") returned None, so download() was never called,
    handle_action() returned without doing anything, endOfDirectory was
    called with no subtitle, and Kodi showed "Attempt failed".
    """
    param = []
    if string == "":
        raw = sys.argv[2]
    else:
        raw = string

    # v1.0.9: Find the '?' and parse everything after it.
    # This handles both formats:
    #   "?action=search&..." -> finds '?' at index 0, parses "action=search&..."
    #   "plugin://.../?action=download&id=123" -> finds '?' at index N, parses "action=download&id=123"
    qmark = raw.find("?")
    if qmark >= 0:
        param_string = raw[qmark + 1:]
    else:
        # No '?' found -- treat entire string as query string (legacy fallback)
        param_string = raw

    if len(param_string) >= 2:
        param = dict(parse_qsl(param_string))

    return param


def normalize_string(str_):
    return unicodedata.normalize("NFKD", str_)
