"""Evaluate pilot readback correctness.

Simple mode: check whether the current value of each required variable
appears in the pilot utterance — either as the literal value or in any
of its standard spoken (phonetic) forms.

Phonetic forms are inferred from the value's pattern:
  - Frequency   "121.805"  → "one two one decimal eight zero five"
  - Flight level "FL150"   → "flight level one five zero"
  - Runway       "25L"     → "two five left"
  - Integer      "5000"    → "five thousand" OR "five zero zero zero"
  - ICAO ident   "SULUS5S" → sequential phonetic regex (Sierra Uniform Lima …)
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import jellyfish


# ---------------------------------------------------------------------------
# ICAO phonetic alphabet + digit pronunciation tables
# ---------------------------------------------------------------------------

# Values are regex fragments (used inside larger patterns), so spelling variants
# that STT routinely emits are accepted via alternations: "alfa" for alpha,
# "juliett" for juliet.  Alternations are wrapped in (?:…) so they stay atomic
# when joined into a sequential identifier pattern.
_LETTER_PHONETICS: Dict[str, str] = {
    'A': '(?:alpha|alfa)', 'B': 'bravo',  'C': 'charlie', 'D': 'delta',
    'E': 'echo',     'F': 'foxtrot',  'G': 'golf',    'H': 'hotel',
    'I': 'india',    'J': '(?:juliet|juliett)', 'K': 'kilo', 'L': 'lima',
    'M': 'mike',     'N': 'november', 'O': 'oscar',   'P': 'papa',
    'Q': 'quebec',   'R': 'romeo',    'S': 'sierra',  'T': 'tango',
    'U': 'uniform',  'V': 'victor',   'W': 'whiskey', 'X': 'x.?ray',
    'Y': 'yankee',   'Z': 'zulu',
}

# Each digit maps to a regex alternation that accepts:
#   - the digit character itself (STT may leave numbers as digits)
#   - the standard English word
#   - the ICAO aviation pronunciation variant
_DIGIT_PHONETICS: Dict[str, str] = {
    '0': r'(?:0|zero)',
    '1': r'(?:1|one|wun)',
    '2': r'(?:2|two|too)',
    '3': r'(?:3|three|tree)',
    '4': r'(?:4|four|fower)',
    '5': r'(?:5|five|fife)',
    '6': r'(?:6|six)',
    '7': r'(?:7|seven)',
    '8': r'(?:8|eight)',
    '9': r'(?:9|nine|niner)',
}

# Simple word list for building spoken altitude / frequency strings.
_DIGIT_WORDS = [
    'zero', 'one', 'two', 'three', 'four',
    'five', 'six', 'seven', 'eight', 'nine',
]

# Separator between digits in a spoken sequence: whitespace/punctuation, and
# optionally a spoken decimal point ("decimal"/"point"/"dot") so a frequency
# read "wun too fife decimal tree fife zero" matches the digit run "125350".
_DIGIT_SEQ_SEP = r'[\s,.\-]*(?:(?:decimal|point|dot|comma)[\s,.\-]*)?'

# Any single spoken digit (bare digit, English word, or ICAO variant). Used as a
# negative-lookahead anchor so a trailing-zero-dropped frequency only matches
# when no further digit follows (else a wrong final digit would match the prefix).
_ANY_DIGIT_WORD = (
    r'(?:[0-9]|zero|one|wun|won|two|too|three|tree|four|fower|'
    r'five|fife|six|seven|eight|nine|niner)'
)


# ---------------------------------------------------------------------------
# Spoken-form generators
# ---------------------------------------------------------------------------

# Speech-to-text punctuates freely inside spoken numbers — "one-hundred",
# "two, five, left", "one—hundred". Any of these may separate two words of a
# spoken form, and so may nothing at all.
_WORD_SEP = r'[\s,.\-–—]*'

_WORD_TO_DIGIT: Dict[str, str] = {word: str(i) for i, word in enumerate(_DIGIT_WORDS)}


def _tolerant_form_regex(form: str) -> str:
    """Regex for a spoken form that tolerates STT punctuation and ICAO variants.

    'flight level one hundred' also matches "flight level one-hundred" and
    "flight level wun hundred". Anchored on word boundaries so a form never
    matches half of a longer number ("one zero zero" inside "1003").
    """
    parts = []
    for token in form.split():
        digit = _WORD_TO_DIGIT.get(token.lower())
        parts.append(_DIGIT_PHONETICS[digit] if digit else re.escape(token))
    return r'(?<!\w)' + _WORD_SEP.join(parts) + r'(?!\w)'


def _icao_digits(value: str) -> str:
    """Spell each digit individually: '2118' → 'two one one eight'."""
    return ' '.join(_DIGIT_WORDS[int(c)] for c in value if c.isdigit())


def _altitude_speak(value: str) -> str:
    """
    Group-thousands pronunciation: '5000' → 'five thousand',
    '1500' → 'one thousand five hundred'.
    Returns empty string when value is not a plain integer.
    """
    try:
        n = int(value)
    except ValueError:
        return ''
    if n == 0:
        return 'zero'
    parts: List[str] = []
    if n >= 1000:
        th = n // 1000
        if th < 10:
            parts.append(f'{_DIGIT_WORDS[th]} thousand')
        else:
            # e.g. 10000 → "one zero thousand" (uncommon but safe)
            parts.append(f'{_icao_digits(str(th))} thousand')
        rem = n % 1000
        if rem >= 100:
            parts.append(f'{_DIGIT_WORDS[rem // 100]} hundred')
    else:
        parts.append(_icao_digits(str(n)))
    return ' '.join(parts)


def _frequency_speak(value: str) -> str:
    """'121.805' → 'one two one decimal eight zero five'."""
    parts = value.split('.')
    integer_spoken = ' '.join(_DIGIT_WORDS[int(d)] for d in parts[0] if d.isdigit())
    if len(parts) > 1:
        decimal_spoken = ' '.join(_DIGIT_WORDS[int(d)] for d in parts[1] if d.isdigit())
        return f'{integer_spoken} decimal {decimal_spoken}'
    return integer_spoken


def _flight_level_speak(value: str) -> str:
    """'FL150' → 'flight level one five zero'."""
    digits = re.sub(r'^FL', '', value, flags=re.IGNORECASE)
    spoken = ' '.join(_DIGIT_WORDS[int(d)] for d in digits if d.isdigit())
    return f'flight level {spoken}'


def _runway_speak(value: str) -> str:
    """'25L' → 'two five left', '07' → 'zero seven'."""
    m = re.match(r'^(\d{2})([LCR]?)$', value, re.IGNORECASE)
    if not m:
        return ''
    digits = ' '.join(_DIGIT_WORDS[int(d)] for d in m.group(1))
    suffix = {'L': 'left', 'R': 'right', 'C': 'center'}.get(m.group(2).upper(), '')
    return f'{digits} {suffix}'.strip()


def _icao_identifier_regex(value: str) -> str:
    """
    Build a regex that matches the ICAO phonetic spelling of an alphanumeric
    identifier spoken character-by-character (SID names, waypoints, etc.).

    'SULUS5S' → pattern matching
        "Sierra Uniform Lima Uniform Sierra 5 Sierra"  (or with commas, etc.)

    Characters are separated by an optional whitespace/punctuation separator
    so natural TTS output such as "Sierra, Uniform, Lima, …" is accepted.
    """
    parts: List[str] = []
    for ch in value.upper():
        if ch in _LETTER_PHONETICS:
            parts.append(_LETTER_PHONETICS[ch])
        elif ch in _DIGIT_PHONETICS:
            parts.append(_DIGIT_PHONETICS[ch])
        else:
            parts.append(re.escape(ch))
    # Allow optional whitespace, commas, hyphens, dots between words
    sep = r'[\s,.\-]*'
    return sep.join(parts)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def spoken_forms(value: str) -> List[str]:
    """
    Return all accepted *literal* spoken forms for a variable value.
    Each item is a plain string (not a regex).  The caller may also need
    _icao_identifier_regex() for multi-character ICAO idents.
    """
    v = value.strip()
    forms: List[str] = [v.lower()]  # literal is always accepted

    if re.match(r'^\d{3}\.\d+$', v):
        forms.append(_frequency_speak(v))

    if re.match(r'^FL\d+$', v, re.IGNORECASE):
        forms.append(_flight_level_speak(v))
        # Round flight levels are spoken in "hundreds": FL100 → "flight level
        # one hundred", FL200 → "two hundred".  Accept with or without the
        # leading "flight level".
        fl_num = int(re.sub(r'^FL', '', v, flags=re.IGNORECASE))
        if fl_num and fl_num % 100 == 0 and fl_num // 100 < 10:
            forms.append(f'flight level {_DIGIT_WORDS[fl_num // 100]} hundred')
            forms.append(f'{_DIGIT_WORDS[fl_num // 100]} hundred')

    if re.match(r'^\d{2}[LCR]?$', v, re.IGNORECASE):
        s = _runway_speak(v)
        if s:
            forms.append(s)

    if re.match(r'^\d+$', v):
        forms.append(_icao_digits(v))     # digit-by-digit: "five zero zero zero"
        forms.append(_altitude_speak(v))  # grouped: "five thousand"

    return [f for f in forms if f]


# A place-shaped value: letters, spaces and the punctuation that occurs in
# airport city names ("Frankfurt am Main", "'s-Hertogenbosch"). Digits exclude
# squawks, altitudes and frequencies, so those never hit the airport dataset.
_PLACE_SHAPED = re.compile(r"^[^\W\d_][^\W\d_ .\-']*(?:[ .\-'][^\W\d_][^\W\d_ .\-']*)*$")


@lru_cache(maxsize=2048)
def place_aliases(value: str) -> Tuple[str, ...]:
    """Accepted alternative spoken values for a place-valued field.

    An airport is read back by ICAO code or by city name, in English or German
    — all of them correct. The aliases come from the bundled airport dataset,
    so this holds for every airport rather than a hardcoded handful.

    Two guards keep this off the hot path: a value that is not place-shaped (a
    squawk, a level, a flight level) never reaches the dataset, and neither does
    anything shorter than an ICAO code — a taxiway named "A" would otherwise
    trigger a full scan of the airport table on every readback field.
    """
    v = value.strip()
    if len(v) < 4 or not _PLACE_SHAPED.match(v):
        return ()
    try:
        from app.airport_data import spoken_place_aliases
        return tuple(
            a for a in spoken_place_aliases(v) if a.strip().lower() != v.lower()
        )
    except Exception:  # dataset missing/unreadable — fall back to literal only
        return ()


def _value_is_icao_ident(value: str) -> bool:
    """
    True when the value looks like a multi-character ICAO alphanumeric
    identifier (SID, STAR, waypoint, …) that would be spoken phonetically.

    Must contain at least one letter and consist only of A-Z / 0-9.
    Excludes pure-digit strings (those are altitudes/squawks) and FL* codes
    (handled by _flight_level_speak).
    """
    v = value.strip().upper()
    return (
        bool(re.match(r'^[A-Z0-9]{2,}$', v))
        and bool(re.search(r'[A-Z]', v))
        and not re.match(r'^FL\d+$', v)
        and not re.match(r'^\d{2}[LCR]?$', v)
    )


# ---------------------------------------------------------------------------
# Fuzzy SID/STAR matching
# ---------------------------------------------------------------------------
# Named procedures (SIDs/STARs) are spoken as a *pronounceable word* plus a
# revision digit and a final letter — "TOBAK2E" is said "Tobak two echo", not
# spelled "Tango Oscar Bravo Alpha Kilo two echo". STT then mangles the word
# ("Tobacco too Echo", "Maroon seven foxtrot"). Neither the literal nor the
# letter-by-letter phonetic regex matches that. We therefore decompose the
# ident into stem + digit(s) + final letter and match each part leniently: the
# digit and letter strictly (digit/word, letter/phonetic), the stem fuzzily
# via jellyfish (Metaphone equality or a high Jaro-Winkler similarity).

# Jaro-Winkler floor for accepting a stem.  tobak~toback=0.97, tobak~tobac=0.92,
# tobak~todac=0.79, while unrelated words sit at <=0.55.  The digit+letter anchors
# corroborate the match, so a fairly low floor stays safe from false positives.
_STEM_SIMILARITY_THRESHOLD = 0.78

# STT homophones for spoken revision digits — "two" is routinely transcribed as
# "to", "four" as "for", "zero" as "oh", etc.  Includes the ICAO radio variants
# (wun, tree, fife, niner, fower) so a SID read "Cindy wun Alfa" matches CINDY1A.
# Used (token-based, so "to" must be a whole word, not the "to" inside "tobacco")
# when checking the SID revision.
_DIGIT_HOMOPHONES: Dict[str, set] = {
    '0': {'0', 'zero', 'oh', 'o'},
    '1': {'1', 'one', 'won', 'wun'},
    '2': {'2', 'two', 'too', 'to'},
    '3': {'3', 'three', 'tree'},
    '4': {'4', 'four', 'for', 'fore', 'fower'},
    '5': {'5', 'five', 'fife'},
    '6': {'6', 'six'},
    '7': {'7', 'seven'},
    '8': {'8', 'eight', 'ate'},
    '9': {'9', 'nine', 'niner'},
}


def _decompose_ident(value: str) -> Optional[Tuple[str, str, str]]:
    """'TOBAK2E' → ('TOBAK', '2', 'E'); None when not SID-shaped."""
    m = re.match(r'^([A-Z]{2,})(\d{1,2})([A-Z])$', value.strip().upper())
    return (m.group(1), m.group(2), m.group(3)) if m else None


def _fuzzy_ident_match(value: str, utterance: str) -> Optional[str]:
    """Lenient match for a word-pronounced SID/STAR.

    Requires the stem (fuzzy), every digit, and the final letter to be present.
    Returns a human-readable description of the match, or None.
    """
    parts = _decompose_ident(value)
    if parts is None:
        return None
    stem, digits, letter = parts

    # Final letter: phonetic word or the bare letter as a standalone token.
    letter_word = _LETTER_PHONETICS.get(letter, re.escape(letter.lower()))
    if not re.search(rf'\b(?:{letter_word}|{re.escape(letter.lower())})\b', utterance, re.IGNORECASE):
        return None

    # Digit(s): each revision digit must appear, in order, as a digit or one of
    # its spoken homophones.  Token-based so "to" (the homophone of two) is only
    # accepted as a whole word, never the "to" inside "tobacco".
    tokens = re.findall(r'[a-z]+|\d', utterance.lower())
    search_from = 0
    for d in digits:
        forms = _DIGIT_HOMOPHONES.get(d, {d})
        found_at = next((i for i in range(search_from, len(tokens)) if tokens[i] in forms), None)
        if found_at is None:
            return None
        search_from = found_at + 1

    # Stem: best spoken word by Metaphone equality or Jaro-Winkler similarity.
    stem_l = stem.lower()
    stem_mp = jellyfish.metaphone(stem_l)
    best_word: Optional[str] = None
    best_score = 0.0
    for word in re.findall(r'[a-z]{3,}', utterance.lower()):
        if word == stem_l:
            best_word, best_score = word, 1.0
            break
        score = jellyfish.jaro_winkler_similarity(word, stem_l)
        if stem_mp and jellyfish.metaphone(word) == stem_mp:
            score = max(score, 0.95)
        if score > best_score:
            best_word, best_score = word, score

    if best_word is None or best_score < _STEM_SIMILARITY_THRESHOLD:
        return None

    return f'fuzzy_sid:{best_word} {digits} {letter.lower()}'


# ---------------------------------------------------------------------------
# Fuzzy waypoint matching
# ---------------------------------------------------------------------------
# ICAO five-letter name-codes (BIBAX, SULUS, ANEKI) are built to be pronounceable
# and are spoken as words, never spelled — so STT returns English that merely
# sounds like them ("Bibaks", "bee backs", "Sullus"). Neither the literal nor the
# letter-by-letter phonetic regex matches that.
#
# Unlike a SID there is no digit or final letter to anchor the match, so the
# safety argument rests elsewhere: ICAO assigns name-codes to be phonetically
# distinct from one another, which makes Metaphone equality a strong signal —
# every mangling of a waypoint shares its Metaphone, while two different
# waypoints do not. Jaro-Winkler carries the rest, with the floor set above the
# worst observed distinct-waypoint pair (OBOKA/TOBAK at 0.78).

_WAYPOINT_SIMILARITY_THRESHOLD = 0.88

# Standard radiotelephony vocabulary. These words occur in ordinary
# transmissions and must never stand in for a spoken waypoint: the name-code
# RIDAR is homophonous with "radar", so without this list "radar contact" would
# satisfy a readback that never named the waypoint at all.
_RT_STOPWORDS = frozenset("""
    radar contact identified direct climb climbing descend descending maintain
    passing initially level flight cleared clear takeoff landing runway squawk
    ident contact tower ground apron delivery departure arrival approach centre
    center roger wilco affirm affirmative negative standby request require
    holding hold short line wait wind altitude heading turn left right speed
    knots feet thousand hundred decimal information report expect continue
    proceed vector vectors established intercept final route stand gate taxi
    push pushback start startup break correction good day morning afternoon
    evening again say repeat with without ready number behind after before
    cross crossing traffic mayday pan emergency fuel souls board
    alpha bravo charlie delta echo foxtrot golf hotel india juliet juliett kilo
    lima mike november oscar papa quebec romeo sierra tango uniform victor
    whiskey xray yankee zulu
    zero one two three four five six seven eight nine niner wun tree fife fower
""".split())


def _value_is_waypoint_name(value: str) -> bool:
    """A pronounceable five/six-letter ICAO name-code, spoken as a word.

    Mirrors the frontend's TTS rule, which title-cases exactly this shape so it
    is read aloud as a word rather than spelled — the two must agree, or the
    matcher would grade something the controller never said that way.
    """
    return bool(re.match(r'^[A-Z]{5,6}$', value.strip().upper()))


def _waypoint_candidates(utterance: str) -> List[str]:
    """Spoken words that could carry a waypoint name.

    Adjacent pairs are joined as well, because STT routinely splits a name-code
    into two English words ("bee backs" for BIBAX). Standard R/T vocabulary is
    excluded; a pair is only considered when neither half is R/T vocabulary, so
    "radar contact" never becomes the candidate "radarcontact".
    """
    # Single-letter words stay in the list: STT writes ANEKI as "a necky", so
    # the pair join needs them even though they are never a candidate alone.
    words = re.findall(r'[a-z]+', utterance.lower())
    candidates = [w for w in words if len(w) >= 3 and w not in _RT_STOPWORDS]
    for left, right in zip(words, words[1:]):
        if left in _RT_STOPWORDS or right in _RT_STOPWORDS:
            continue
        candidates.append(left + right)
    return candidates


def _fuzzy_waypoint_match(value: str, utterance: str) -> Optional[str]:
    """Lenient match for a waypoint spoken as a word. Returns a description."""
    if not _value_is_waypoint_name(value):
        return None
    target = value.strip().lower()
    target_mp = jellyfish.metaphone(target)

    best_word: Optional[str] = None
    best_score = 0.0
    for word in _waypoint_candidates(utterance):
        score = jellyfish.jaro_winkler_similarity(word, target)
        if target_mp and jellyfish.metaphone(word) == target_mp:
            score = max(score, 0.95)
        if score > best_score:
            best_word, best_score = word, score

    if best_word is None or best_score < _WAYPOINT_SIMILARITY_THRESHOLD:
        return None
    return f'fuzzy_waypoint:{best_word}'


# ---------------------------------------------------------------------------
# "Correction" — the pilot withdrawing what they just said
# ---------------------------------------------------------------------------
# Standard phraseology: everything after CORRECTION supersedes what came before
# it. Searching the whole transmission for each expected value ignores that, so
# a pilot who read back the right squawk, said "correction", then read back a
# different one was graded correct.
#
# A correction only supersedes the item it actually restates — correcting the
# squawk must leave a correctly read-back altitude standing. Which item that is
# comes from the keyword that introduced the value ("squawk 2341", "climb
# 5000", "via BIBAX1N"): if the same keyword reappears after the marker, the
# pilot restated that item and the earlier value no longer counts.

_CORRECTION_RE = re.compile(
    r'\b(?:negative\W+)?correction\b'
    r'|\bberichtigung\b|\bkorrektur\b',
    re.IGNORECASE,
)

# Keywords that introduce a clearance item on the radio.
_ITEM_KEYWORDS = (
    'squawk', 'climb', 'climbing', 'descend', 'descending', 'maintain',
    'passing', 'initially', 'runway', 'contact', 'direct', 'via', 'cleared',
    'heading', 'qnh', 'altitude', 'level', 'stand', 'gate', 'taxi', 'wind',
    'information', 'expect', 'hold', 'cross', 'monitor',
)

# How far back to look for the keyword that introduced a value.
_KEYWORD_LOOKBEHIND = 40


def _split_at_correction(utterance: str) -> Optional[Tuple[str, str]]:
    """``(before, after)`` around the last correction marker, or None."""
    markers = list(_CORRECTION_RE.finditer(utterance))
    if not markers:
        return None
    last = markers[-1]
    return utterance[:last.start()], utterance[last.end():]


def _value_span(expected_str: str, text: str) -> Optional[Tuple[int, int]]:
    """Where the expected value appears in ``text`` — literal/spoken/phonetic."""
    for form in spoken_forms(expected_str):
        if not form:
            continue
        m = re.search(_tolerant_form_regex(form), text, re.IGNORECASE)
        if m:
            return m.span()
    digits = re.sub(r'\D', '', expected_str)
    if len(digits) >= 2:
        seq = _DIGIT_SEQ_SEP.join(_DIGIT_PHONETICS[d] for d in digits)
        m = re.search(seq, text, re.IGNORECASE)
        if m:
            return m.span()
    if _value_is_icao_ident(expected_str):
        try:
            m = re.search(_icao_identifier_regex(expected_str), text, re.IGNORECASE)
            if m:
                return m.span()
        except re.error:
            pass
    return None


def _introducing_keyword(expected_str: str, text: str) -> Optional[str]:
    """The R/T keyword that introduced this value, e.g. "squawk" for "squawk 2341"."""
    span = _value_span(expected_str, text)
    if span is None:
        return None
    window = text[max(0, span[0] - _KEYWORD_LOOKBEHIND):span[0]].lower()
    found = [(window.rfind(k), k) for k in _ITEM_KEYWORDS]
    best = max((pos, k) for pos, k in found)
    return best[1] if best[0] >= 0 else None


def _superseded_by_correction(expected_str: str, before: str, after: str) -> bool:
    """True when the correction restated this item, withdrawing the earlier value.

    Conservative on purpose: without a keyword to identify the item, the value
    is left standing rather than failing a readback that may well be correct.
    """
    keyword = _introducing_keyword(expected_str, before)
    if keyword is None:
        return False
    return bool(re.search(rf'\b{re.escape(keyword)}\b', after, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Core evaluator
# ---------------------------------------------------------------------------

def _match_readback_value(
    expected_str: str,
    utterance: str,
) -> Tuple[bool, Optional[str], List[str]]:
    """Match an expected value — or any equivalent name for it — in the utterance.

    Returns ``(matched, matched_via, accepted_forms)``. Shared by scalar and
    list-valued readback fields so they grade identically.

    Place-valued fields (destination, alternate) accept the ICAO code and the
    city name in either language, because all of those are a correct readback of
    the same airport.
    """
    matched, matched_via, forms = _match_one_value(expected_str, utterance)
    if matched or not expected_str:
        return matched, matched_via, forms

    for alias in place_aliases(expected_str):
        alias_matched, alias_via, alias_forms = _match_one_value(alias, utterance)
        forms = forms + alias_forms
        if alias_matched:
            return True, f"place_alias:{alias} ({alias_via})", forms

    return False, None, forms


def _match_one_value(
    expected_str: str,
    utterance: str,
) -> Tuple[bool, Optional[str], List[str]]:
    """Match one literal expected value against the utterance."""
    forms = spoken_forms(expected_str) if expected_str else []
    matched = False
    matched_via: Optional[str] = None

    # 1. Check literal value and all static spoken forms
    for form in forms:
        if form and re.search(_tolerant_form_regex(form), utterance, re.IGNORECASE):
            matched = True
            matched_via = form
            break

    # 1b. Digit-by-digit phonetic, accepting ICAO radio variants
    #     (wun, tree, fife, niner, fower …) which the static spoken forms
    #     above don't include — e.g. QNH 1013 read as "wun zero wun tree",
    #     or frequency 125.350 as "wun too fife decimal tree fife zero"
    #     (the separator optionally swallows a spoken "decimal"/"point").
    if not matched and expected_str:
        digits_only = re.sub(r'\D', '', expected_str)
        # A parallel runway is only read back correctly with its side. Without
        # this guard the digit fallback drops the L/C/R and "two five right"
        # would satisfy an expected 25L.
        runway_side = re.match(r'^\d{2}([LCR])$', expected_str, re.IGNORECASE)
        side_suffix = ''
        if runway_side:
            side_word = {'L': 'left', 'R': 'right', 'C': '(?:center|centre)'}[runway_side.group(1).upper()]
            side_suffix = rf'{_DIGIT_SEQ_SEP}(?:{runway_side.group(1)}|{side_word})'
        # Frequencies are routinely read without the trailing zero: "125.35"
        # for 125.350, "118.7" for 118.700.  Accept the value with trailing
        # zeros of the decimal dropped, keeping at least the 3-digit MHz part.
        # (candidate digits, must_end) — the trailing-zero-dropped form must
        # not be followed by another digit, so a wrong final digit is caught.
        digit_candidates = [(digits_only, False)]
        if re.match(r'^\d{3}\.\d+$', expected_str) and digits_only.endswith('0'):
            trimmed = digits_only.rstrip('0')
            if len(trimmed) >= 3 and trimmed != digits_only:
                digit_candidates.append((trimmed, True))
        for cand, must_end in digit_candidates:
            if len(cand) >= 2:
                seq = _DIGIT_SEQ_SEP.join(_DIGIT_PHONETICS[d] for d in cand) + side_suffix
                if must_end:
                    seq += rf'(?!{_DIGIT_SEQ_SEP}{_ANY_DIGIT_WORD})'
                if re.search(seq, utterance, re.IGNORECASE):
                    matched = True
                    matched_via = "digit_phonetic"
                    break

    # 2. Check ICAO phonetic sequential pattern for identifiers
    if not matched and expected_str and _value_is_icao_ident(expected_str):
        pattern = _icao_identifier_regex(expected_str)
        try:
            if re.search(pattern, utterance, re.IGNORECASE):
                matched = True
                matched_via = "icao_phonetic"
        except re.error:
            pass  # malformed pattern — skip

    # 3. Fuzzy match for word-pronounced SID/STAR names ("Tobak two echo"
    #    transcribed as "Tobacco too Echo").
    if not matched and expected_str:
        fuzzy = _fuzzy_ident_match(expected_str, utterance)
        if fuzzy is not None:
            matched = True
            matched_via = fuzzy

    # 4. Fuzzy match for a bare waypoint name-code spoken as a word
    #    ("BIBAX" transcribed as "Bibaks" / "bee backs").
    if not matched and expected_str:
        fuzzy_wp = _fuzzy_waypoint_match(expected_str, utterance)
        if fuzzy_wp is not None:
            matched = True
            matched_via = fuzzy_wp

    return matched, matched_via, forms


def evaluate_readback_simple(
    pilot_utterance: str,
    readback_required: List[str],
    variables: Dict[str, Any],
) -> Tuple[bool, List[str], List[Dict[str, Any]]]:
    """
    Returns (passed, missing_fields, reports).

    A field passes if the current variable value is found in the utterance
    as any of:
      1. The literal value (case-insensitive substring match)
      2. Any standard spoken form (frequency, FL, runway, altitude, …)
      3. For ICAO alphanumeric idents: the ICAO phonetic sequential regex
         (e.g. "SULUS5S" → Sierra Uniform Lima Uniform Sierra 5 Sierra)

    ``reports`` is a per-field diagnostic for debugging: what was expected, the
    accepted spoken forms tried, whether it matched, and which form matched the
    utterance (so the comm log can show "expected 25R ← matched 'two five right'").
    """
    missing: List[str] = []
    reports: List[Dict[str, Any]] = []
    utterance = pilot_utterance
    correction = _split_at_correction(utterance)

    def match_field(expected_str: str) -> Tuple[bool, Optional[str], List[str]]:
        """Match one value, honouring a "correction" the pilot transmitted."""
        matched, matched_via, forms = _match_readback_value(expected_str, utterance)
        if not matched or correction is None:
            return matched, matched_via, forms
        before, after = correction
        # Still stated after the correction — nothing was withdrawn.
        if _match_readback_value(expected_str, after)[0]:
            return matched, f"{matched_via} (after correction)", forms
        if _superseded_by_correction(expected_str, before, after):
            return False, None, forms
        return matched, matched_via, forms

    for field in readback_required:
        expected = variables.get(field)

        # List-valued field: every element must be read back. An empty list
        # requires nothing (e.g. crossing_runways == [] when the route crosses
        # no runways), so the field passes trivially.
        if isinstance(expected, list):
            for item in expected:
                item_str = "" if item is None else str(item).strip()
                if not item_str:
                    continue
                matched, matched_via, forms = match_field(item_str)
                reports.append({
                    "field": field,
                    "expected": item_str,
                    "matched": matched,
                    "matched_via": matched_via,
                    "accepted_forms": list(dict.fromkeys(forms)),
                })
                if not matched:
                    missing.append(f"{field}:{item_str}")
            continue

        expected_str = "" if expected is None else str(expected).strip()
        matched, matched_via, forms = match_field(expected_str)

        report: Dict[str, Any] = {
            "field": field,
            "expected": expected_str,
            "matched": matched,
            "matched_via": matched_via,
            # Distinct accepted forms tried (literal + phonetic variants).
            "accepted_forms": list(dict.fromkeys(forms)),
        }
        if expected is None:
            report["note"] = "variable not set"
        reports.append(report)

        if not matched:
            missing.append(field)

    return (len(missing) == 0), missing, reports


def check_readback(
    pilot_utterance: str,
    readback_required: List[str],
    readback_mode: str,
    variables: Dict[str, Any],
) -> Tuple[bool, List[str], List[Dict[str, Any]]]:
    """
    Dispatch to the appropriate readback evaluator.

    Returns (passed, missing_fields, reports).  ``reports`` is empty when no
    readback is required.
    """
    if readback_mode == "none" or not readback_required:
        return True, [], []

    if readback_mode in ("simple", "strict"):
        # strict is reserved for future stricter matching; uses simple for now
        return evaluate_readback_simple(pilot_utterance, readback_required, variables)

    return True, [], []
