# -*- coding: utf-8 -*-
import xbmc
import xbmcaddon

ADDON_ID = "service.subtitles.subsro"
LOG_DEBUG = xbmc.LOGDEBUG
LOG_INFO = xbmc.LOGINFO
LOG_WARNING = xbmc.LOGWARNING
LOG_ERROR = xbmc.LOGERROR


def _get_logging_enabled():
    try:
        return xbmcaddon.Addon(ADDON_ID).getSettingBool("enable_logging")
    except Exception:
        return False


def _log(message, level=LOG_DEBUG):
    xbmc.log("[{addon}] {msg}".format(addon=ADDON_ID, msg=message), level=level)


def debug(message):
    if _get_logging_enabled():
        _log(message, LOG_DEBUG)


def info(message):
    _log(message, LOG_INFO)


def warning(message):
    _log(message, LOG_WARNING)


def error(message):
    _log(message, LOG_ERROR)
