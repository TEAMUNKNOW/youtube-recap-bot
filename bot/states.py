"""UI state machine constants for callback validation."""

from __future__ import annotations

from enum import Enum


class UIState(str, Enum):
    AWAITING_SOURCE = "awaiting_source"
    AWAITING_RIGHTS = "awaiting_rights"
    CHOOSE_MODE = "choose_mode"
    CHOOSE_TTS = "choose_tts"
    CHOOSE_LANGUAGE = "choose_language"
    CHOOSE_PARTITION = "choose_partition"
    CHOOSE_EXPORT = "choose_export"
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"


# Short callback action codes
CB_MODE_RECAP = "m:r"
CB_MODE_TRANSFORM = "m:t"
CB_TTS_EDGE = "t:e"
CB_TTS_ELEVEN = "t:11"
CB_TTS_OPENAI = "t:o"
CB_LANG_HI = "l:hi"
CB_LANG_EN = "l:en"
CB_LANG_BN = "l:bn"
CB_LANG_ES = "l:es"
CB_LANG_ORIG = "l:or"
CB_PART_FULL = "p:f"
CB_PART_SPLIT = "p:s"
CB_EXPORT_TG = "e:tg"
CB_EXPORT_YT = "e:yt"
CB_EXPORT_BOTH = "e:b"
CB_RIGHTS_ACK = "r:ok"
CB_CANCEL = "x:c"


# Navigation/result actions
CB_MENU_HELP = "h"
CB_MENU_HOW = "h:how"
CB_MENU_VOICE = "h:voice"
CB_MENU_SYNC = "h:sync"
CB_MENU_THUMB = "h:thumb"
CB_MENU_OUTPUT = "h:out"
CB_MENU_SETTINGS = "h:set"
CB_RESULT_DETAILS = "r:d"
CB_RESULT_SYNC = "r:s"
CB_RESULT_RAW = "r:r"
CB_RESULT_RETRY = "r:y"
CB_RESULT_BACK = "r:b"
