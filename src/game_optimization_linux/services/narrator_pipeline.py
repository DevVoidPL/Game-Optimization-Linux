"""Bounded asynchronous pipeline for local subtitle narration."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import logging
import os
from queue import SimpleQueue
import re
from threading import current_thread, RLock
import time
from typing import Protocol
import unicodedata
from uuid import uuid4

from game_optimization_linux.models.narrator import (
    CaptureFrame,
    CaptureState,
    NarratorEvent,
    NarratorGameSettings,
    OcrDecisionObservation,
    NarratorSessionSnapshot,
    NarratorSessionStatus,
    NarratorSourceMode,
    NarratorSubtitleLanguageMode,
    OcrResult,
    PcmAudio,
    TranslationResult,
)

from .narrator_capture import CaptureRequest, ScreenCaptureProvider
from .narrator_ocr import OCR_STRONG_LINE_CONFIDENCE
from .narrator_persistence import TranslationCache


logger = logging.getLogger(__name__)

OCR_STABLE_OBSERVATIONS = 2
OCR_SIMILARITY_THRESHOLD = 0.88
OCR_STABILITY_WINDOW_SECONDS = 1.25
OCR_DECISION_HISTORY_LIMIT = 20
# The last line read, re-read with an OCR slip: keys (identity without spaces)
# of at least OCR_DUPLICATE_MIN_KEY_LENGTH characters that differ by at most
# OCR_DUPLICATE_MAX_EDITS added, removed or changed characters.
OCR_DUPLICATE_MAX_EDITS = 1
OCR_DUPLICATE_MIN_KEY_LENGTH = 10
# Gate and deduplication must agree when a subtitle really disappeared.
SUBTITLE_DISAPPEARANCE_OBSERVATIONS = 3


class SubtitleSource(Protocol):
    source_id: str

    def available_for(self, game_key: str) -> bool: ...

    def start(
        self,
        game_key: str,
        callback: Callable[[str, float], None],
    ) -> None: ...

    def stop(self) -> None: ...


class OcrProvider(Protocol):
    provider_id: str
    available: bool

    def recognize(self, frame: CaptureFrame, *, language: str) -> OcrResult: ...


class TranslationProvider(Protocol):
    provider_id: str
    available: bool

    def translate(
        self,
        text: str,
        *,
        source_language: str,
        target_language: str,
        profile_id: str,
    ) -> TranslationResult: ...

    def cancel(self) -> None: ...


class TtsProvider(Protocol):
    provider_id: str
    available: bool

    def synthesize(
        self,
        text: str,
        *,
        language: str,
        voice_id: str,
        speech_rate: float,
    ) -> PcmAudio: ...

    def cancel(self) -> None: ...


class NarratorAudioOutput(Protocol):
    provider_id: str
    available: bool

    def play(
        self,
        audio: PcmAudio,
        *,
        volume: float,
        request_id: int,
        started_callback: Callable[[float], None],
        completed_callback: Callable[[], None],
        error_callback: Callable[[str], None],
        text: str = "",
    ) -> None: ...

    def stop(self) -> None: ...


class GameActivityProvider(Protocol):
    def is_active(self, game_key: str) -> bool | None: ...


def normalize_subtitle(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text))
    printable = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in normalized
    )
    return " ".join(printable.split())


_EDGE_SYMBOLS = "|│¦_=+<>*"
_COMPARE_WORD_SPLIT = re.compile(r"[^0-9a-ząćęłńóśźż]+")
_NEGATION_WORDS = frozenset(
    {"nie", "bez", "brak", "no", "not", "never", "cannot", "cant", "dont", "wont"}
)


def subtitle_identity(text: str) -> str:
    """Normalize case, punctuation and OCR-lost accents for comparisons."""
    normalized = normalize_subtitle(text).casefold().translate(
        str.maketrans({"ł": "l", "đ": "d"})
    )
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.category(character).startswith("M")
    )
    return " ".join(
        "".join(
            character if character.isalnum() else " " for character in normalized
        ).split()
    )


_TTS_PUNCTUATION = ".,!?;:…"
_TTS_KEPT_SYMBOLS = frozenset(".,!?;:'\"()… ")
_TTS_QUOTE_MAP = str.maketrans({"„": '"', "”": '"', "“": '"', "’": "'", "‘": "'"})
_TTS_DASHES = "-\N{EN DASH}\N{EM DASH}"
_TTS_STANDALONE_DASH = "\x00"
_TTS_PUNCTUATION_RUN = re.compile(r"(?:[.,!?;:…]\s*){2,}")
_TTS_SPEAKER_LABEL = re.compile(
    r"^\s*([^\W\d_][^\s\d:]*(?:\s+[^\W\d_][^\s\d:]*){0,2}):\s+(.+)$",
    re.DOTALL,
)


def _collapse_punctuation_run(match: re.Match[str]) -> str:
    run = match.group(0)
    compact = run.replace(" ", "")
    if "…" in compact or "..." in compact:
        value = "..."
    else:
        value = compact[0]
    return value + (" " if run[-1].isspace() else "")


def sanitize_tts_text(text: str) -> str:
    """Final character filter for speech only; display text is unchanged."""

    value = normalize_subtitle(text).translate(_TTS_QUOTE_MAP)
    characters: list[str] = []
    for index, character in enumerate(value):
        previous = value[index - 1] if index else ""
        following = value[index + 1] if index + 1 < len(value) else ""
        if character.isalnum() or character in _TTS_KEPT_SYMBOLS:
            characters.append(character)
        elif character == "%":
            characters.append("%" if previous.isdigit() else " ")
        elif character in _TTS_DASHES:
            inside_word = previous.isalnum() and following.isalnum()
            characters.append(character if inside_word else _TTS_STANDALONE_DASH)
        else:
            characters.append(" ")
    value = "".join(characters)
    # Tokens made only of quotes/brackets/punctuation carry no speech.
    tokens = [
        token
        for token in value.split()
        if any(character.isalnum() for character in token)
        or token.strip("'\"()") and all(
            character in _TTS_PUNCTUATION or character == _TTS_STANDALONE_DASH
            for character in token.strip("'\"()")
        )
    ]
    value = " ".join(tokens).strip(" " + _TTS_STANDALONE_DASH)
    value = value.replace(_TTS_STANDALONE_DASH, ",")
    value = re.sub(r"\s+([.,!?;:…])", r"\1", value)
    value = _TTS_PUNCTUATION_RUN.sub(_collapse_punctuation_run, value)
    value = re.sub(r"\.{4,}", "...", value)
    value = value.lstrip(_TTS_PUNCTUATION + ") ").strip()
    value = " ".join(value.split())
    if not any(character.isalpha() for character in value):
        return ""
    return value


def strip_speaker_label(text: str) -> str:
    """Remove a leading ``Name:`` speaker label of at most three words."""

    match = _TTS_SPEAKER_LABEL.match(text)
    if match is None:
        return text
    label, remainder = match.group(1), match.group(2).strip()
    if not all(word[:1].isupper() for word in label.split()):
        return text
    first_letter = next(
        (character for character in remainder if character.isalpha()), ""
    )
    if not first_letter.isupper():
        return text
    if sum(character.isalpha() for character in remainder) < 2:
        return text
    return remainder


def _trim_symbolic_edges(text: str) -> str:
    """Remove only language-free symbols from the outside of a phrase."""

    value = normalize_subtitle(text).strip()
    while value and value[0] in _EDGE_SYMBOLS:
        value = value[1:].lstrip()
    while value and value[-1] in _EDGE_SYMBOLS:
        value = value[:-1].rstrip()
    return value


def _is_proven_mixed_noise(token: str) -> bool:
    """Recognise structural OCR garbage without deleting identifiers like R2D2."""

    compact = token.strip().strip(".,!?…:;\"„”()[]{}")
    letters = sum(character.isalpha() for character in compact)
    digits = sum(character.isdigit() for character in compact)
    if not letters or not digits:
        return False
    if any(
        not character.isalnum() and character not in {"'", "’", "-"}
        for character in compact
    ):
        return True
    return bool(
        re.match(r"^\d{2,}[^\W\d_]+$", compact, flags=re.UNICODE)
        and digits / max(1, letters + digits) >= 0.50
    )


def _drop_proven_noise_tokens(text: str) -> str:
    kept: list[str] = []
    for token in text.split():
        symbolic = token.strip("'\"„”")
        if symbolic and all(character in _EDGE_SYMBOLS for character in symbolic):
            continue
        if not _is_proven_mixed_noise(token):
            kept.append(token)
    return " ".join(kept)


def _comparison_words(identity: str) -> list[str]:
    return [word for word in _COMPARE_WORD_SPLIT.split(identity) if word]


def _numeric_token(token: str) -> bool:
    words = _comparison_words(subtitle_identity(token))
    return bool(words) and all(word.isdigit() for word in words)


def _semantic_number_words(text: str) -> list[str]:
    """Comparison words without numbers proven detached from the sentence.

    A leading number run directly followed by a capitalised word, or a trailing
    number run after terminal punctuation, is OCR debris next to the line
    (``4 To nie…``, ``Dzięki. 4``). ``Pokój 101`` keeps its number because it
    is attached to the preceding word.
    """

    tokens = [
        token
        for token in normalize_subtitle(text).split()
        if _comparison_words(subtitle_identity(token))
    ]
    start = 0
    while start < len(tokens) and _numeric_token(tokens[start]):
        start += 1
    if not (start and start < len(tokens) and tokens[start][:1].isupper()):
        start = 0
    end = len(tokens)
    while end > start and _numeric_token(tokens[end - 1]):
        end -= 1
    if not (
        end < len(tokens)
        and end > start
        and tokens[end - 1].rstrip("\"'”)").endswith((".", "!", "?", "…"))
    ):
        end = len(tokens)
    words: list[str] = []
    for token in tokens[start:end]:
        words.extend(_comparison_words(subtitle_identity(token)))
    return words


def _numbers_conflict(
    first_words: list[str], second_words: list[str], *, strong_core: bool
) -> bool:
    """Aligned number changes are semantic; one-sided unstable numbers are not.

    A number present at the same aligned position in both variants but with a
    different value (``101``/``102``) is always a conflict. A number that has
    no counterpart at all is OCR instability only when a strong shared core
    proves that both observations are the same line.
    """

    if [word for word in first_words if word.isdigit()] == [
        word for word in second_words if word.isdigit()
    ]:
        return False
    matcher = SequenceMatcher(None, first_words, second_words, autojunk=False)
    for tag, first_start, first_end, second_start, second_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        left = [word for word in first_words[first_start:first_end] if word.isdigit()]
        right = [
            word for word in second_words[second_start:second_end] if word.isdigit()
        ]
        if not left and not right:
            continue
        if left and right:
            return True
        if not strong_core:
            return True
    return False


def _lcs_length(first: list[str], second: list[str]) -> int:
    rows = len(first)
    columns = len(second)
    table = [[0] * (columns + 1) for _ in range(rows + 1)]
    for row in range(rows - 1, -1, -1):
        for column in range(columns - 1, -1, -1):
            if first[row] == second[column]:
                table[row][column] = 1 + table[row + 1][column + 1]
            else:
                table[row][column] = max(
                    table[row + 1][column], table[row][column + 1]
                )
    return table[0][0]


def _has_polarity_prefix_conflict(first: list[str], second: list[str]) -> bool:
    for left in first:
        for right in second:
            if left == right:
                continue
            short, long = sorted((left, right), key=len)
            if len(short) < 3:
                continue
            if any(long == prefix + short for prefix in ("un", "non", "dis")):
                return True
    return False


@dataclass(frozen=True, slots=True)
class _PhraseMetrics:
    char_similarity: float
    token_similarity: float
    common_core_words: int
    common_core_ratio: float
    numbers_match: bool
    semantic_conflict: bool


def _phrase_metrics(first_text: str, second_text: str) -> _PhraseMetrics:
    first = subtitle_identity(first_text)
    second = subtitle_identity(second_text)
    first_compact = first.replace(" ", "")
    second_compact = second.replace(" ", "")
    char_similarity = (
        SequenceMatcher(None, first_compact, second_compact, autojunk=False).ratio()
        if first_compact and second_compact
        else 0.0
    )
    first_words = _comparison_words(first)
    second_words = _comparison_words(second)
    token_similarity = (
        SequenceMatcher(None, first_words, second_words, autojunk=False).ratio()
        if first_words and second_words
        else 0.0
    )
    common = _lcs_length(first_words, second_words)
    shorter = min(len(first_words), len(second_words))
    strong_core = common >= 3 and bool(shorter) and common / shorter >= 0.75
    numbers_match = not _numbers_conflict(
        _semantic_number_words(first_text),
        _semantic_number_words(second_text),
        strong_core=strong_core,
    )
    semantic_conflict = (
        not numbers_match
        or (set(first_words) & _NEGATION_WORDS)
        != (set(second_words) & _NEGATION_WORDS)
        or _has_polarity_prefix_conflict(first_words, second_words)
    )
    return _PhraseMetrics(
        char_similarity=char_similarity,
        token_similarity=token_similarity,
        common_core_words=common,
        common_core_ratio=common / shorter if shorter else 0.0,
        numbers_match=numbers_match,
        semantic_conflict=semantic_conflict,
    )


def _looks_like_edge_scrap(word: str) -> bool:
    letters = [character for character in word if character.isalpha()]
    longest_consonant_run = max(
        (len(value) for value in re.split(r"[aąeęioóuy]+", word)),
        default=0,
    )
    return bool(
        len(word) <= 2
        or not letters
        or not any(character in "aąeęioóuy" for character in letters)
        or longest_consonant_run >= 4
    )


def _only_ocr_like_changes(first_text: str, second_text: str) -> bool:
    """Reject real word substitutions while permitting bounded OCR damage."""

    first = _comparison_words(subtitle_identity(first_text))
    second = _comparison_words(subtitle_identity(second_text))
    common = _lcs_length(first, second)
    shorter = min(len(first), len(second))
    common_ratio = common / shorter if shorter else 0.0
    strong_core = common >= 6 and common_ratio >= 0.85
    weak_budget = max(1, common // 8) if strong_core else 0
    matcher = SequenceMatcher(None, first, second, autojunk=False)
    for tag, first_start, first_end, second_start, second_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        left = first[first_start:first_end]
        right = second[second_start:second_end]
        at_edge = (
            first_start == second_start == 0
            or first_end == len(first) and second_end == len(second)
        )
        if tag in {"insert", "delete"}:
            changed = left or right
            if any(word in _NEGATION_WORDS for word in changed):
                return False
            if at_edge:
                if len(changed) == 1:
                    continue
                if len(changed) <= 3 and all(
                    word.isdigit() or _looks_like_edge_scrap(word)
                    for word in changed
                ):
                    continue
                return False
            # A line break can leave one glyph or digit inside a long line.
            if (
                strong_core
                and len(changed) == 1
                and (changed[0].isdigit() or len(changed[0]) == 1)
            ):
                continue
            return False
        left_joined = "".join(left)
        right_joined = "".join(right)
        if not left_joined or not right_joined:
            return False
        if (
            at_edge
            and common >= 3
            and all(
                word.isdigit() or _looks_like_edge_scrap(word)
                for word in (*left, *right)
            )
        ):
            continue
        ratio = SequenceMatcher(
            None, left_joined, right_joined, autojunk=False
        ).ratio()
        if ratio >= 0.72:
            continue
        # Glyph confusion (b/d, i/l, c/g) inside an otherwise identical long
        # line. Bounded per phrase so unrelated vocabulary never collapses.
        if (
            weak_budget
            and len(left) == len(right) == 1
            and (ratio >= 0.60 or max(len(left[0]), len(right[0])) <= 2)
        ):
            weak_budget -= 1
            continue
        return False
    return True


def _meaningful_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in str(text).split("\n"):
        cleaned = _drop_proven_noise_tokens(_trim_symbolic_edges(line))
        words = _comparison_words(subtitle_identity(cleaned))
        if not words:
            continue
        if all(word.isdigit() or _looks_like_edge_scrap(word) for word in words):
            continue
        lines.append(cleaned)
    return lines


def _line_extension_variant(
    first_lines: list[str] | tuple[str, ...],
    second_lines: list[str] | tuple[str, ...],
) -> bool:
    """One multi-line subtitle whose last line appeared, vanished or was cut.

    Matching whole leading lines must carry most of the shorter text. A new
    dialogue that shares only one line with the old one stays distinct.
    """

    shorter, longer = sorted((list(first_lines), list(second_lines)), key=len)
    if len(longer) < 2 or not shorter:
        return False
    matched = 0
    for left, right in zip(shorter, longer):
        if not _same_episode_variant(left, right):
            break
        matched += 1
    if matched == 0:
        return False
    shorter_words = sum(
        len(_comparison_words(subtitle_identity(line))) for line in shorter
    )
    matched_words = sum(
        len(_comparison_words(subtitle_identity(line))) for line in shorter[:matched]
    )
    if not shorter_words or matched_words / shorter_words < 0.70:
        return False
    if matched == len(shorter):
        return True
    # Both show the same leading lines; only the final partial line differs.
    return (
        len(shorter) >= 3
        and matched == len(shorter) - 1
        and len(longer) == len(shorter)
    )


def _same_episode_variant(first_text: str, second_text: str) -> bool:
    """Use several guarded signals; no character threshold is authoritative."""

    first = subtitle_identity(first_text)
    second = subtitle_identity(second_text)
    if not first or not second:
        return False
    if first == second or _dedup_is_split_merge_variant(first, second):
        return True
    metrics = _phrase_metrics(first_text, second_text)
    if metrics.semantic_conflict:
        return False
    if (
        _dedup_is_garbage_variant(first, second)
        or _is_ocr_slip_variant(first, second)
    ):
        return True
    first_words = _comparison_words(first)
    second_words = _comparison_words(second)
    if min(len(first_words), len(second_words)) < 3:
        return False
    if not _only_ocr_like_changes(first, second):
        return False
    return bool(
        (
            metrics.char_similarity >= 0.90
            and metrics.token_similarity >= 0.70
            and metrics.common_core_ratio >= 0.65
        )
        or (
            metrics.char_similarity >= 0.84
            and metrics.token_similarity >= 0.65
            and metrics.common_core_words >= 3
            and metrics.common_core_ratio >= 0.75
        )
        or (
            metrics.char_similarity >= 0.86
            and metrics.token_similarity >= 0.50
            and metrics.common_core_words >= 3
            and metrics.common_core_ratio >= 0.75
        )
    )


_ARTIFACT_CHARACTERS = frozenset("%$#@©®^~`\\{}[]<>|_=+*")


def _token_identity(token: str) -> str:
    return " ".join(_comparison_words(subtitle_identity(token)))


def _split_token(token: str) -> tuple[str, str, str]:
    """Split a surface token into leading punctuation, word and trailing punctuation."""

    start = 0
    while start < len(token) and not token[start].isalnum():
        start += 1
    stop = len(token)
    while stop > start and not token[stop - 1].isalnum():
        stop -= 1
    return token[:start], token[start:stop], token[stop:]


def _surface_tokens(text: str) -> list[tuple[str, str, str]]:
    """Word tokens with punctuation; standalone punctuation joins the previous word."""

    tokens: list[tuple[str, str, str]] = []
    for token in normalize_subtitle(text).split():
        lead, core, trail = _split_token(token)
        if core:
            tokens.append((lead, core, trail))
        elif tokens:
            previous_lead, previous_core, previous_trail = tokens[-1]
            tokens[-1] = (previous_lead, previous_core, f"{previous_trail} {token}")
    return tokens


def _fuzzy_word(first: str, second: str) -> bool:
    """Same word read with an OCR slip or with glyphs cut at a line edge."""

    if first == second:
        return True
    short, long = sorted((first, second), key=len)
    if (
        len(short) >= 3
        and len(long) - len(short) <= 4
        and (long.startswith(short) or long.endswith(short))
    ):
        return True
    return bool(
        len(short) >= 4
        and SequenceMatcher(None, first, second, autojunk=False).ratio() >= 0.72
    )


def _diacritic_count(word: str) -> int:
    return sum(character.isalpha() and not character.isascii() for character in word)


def _structural_token(core: str, surface: str) -> bool:
    """Symbols, lone digits and lone characters carry no dialogue on an edge."""

    identity = _token_identity(core)
    return bool(
        not identity
        or identity.isdigit()
        or len(identity) == 1
        or any(character in _ARTIFACT_CHARACTERS for character in surface)
    )


def _line_edge_indices(lines: tuple[str, ...], word_count: int) -> set[int]:
    counts = [
        len(_surface_tokens(line)) for line in lines if _surface_tokens(line)
    ]
    if not counts or sum(counts) != word_count:
        return {0, max(0, word_count - 1)}
    edges: set[int] = set()
    position = 0
    for count in counts:
        edges.update({position, position + count - 1})
        position += count
    return edges


def _cluster_representative(
    variants: list[tuple[str, float, tuple[str, ...]]],
) -> tuple[str, tuple[str, ...]]:
    """Token-voted consensus text built only from whole observed tokens.

    The medoid is only the alignment pivot. Every other variant votes on each
    aligned pivot word; a word cut at a line edge is completed from a variant
    that saw it whole, accents lost by OCR are restored, and punctuation is
    taken from its own position (a sentence stop before a lowercase word needs
    a strict majority). Words missing from the pivot's outer edges are taken
    from the best variant that saw them. Only symbols, lone digits and lone
    characters that no other clean variant repeats are trimmed from the edges.
    """

    if not variants:
        return "", ()
    if len(variants) == 1:
        return variants[0][0], variants[0][2]
    texts = [text for text, _quality, _lines in variants]
    tokenized = [_surface_tokens(text) for text in texts]
    keys = [[_token_identity(core) for _lead, core, _trail in tokens] for tokens in tokenized]
    pivot_index = 0
    best_score = float("-inf")
    for index, (text, quality, _lines) in enumerate(variants):
        if not tokenized[index]:
            continue
        agreement = sum(
            _phrase_metrics(text, other).char_similarity
            for other_index, other in enumerate(texts)
            if other_index != index
        ) / (len(texts) - 1)
        score = agreement * 0.6 + max(0.0, quality) * 0.4
        if score > best_score:
            pivot_index, best_score = index, score
    pivot = tokenized[pivot_index]
    pivot_keys = keys[pivot_index]
    if not pivot:
        return variants[pivot_index][0], variants[pivot_index][2]
    slots: list[list[tuple[str, str, float]]] = [
        [(core, trail, variants[pivot_index][1])] for _lead, core, trail in pivot
    ]
    leading: list[tuple[float, list[tuple[str, str, str]]]] = []
    trailing: list[tuple[float, list[tuple[str, str, str]]]] = []
    for index, tokens in enumerate(tokenized):
        if index == pivot_index or not tokens:
            continue
        quality = variants[index][1]
        matcher = SequenceMatcher(None, pivot_keys, keys[index], autojunk=False)
        for tag, first_start, first_end, second_start, second_end in matcher.get_opcodes():
            if tag == "equal":
                for offset in range(first_end - first_start):
                    _lead, core, trail = tokens[second_start + offset]
                    slots[first_start + offset].append((core, trail, quality))
                continue
            paired = min(first_end - first_start, second_end - second_start)
            for offset in range(paired):
                if _fuzzy_word(
                    pivot_keys[first_start + offset], keys[index][second_start + offset]
                ):
                    _lead, core, trail = tokens[second_start + offset]
                    slots[first_start + offset].append((core, trail, quality))
            extra = tokens[second_start + paired : second_end]
            if not extra or not all(
                not _structural_token(core, lead + core + trail)
                and not _looks_like_edge_scrap(_token_identity(core))
                for lead, core, trail in extra
            ):
                continue
            if first_start == 0 and first_end - first_start == 0:
                leading.append((quality, list(extra)))
            elif first_end == len(pivot):
                trailing.append((quality, list(extra)))
    edges = _line_edge_indices(variants[pivot_index][2], len(pivot))

    def choose_word(slot_index: int) -> str:
        votes = slots[slot_index]
        by_key: dict[str, list[str]] = {}
        for core, _trail, _quality in votes:
            by_key.setdefault(_token_identity(core), []).append(core)
        pivot_key = pivot_keys[slot_index]
        chosen_key = max(
            by_key,
            key=lambda key: (len(by_key[key]), key == pivot_key),
        )
        if slot_index in edges:
            completions = [
                key
                for key in by_key
                if len(key) > len(chosen_key)
                and (key.startswith(chosen_key) or key.endswith(chosen_key))
            ]
            if completions:
                chosen_key = max(completions, key=len)
        forms = by_key[chosen_key]
        pivot_core = pivot[slot_index][1]
        return max(
            forms,
            key=lambda form: (
                _diacritic_count(form),
                forms.count(form),
                form == pivot_core,
            ),
        )

    words = [choose_word(index) for index in range(len(pivot))]

    def supplier_trail(slot_index: int, word: str) -> str:
        for core, trail, _quality in slots[slot_index]:
            if core == word:
                return trail
        return pivot[slot_index][2]

    def choose_trail(slot_index: int, next_word: str) -> str:
        """Punctuation from the pivot's own position, never invented."""

        votes = [trail for _core, trail, _quality in slots[slot_index]]
        pivot_trail = pivot[slot_index][2]
        if not next_word:
            word = words[slot_index]
            if _token_identity(word) != pivot_keys[slot_index]:
                # The final word was completed from a variant that saw it
                # whole; take that variant's punctuation with it.
                return supplier_trail(slot_index, word)
            return pivot_trail
        terminal = any(
            character in ".!?" for character in pivot_trail.replace("...", "")
        )
        if (
            pivot_trail
            and terminal
            and next_word[:1].islower()
            and votes.count(pivot_trail) * 2 <= len(votes)
        ):
            return ""
        return pivot_trail

    extension_leading = max(leading, key=lambda item: item[0])[1] if leading else []
    extension_trailing = max(trailing, key=lambda item: item[0])[1] if trailing else []
    def confirmed(slot_index: int) -> bool:
        """Another variant saw the same token at the aligned position."""

        key = _token_identity(words[slot_index])
        return (
            sum(
                _token_identity(core) == key
                and not any(
                    character in _ARTIFACT_CHARACTERS for character in core + trail
                )
                for core, trail, _quality in slots[slot_index]
            )
            >= 2
        )

    # Entries carry (lead, core, trail, confirmed-by-another-variant).
    result: list[tuple[str, str, str, bool]] = [
        (lead, core, trail, True) for lead, core, trail in extension_leading
    ]
    for index, word in enumerate(words):
        following = (
            words[index + 1]
            if index + 1 < len(words)
            else (extension_trailing[0][1] if extension_trailing else "")
        )
        result.append(
            (pivot[index][0], word, choose_trail(index, following), confirmed(index))
        )
    result.extend(
        (lead, core, trail, True) for lead, core, trail in extension_trailing
    )

    def removable_edge(entry: tuple[str, str, str, bool]) -> bool:
        lead, core, trail, is_confirmed = entry
        return not is_confirmed and _structural_token(core, lead + core + trail)

    while len(result) > 1 and removable_edge(result[0]):
        result.pop(0)
    while len(result) > 1 and removable_edge(result[-1]):
        dropped_trail = result.pop()[2].strip()
        last_lead, last_core, last_trail, last_confirmed = result[-1]
        terminal = "".join(
            character for character in dropped_trail if character in ".!?…"
        )
        if terminal and not last_trail:
            result[-1] = (last_lead, last_core, terminal, last_confirmed)
    text = " ".join(
        lead + core + trail for lead, core, trail, _confirmed in result
    )
    return _trim_symbolic_edges(text), variants[pivot_index][2]


def _line_word_positions(
    text: str, lines: tuple[str, ...] | list[str]
) -> tuple[list[str], set[int], int]:
    """Comparison words with the indices of every line's first and last word."""

    words: list[str] = []
    edges: set[int] = set()
    line_count = 0
    for line in lines:
        line_words = _comparison_words(subtitle_identity(line))
        if not line_words:
            continue
        edges.update({len(words), len(words) + len(line_words) - 1})
        words.extend(line_words)
        line_count += 1
    if not words:
        words = _comparison_words(subtitle_identity(text))
        edges = {0, max(0, len(words) - 1)}
        line_count = 1
    return words, edges, line_count


def _long_episode_variant(
    first_text: str,
    first_lines: tuple[str, ...] | list[str],
    second_text: str,
    second_lines: tuple[str, ...] | list[str],
) -> bool:
    """One long multi-line subtitle re-read with line-edge damage.

    Differences may only drop, add or mangle words at the start or end of a
    line (the ROI clips glyphs there) or be single OCR-misread words. The
    shared word core must cover most of the shorter text, and number,
    negation and polarity protection is unchanged.
    """

    first, first_edges, first_line_count = _line_word_positions(first_text, first_lines)
    second, second_edges, second_line_count = _line_word_positions(
        second_text, second_lines
    )
    shorter = min(len(first), len(second))
    if shorter < 6 or max(first_line_count, second_line_count) < 2:
        return False
    if _phrase_metrics(first_text, second_text).semantic_conflict:
        return False
    matched = 0
    misreads = 0
    regions = 0
    matcher = SequenceMatcher(None, first, second, autojunk=False)
    for tag, first_start, first_end, second_start, second_end in matcher.get_opcodes():
        if tag == "equal":
            matched += first_end - first_start
            continue
        regions += 1
        at_line_edge = bool(
            {first_start, first_end - 1} & first_edges
            or {second_start, second_end - 1} & second_edges
            or first_start == first_end and first_start in {
                edge + 1 for edge in first_edges
            }
            or second_start == second_end and second_start in {
                edge + 1 for edge in second_edges
            }
        )
        left_region = first[first_start:first_end]
        right_region = second[second_start:second_end]
        paired = min(len(left_region), len(right_region))

        def pairs(from_end: bool) -> list[tuple[str, str]]:
            if from_end:
                return list(zip(left_region[len(left_region) - paired :],
                                right_region[len(right_region) - paired :]))
            return list(zip(left_region[:paired], right_region[:paired]))

        candidates = (pairs(False), pairs(True))
        chosen = max(
            candidates,
            key=lambda values: sum(_fuzzy_word(a, b) for a, b in values),
        )
        from_end = chosen is candidates[1] and candidates[1] != candidates[0]
        for left, right in chosen:
            if _fuzzy_word(left, right):
                matched += 1
                misreads += 1
            elif at_line_edge and (
                _looks_like_edge_scrap(left) or _looks_like_edge_scrap(right)
            ):
                continue
            else:
                return False
        if from_end:
            extra = [
                *left_region[: len(left_region) - paired],
                *right_region[: len(right_region) - paired],
            ]
        else:
            extra = [*left_region[paired:], *right_region[paired:]]
        if not extra:
            continue
        if any(word in _NEGATION_WORDS for word in extra):
            return False
        if not at_line_edge:
            return False
        # The ROI can clip up to a few words at the start or end of a line.
        if len(extra) > 3:
            return False
    if regions > 2 * max(first_line_count, second_line_count):
        return False
    if misreads > max(2, shorter // 5):
        return False
    return matched / shorter >= 0.80


def _token_matches_marker(token: str, marker: str) -> bool:
    return bool(subtitle_identity(token)) and subtitle_identity(token) == subtitle_identity(marker)


def _strip_low_quality_edges(
    text: str,
    *,
    leading: tuple[str, ...],
    trailing: tuple[str, ...],
    reference_text: str,
) -> str:
    """Remove weak edge evidence only as a cluster or outside a known core."""

    tokens = _trim_symbolic_edges(text).split()
    if not tokens:
        return ""

    reference_words = set(_comparison_words(subtitle_identity(reference_text)))

    def protected(marker: str) -> bool:
        identity = subtitle_identity(marker)
        return bool(
            identity in _NEGATION_WORDS
            or identity.isdigit()
            or identity in reference_words
        )

    def removable(
        markers: tuple[str, ...], original: str, candidate: str
    ) -> bool:
        if any(protected(marker) for marker in markers):
            return False
        if len(markers) >= 2:
            return True
        if markers and len(subtitle_identity(markers[0])) > 1:
            return True
        if not markers or not reference_text:
            return False
        before = _phrase_metrics(original, reference_text)
        after = _phrase_metrics(candidate, reference_text)
        return (
            not after.semantic_conflict
            and after.common_core_words >= 1
            and after.char_similarity >= before.char_similarity + 0.05
            and after.token_similarity >= before.token_similarity
        )

    matched_leading = 0
    for marker, token in zip(leading, tokens):
        if not _token_matches_marker(token, marker):
            break
        matched_leading += 1
    if matched_leading and removable(
        leading[:matched_leading],
        " ".join(tokens),
        " ".join(tokens[matched_leading:]),
    ):
        tokens = tokens[matched_leading:]

    matched_trailing = 0
    for marker, token in zip(reversed(trailing), reversed(tokens)):
        if not _token_matches_marker(token, marker):
            break
        matched_trailing += 1
    if matched_trailing and removable(
        trailing[len(trailing) - matched_trailing :],
        " ".join(tokens),
        " ".join(tokens[: len(tokens) - matched_trailing]),
    ):
        tokens = tokens[: len(tokens) - matched_trailing]
    return _trim_symbolic_edges(" ".join(tokens))


def _clean_observed_text(
    text: str,
    *,
    leading_low_quality_tokens: tuple[str, ...] = (),
    trailing_low_quality_tokens: tuple[str, ...] = (),
    reference_text: str = "",
) -> str:
    cleaned = _drop_proven_noise_tokens(_trim_symbolic_edges(text))
    return _strip_low_quality_edges(
        cleaned,
        leading=tuple(leading_low_quality_tokens),
        trailing=tuple(trailing_low_quality_tokens),
        reference_text=reference_text,
    )


def _within_edits(first: str, second: str, limit: int) -> bool:
    """Whether at most ``limit`` added, removed or changed characters separate them."""

    if abs(len(first) - len(second)) > limit:
        return False
    previous = list(range(len(second) + 1))
    for row, left in enumerate(first, start=1):
        current = [row]
        for column, right in enumerate(second, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (left != right),
                )
            )
        if min(current) > limit:
            return False
        previous = current
    return previous[-1] <= limit


def _is_ocr_slip_variant(first: str, second: str) -> bool:
    """One long enough line re-read with a single slip (``tu adam``/``tuadamx``)."""

    first_key = first.replace(" ", "")
    second_key = second.replace(" ", "")
    return min(
        len(first_key), len(second_key)
    ) >= OCR_DUPLICATE_MIN_KEY_LENGTH and _within_edits(
        first_key, second_key, OCR_DUPLICATE_MAX_EDITS
    )


@dataclass(frozen=True, slots=True)
class OcrGateObservation:
    raw_text: str
    filtered_text: str
    confidence: float | None
    rejection_reason: str
    accepted_text: str = ""
    needs_confirmation: bool = False
    candidate_started: bool = False
    credible: bool = False
    candidate_text: str = ""
    candidate_observation_count: int = 0
    required_observations: int = OCR_STABLE_OBSERVATIONS
    candidate_similarity: float | None = None
    candidate_match_kind: str = ""
    candidate_replaced: bool = False
    decision: str = ""
    candidate_id: int = 0
    replaced_candidate_id: int = 0
    replaced_candidate_text: str = ""
    quality_score: float | None = None
    selected_line_confidence: float | None = None
    canonical_text: str = ""
    char_similarity: float | None = None
    token_similarity: float | None = None
    reason_code: str = ""


@dataclass(frozen=True, slots=True)
class _AcceptedSubtitleTiming:
    first_visible_frame_timestamp: float
    frame_timestamp: float
    accepted_at: float
    frame_acquisition_ms: float
    roi_preparation_ms: float
    ocr_preprocessing_ms: float
    ocr_ms: float
    stabilization_dedup_ms: float


@dataclass(frozen=True, slots=True)
class _TtsStageResult:
    audio: PcmAudio
    started_at: float
    finished_at: float


class SubtitleTextGate:
    """Reject implausible OCR and require bounded, quality-ranked consensus."""

    def __init__(
        self,
        *,
        min_confidence: float = 0.62,
        required_observations: int = OCR_STABLE_OBSERVATIONS,
        similarity_threshold: float = OCR_SIMILARITY_THRESHOLD,
        stability_window_seconds: float = OCR_STABILITY_WINDOW_SECONDS,
    ) -> None:
        self.min_confidence = min(1.0, max(0.0, float(min_confidence)))
        self.required_observations = max(2, int(required_observations))
        self.similarity_threshold = min(1.0, max(0.0, similarity_threshold))
        self.stability_window_seconds = max(0.1, stability_window_seconds)
        self._candidate_text = ""
        self._candidate_identity = ""
        self._candidate_confidence = -1.0
        self._candidate_quality = -1.0
        self._candidate_variants: list[tuple[str, float, tuple[str, ...]]] = []
        self._candidate_count = 0
        self._candidate_since = 0.0
        # One displaced cluster, so a single interleaved misread cannot erase
        # the votes of the subtitle that is still on screen.
        self._previous_cluster: dict[str, object] | None = None
        self._accepted_text = ""
        self._accepted_identity = ""
        self._accepted_lines: tuple[str, ...] = ()
        self._absence_streak = 0
        self._needs_confirmation = False
        self._candidate_id = 0
        self.last_line_similarity: float | None = None

    @property
    def needs_confirmation(self) -> bool:
        return self._needs_confirmation

    @property
    def canonical_text(self) -> str:
        return self._accepted_text

    def observe(
        self,
        text: str,
        confidence: float | None,
        *,
        now: float,
        raw_text: str | None = None,
        strong_short_phrase_evidence: bool = False,
        quality_score: float | None = None,
        selected_line_confidence: float | None = None,
        leading_low_quality_tokens: tuple[str, ...] = (),
        trailing_low_quality_tokens: tuple[str, ...] = (),
        frame_had_text: bool | None = None,
    ) -> OcrGateObservation:
        raw = str(text if raw_text is None else raw_text)
        previous_candidate = self._candidate_text
        previous_identity = self._candidate_identity
        previous_since = self._candidate_since
        reference = (
            self._accepted_text if self._accepted_identity else previous_candidate
        )
        filtered = _clean_observed_text(
            text,
            leading_low_quality_tokens=leading_low_quality_tokens,
            trailing_low_quality_tokens=trailing_low_quality_tokens,
            reference_text=reference,
        )
        observed_lines = tuple(_meaningful_lines(text))
        if len(observed_lines) >= 2 and (
            leading_low_quality_tokens or trailing_low_quality_tokens
        ):
            observed_lines = tuple(
                _clean_observed_text(
                    line,
                    leading_low_quality_tokens=(
                        leading_low_quality_tokens if index == 0 else ()
                    ),
                    trailing_low_quality_tokens=(
                        trailing_low_quality_tokens
                        if index == len(observed_lines) - 1
                        else ()
                    ),
                    reference_text=reference,
                )
                for index, line in enumerate(observed_lines)
            )
        had_text = bool(raw.strip()) if frame_had_text is None else bool(frame_had_text)
        if had_text:
            self._absence_streak = 0
        else:
            self._absence_streak += 1
        confirmed_disappearance = (
            self._absence_streak >= SUBTITLE_DISAPPEARANCE_OBSERVATIONS
        )
        self.last_line_similarity = None
        reason = self._validate(filtered, confidence)
        if reason == "empty" and had_text:
            reason = "noise"

        if reason:
            if (
                previous_candidate
                and not confirmed_disappearance
                and now - previous_since <= self.stability_window_seconds
            ):
                self._needs_confirmation = True
                return OcrGateObservation(
                    raw,
                    filtered,
                    confidence,
                    reason,
                    needs_confirmation=True,
                    credible=False,
                    candidate_text=previous_candidate,
                    candidate_observation_count=self._candidate_count,
                    required_observations=self.required_observations,
                    candidate_id=self._candidate_id,
                    quality_score=quality_score,
                    selected_line_confidence=selected_line_confidence,
                    canonical_text=self._accepted_text,
                    decision=f"candidate_retained_after_{reason}",
                    reason_code=f"transient_{reason}",
                )
            abandoned_id = self._candidate_id if previous_candidate else 0
            if confirmed_disappearance:
                self._clear(no_subtitle=True)
            elif previous_candidate:
                self._reset_candidate()
            pending_episode_disappearance = bool(
                self._accepted_identity
                and not had_text
                and not confirmed_disappearance
            )
            if pending_episode_disappearance:
                self._needs_confirmation = True
            elif not previous_candidate:
                self._needs_confirmation = False
            return OcrGateObservation(
                raw,
                filtered,
                confidence,
                reason,
                needs_confirmation=pending_episode_disappearance,
                required_observations=self.required_observations,
                candidate_id=abandoned_id,
                replaced_candidate_id=abandoned_id,
                replaced_candidate_text=previous_candidate,
                quality_score=quality_score,
                selected_line_confidence=selected_line_confidence,
                canonical_text=self._accepted_text,
                decision=(
                    f"candidate_reset_{reason}"
                    if previous_candidate
                    else f"rejected_{reason}"
                ),
                reason_code=(
                    "confirmed_disappearance"
                    if confirmed_disappearance
                    else "candidate_timeout" if previous_candidate else reason
                ),
            )

        identity = subtitle_identity(filtered)
        accepted_metrics = _phrase_metrics(filtered, self._accepted_text)
        if self._accepted_identity:
            self.last_line_similarity = accepted_metrics.char_similarity
        if self._accepted_identity and (
            _same_episode_variant(filtered, self._accepted_text)
            or _line_extension_variant(observed_lines, self._accepted_lines)
            or _long_episode_variant(
                filtered, observed_lines, self._accepted_text, self._accepted_lines
            )
        ):
            self._reset_candidate()
            return OcrGateObservation(
                raw,
                filtered,
                confidence,
                "duplicate",
                credible=True,
                required_observations=self.required_observations,
                candidate_similarity=accepted_metrics.char_similarity,
                candidate_match_kind=(
                    "normalized_exact"
                    if identity == self._accepted_identity
                    else "active_episode_variant"
                ),
                quality_score=quality_score,
                selected_line_confidence=selected_line_confidence,
                canonical_text=self._accepted_text,
                char_similarity=accepted_metrics.char_similarity,
                token_similarity=accepted_metrics.token_similarity,
                decision="duplicate_accepted_phrase",
                reason_code="active_episode_variant",
            )

        if strong_short_phrase_evidence and not self._candidate_identity:
            self._candidate_id += 1
            accepted_candidate_id = self._candidate_id
            self._accepted_text = filtered
            self._accepted_identity = identity
            self._accepted_lines = observed_lines
            self._reset_candidate()
            return OcrGateObservation(
                raw,
                filtered,
                confidence,
                "",
                filtered,
                credible=True,
                candidate_text=filtered,
                candidate_observation_count=1,
                required_observations=1,
                candidate_match_kind="strong_short_evidence",
                candidate_id=accepted_candidate_id,
                quality_score=quality_score,
                selected_line_confidence=selected_line_confidence,
                canonical_text=filtered,
                char_similarity=1.0,
                token_similarity=1.0,
                decision="accepted_strong_short_evidence",
                reason_code="strong_short_evidence",
            )

        within_window = bool(
            self._candidate_identity
            and now - self._candidate_since <= self.stability_window_seconds
        )
        candidate_metrics = _phrase_metrics(filtered, self._candidate_text)
        match_kind = (
            self._cluster_match_kind(filtered, observed_lines)
            if within_window
            else ""
        )
        if (
            not match_kind
            and self._previous_cluster is not None
            and now - float(self._previous_cluster["since"])
            <= self.stability_window_seconds
        ):
            self._swap_previous_cluster()
            match_kind = self._cluster_match_kind(filtered, observed_lines)
            if match_kind:
                match_kind = f"restored_{match_kind}"
                candidate_metrics = _phrase_metrics(filtered, self._candidate_text)
            else:
                self._swap_previous_cluster()
        similar = bool(match_kind)
        if not similar:
            if previous_identity and now - previous_since <= self.stability_window_seconds:
                self._previous_cluster = self._cluster_state()
            else:
                self._previous_cluster = None
            replaced_id = self._candidate_id if previous_identity else 0
            replaced_text = self._candidate_text if previous_identity else ""
            self._candidate_id += 1
            self._candidate_text = filtered
            self._candidate_identity = identity
            self._candidate_confidence = confidence if confidence is not None else -1.0
            self._candidate_quality = self._variant_quality(
                confidence, quality_score, selected_line_confidence
            )
            self._candidate_variants = [
                (self._candidate_text, self._candidate_quality, observed_lines)
            ]
            self._candidate_count = 1
            self._candidate_since = now
            self._needs_confirmation = True
            return OcrGateObservation(
                raw,
                filtered,
                confidence,
                "unstable",
                needs_confirmation=True,
                candidate_started=True,
                credible=True,
                candidate_text=filtered,
                candidate_observation_count=1,
                required_observations=self.required_observations,
                candidate_similarity=(
                    candidate_metrics.char_similarity if previous_identity else None
                ),
                candidate_replaced=bool(previous_identity),
                candidate_id=self._candidate_id,
                replaced_candidate_id=replaced_id,
                replaced_candidate_text=replaced_text,
                quality_score=quality_score,
                selected_line_confidence=selected_line_confidence,
                canonical_text=self._accepted_text,
                char_similarity=(
                    candidate_metrics.char_similarity if previous_identity else None
                ),
                token_similarity=(
                    candidate_metrics.token_similarity if previous_identity else None
                ),
                decision=(
                    "candidate_window_expired"
                    if previous_identity
                    and now - previous_since > self.stability_window_seconds
                    else (
                        "candidate_replaced_dissimilar"
                        if previous_identity
                        else "candidate_started"
                    )
                ),
                reason_code=(
                    "candidate_timeout"
                    if previous_identity
                    and now - previous_since > self.stability_window_seconds
                    else "dissimilar_candidate" if previous_identity else "new_candidate"
                ),
            )

        self._candidate_count += 1
        candidate_quality = self._variant_quality(
            confidence, quality_score, selected_line_confidence
        )
        self._candidate_variants.append(
            (filtered, candidate_quality, observed_lines)
        )
        self._candidate_variants = self._candidate_variants[-12:]
        if candidate_quality > self._candidate_quality:
            self._candidate_text = filtered
            self._candidate_identity = identity
            self._candidate_confidence = confidence if confidence is not None else -1.0
            self._candidate_quality = candidate_quality
        if self._candidate_count < self.required_observations:
            self._needs_confirmation = True
            return OcrGateObservation(
                raw,
                filtered,
                confidence,
                "unstable",
                needs_confirmation=True,
                credible=True,
                candidate_text=self._candidate_text,
                candidate_observation_count=self._candidate_count,
                required_observations=self.required_observations,
                candidate_similarity=candidate_metrics.char_similarity,
                candidate_match_kind=match_kind,
                candidate_id=self._candidate_id,
                quality_score=quality_score,
                selected_line_confidence=selected_line_confidence,
                canonical_text=self._accepted_text,
                char_similarity=candidate_metrics.char_similarity,
                token_similarity=candidate_metrics.token_similarity,
                decision="candidate_confirming",
                reason_code="strong_vote",
            )

        accepted, accepted_lines = _cluster_representative(
            self._candidate_variants
        )
        accepted = accepted or self._candidate_text
        accepted_count = self._candidate_count
        accepted_candidate_id = self._candidate_id
        self._accepted_text = accepted
        self._accepted_identity = subtitle_identity(accepted)
        self._accepted_lines = accepted_lines
        self._reset_candidate()
        self._previous_cluster = None
        return OcrGateObservation(
            raw,
            filtered,
            confidence,
            "",
            accepted,
            credible=True,
            candidate_text=accepted,
            candidate_observation_count=accepted_count,
            required_observations=self.required_observations,
            candidate_similarity=candidate_metrics.char_similarity,
            candidate_match_kind=match_kind,
            candidate_id=accepted_candidate_id,
            quality_score=quality_score,
            selected_line_confidence=selected_line_confidence,
            canonical_text=accepted,
            char_similarity=candidate_metrics.char_similarity,
            token_similarity=candidate_metrics.token_similarity,
            decision="accepted_consensus",
            reason_code="consensus_reached",
        )

    def _cluster_match_kind(
        self, filtered: str, observed_lines: tuple[str, ...]
    ) -> str:
        identity = subtitle_identity(filtered)
        variants = self._candidate_variants or [
            (self._candidate_text, self._candidate_quality, ())
        ]
        for text, _quality, lines in reversed(variants):
            if not text:
                continue
            kind = self._identity_match_kind(
                identity,
                subtitle_identity(text),
                metrics=_phrase_metrics(filtered, text),
                first_text=filtered,
                second_text=text,
            )
            if kind:
                return kind
            if _line_extension_variant(observed_lines, lines):
                return "line_extension"
        return ""

    def _cluster_state(self) -> dict[str, object]:
        return {
            "text": self._candidate_text,
            "identity": self._candidate_identity,
            "confidence": self._candidate_confidence,
            "quality": self._candidate_quality,
            "variants": list(self._candidate_variants),
            "count": self._candidate_count,
            "since": self._candidate_since,
            "id": self._candidate_id,
        }

    def _swap_previous_cluster(self) -> None:
        previous = self._previous_cluster
        if previous is None:
            return
        self._previous_cluster = self._cluster_state()
        self._candidate_text = str(previous["text"])
        self._candidate_identity = str(previous["identity"])
        self._candidate_confidence = float(previous["confidence"])
        self._candidate_quality = float(previous["quality"])
        self._candidate_variants = list(previous["variants"])  # type: ignore[arg-type]
        self._candidate_count = int(previous["count"])
        self._candidate_since = float(previous["since"])
        self._candidate_id = int(previous["id"])

    @staticmethod
    def _variant_quality(
        confidence: float | None,
        quality_score: float | None,
        selected_line_confidence: float | None,
    ) -> float:
        if quality_score is not None:
            return quality_score
        if selected_line_confidence is not None:
            return selected_line_confidence
        return confidence if confidence is not None else -1.0

    def _clear(self, *, no_subtitle: bool) -> None:
        self._reset_candidate()
        self._previous_cluster = None
        if no_subtitle:
            # End only the active episode. Keep the canonical text for
            # diagnostics and cooldown correlation across appearances.
            self._accepted_identity = ""
            self._accepted_lines = ()

    def _reset_candidate(self) -> None:
        self._candidate_text = ""
        self._candidate_identity = ""
        self._candidate_confidence = -1.0
        self._candidate_quality = -1.0
        self._candidate_variants = []
        self._candidate_count = 0
        self._candidate_since = 0.0
        self._needs_confirmation = False

    def _identity_match_kind(
        self,
        first: str,
        second: str,
        *,
        metrics: _PhraseMetrics,
        first_text: str = "",
        second_text: str = "",
    ) -> str:
        if not first or not second:
            return ""
        if first == second:
            return "normalized_exact"
        if _dedup_is_split_merge_variant(first, second):
            return "split_merge"
        if metrics.semantic_conflict:
            return ""
        if (
            min(len(first), len(second)) >= 5
            and self._edit_distance_at_most_one(first, second)
        ):
            return "single_edit"
        if (
            metrics.char_similarity >= self.similarity_threshold
            and _only_ocr_like_changes(first, second)
        ):
            return "similarity"
        if _same_episode_variant(first_text or first, second_text or second):
            return "multi_signal_core"
        return ""

    @staticmethod
    def _edit_distance_at_most_one(first: str, second: str) -> bool:
        if abs(len(first) - len(second)) > 1:
            return False
        if first == second:
            return True
        if len(first) == len(second):
            return sum(left != right for left, right in zip(first, second)) == 1
        shorter, longer = (first, second) if len(first) < len(second) else (second, first)
        short_index = long_index = differences = 0
        while short_index < len(shorter) and long_index < len(longer):
            if shorter[short_index] == longer[long_index]:
                short_index += 1
                long_index += 1
                continue
            differences += 1
            if differences > 1:
                return False
            long_index += 1
        return True

    def _validate(self, text: str, confidence: float | None) -> str:
        if not text:
            return "empty"
        if confidence is None or confidence < self.min_confidence:
            return "low_confidence"

        visible = [character for character in text if not character.isspace()]
        alpha_count = sum(character.isalpha() for character in visible)
        digit_count = sum(character.isdigit() for character in visible)
        alphanumeric_count = alpha_count + digit_count
        if alpha_count < 2:
            return "min_alphabetic"
        if alphanumeric_count and digit_count / alphanumeric_count > 0.50:
            return "digit_ratio"
        noise_count = self._noise_units("".join(visible))
        visible_units = alphanumeric_count + noise_count
        if visible_units and noise_count / visible_units > 0.35:
            return "symbol_ratio"

        fragments = re.findall(
            r"[^\W_]+(?:['\N{RIGHT SINGLE QUOTATION MARK}][^\W_]+)?",
            text,
        )
        isolated = sum(
            len(fragment.replace("'", "").replace("’", "")) == 1
            for fragment in fragments
        )
        if (
            len(fragments) >= 3
            and isolated >= 3
            and isolated / len(fragments) >= 0.60
        ):
            return "isolated_fragments"
        # Only tiny fragments and no word of three letters: at moderate
        # confidence this is debris (``SĄ w``), not a short dialogue line.
        if (
            len(fragments) >= 2
            and isolated >= 1
            and all(len(fragment) <= 2 for fragment in fragments)
            and confidence < 0.80
        ):
            return "isolated_fragments"

        for fragment in fragments:
            compact = fragment.replace("'", "").replace("’", "")
            letters = sum(character.isalpha() for character in compact)
            digits = sum(character.isdigit() for character in compact)
            if (
                len(compact) >= 8
                and letters
                and digits
                and digits / len(compact) >= 0.15
            ):
                return "alphanumeric_noise"
            alphabetic_runs = re.findall(r"[^\W\d_]+", compact)
            for run in alphabetic_runs:
                if len(run) < 8:
                    continue
                longest_consonant_run = max(
                    (
                        len(value)
                        for value in re.split(r"[aąeęioóuyAĄEĘIOÓUY]+", run)
                    ),
                    default=0,
                )
                if longest_consonant_run >= 7:
                    return "alphabetic_noise"
        return ""

    @staticmethod
    def _noise_units(text: str) -> int:
        """Count OCR noise while tolerating contractions and punctuation runs."""
        units = 0
        previous_terminal = False
        for index, character in enumerate(text):
            if character.isalnum():
                previous_terminal = False
                continue
            if (
                character in {"'", "’"}
                and index > 0
                and index + 1 < len(text)
                and text[index - 1].isalpha()
                and text[index + 1].isalpha()
            ):
                previous_terminal = False
                continue
            is_terminal = character in {".", "!", "?", "…"}
            if is_terminal and previous_terminal:
                continue
            units += 1
            previous_terminal = is_terminal
        return units


_DEDUP_VOWELS = frozenset("aąeęioóuy")
# q, v and x do not occur in Polish orthography, so a token containing one is
# almost always OCR noise rather than a word.
_DEDUP_FOREIGN_LETTERS = frozenset("qvx")
_DEDUP_WORD_SPLIT = re.compile(r"[^0-9a-ząćęłńóśźż]+")
_DEDUP_FOLD = str.maketrans("ąćęłńóśźż", "acelnoszz")
# A shared core must carry most of the shorter phrase and at least this many
# real words, so two unrelated lines can never collapse into one.
_DEDUP_MIN_CORE_WORDS = 3
_DEDUP_MIN_CORE_RATIO = 0.7
# Consecutive text-free observations that end a subtitle episode. More than one,
# so a single dropped or misread frame cannot re-arm the same phrase.
_DEDUP_EPISODE_ABSENT_FRAMES = SUBTITLE_DISAPPEARANCE_OBSERVATIONS


def _dedup_words(identity: str) -> list[str]:
    return [word for word in _DEDUP_WORD_SPLIT.split(identity) if word]


def _dedup_is_word(token: str) -> bool:
    """Whether a token counts towards the shared core."""

    return len(token) >= 2 and any(character.isalpha() for character in token)


def _dedup_looks_like_garbage(token: str) -> bool:
    """Whether an unmatched token is OCR noise rather than a real word."""

    letters = [character for character in token if character.isalpha()]
    if len(letters) < 2:
        return True
    if _DEDUP_FOREIGN_LETTERS.intersection(letters):
        return True
    return not any(character in _DEDUP_VOWELS for character in letters)


def _dedup_words_match(first: str, second: str, *, at_edge: bool = False) -> bool:
    """Equal words, or one truncated by OCR at a phrase edge.

    ``gent``/``agent`` and ``kojnie``/``spokojnie`` are the same word with its
    start eaten by the ROI. Interior truncation is accepted only when the same
    observation also proves that the subtitle edge was clipped. A truncation
    may lose at most three characters, so ``wróć`` can never match ``jedź``.
    """

    if first == second:
        return True
    short, long = sorted((first, second), key=len)
    if len(short) < 2 or len(long) - len(short) > 3:
        return False
    if not at_edge and not _dedup_looks_like_garbage(long):
        return False
    return long.startswith(short) or long.endswith(short)


def _dedup_absorbed(word: str, garbage_tokens: list[str]) -> bool:
    """Whether a real word was swallowed by garbage glued to it.

    ``xsasJEDZ`` contains ``jedź`` with noise welded to its front.
    """

    folded = word.translate(_DEDUP_FOLD)
    return any(
        folded in token.translate(_DEDUP_FOLD) for token in garbage_tokens
    )


def _dedup_has_edge_truncation(first: list[str], second: list[str]) -> bool:
    return any(
        left != right and _dedup_words_match(left, right, at_edge=True)
        for left, right in ((first[0], second[0]), (first[-1], second[-1]))
    )


def _dedup_core_matches(first: list[str], second: list[str]) -> int:
    """Length of the longest in-order run of matching words."""

    edge_truncated = _dedup_has_edge_truncation(first, second)
    rows = len(first)
    columns = len(second)
    table = [[0] * (columns + 1) for _ in range(rows + 1)]
    for row in range(rows - 1, -1, -1):
        for column in range(columns - 1, -1, -1):
            if _dedup_words_match(
                first[row], second[column],
                at_edge=(
                    edge_truncated or row == column == 0
                    or (row == rows - 1 and column == columns - 1)
                ),
            ):
                table[row][column] = 1 + table[row + 1][column + 1]
            else:
                table[row][column] = max(
                    table[row + 1][column], table[row][column + 1]
                )
    return table[0][0]


def _dedup_is_garbage_variant(candidate: str, spoken: str) -> bool:
    """Whether two phrases are the same line, one carrying OCR garbage.

    Only noise is forgiven. Every word that fails to align must itself look like
    garbage, or be a real word swallowed by garbage on the other side, so an
    extra or changed word inside the sentence keeps the phrases distinct.
    """

    if re.findall(r"\d+", candidate) != re.findall(r"\d+", spoken):
        return False
    candidate_words = [word for word in _dedup_words(candidate) if _dedup_is_word(word)]
    spoken_words = [word for word in _dedup_words(spoken) if _dedup_is_word(word)]
    if not candidate_words or not spoken_words:
        return False
    matched = _dedup_core_matches(candidate_words, spoken_words)
    shorter = min(len(candidate_words), len(spoken_words))
    if matched < _DEDUP_MIN_CORE_WORDS or matched / shorter < _DEDUP_MIN_CORE_RATIO:
        return False

    candidate_unmatched: list[str] = []
    spoken_unmatched: list[str] = []
    # Multiple subtitle rows can be clipped at the same ROI boundary; retain
    # internal truncation tolerance only with a matching outer-edge truncation.
    edge_truncated = _dedup_has_edge_truncation(candidate_words, spoken_words)
    remaining = list(enumerate(spoken_words))
    for candidate_index, word in enumerate(candidate_words):
        for index, (spoken_index, other) in enumerate(remaining):
            at_edge = (
                edge_truncated
                or candidate_index == spoken_index == 0
                or (
                    candidate_index == len(candidate_words) - 1
                    and spoken_index == len(spoken_words) - 1
                )
            )
            if _dedup_words_match(word, other, at_edge=at_edge):
                del remaining[index]
                break
        else:
            candidate_unmatched.append(word)
    spoken_unmatched = [word for _, word in remaining]

    candidate_garbage = [
        word for word in candidate_unmatched if _dedup_looks_like_garbage(word)
    ]
    spoken_garbage = [
        word for word in spoken_unmatched if _dedup_looks_like_garbage(word)
    ]
    for word in candidate_unmatched:
        if _dedup_looks_like_garbage(word):
            continue
        if not _dedup_absorbed(word, spoken_garbage):
            return False
    for word in spoken_unmatched:
        if _dedup_looks_like_garbage(word):
            continue
        if not _dedup_absorbed(word, candidate_garbage):
            return False
    return True


def _dedup_is_split_merge_variant(candidate: str, spoken: str) -> bool:
    """Match token-boundary OCR changes without forgiving changed characters.

    Multiple splits and merges can occur together with no token-count change.
    Number boundaries remain significant (``1 23`` is not ``12 3``).
    """

    return (
        re.findall(r"\d+", candidate) == re.findall(r"\d+", spoken)
        and candidate.replace(" ", "") == spoken.replace(" ", "")
    )


class PhraseDeduplicator:
    """Speak each subtitle once until a shared, confirmed disappearance."""

    def __init__(self) -> None:
        self._visible_phrase = ""
        self._spoken_at: dict[str, float] = {}
        self._episode_identity = ""
        self._canonical_text = ""
        self._absent_streak = 0
        self._next_episode_id = 0
        self._active_episode_id = 0
        self.last_char_similarity: float | None = None
        self.last_token_similarity: float | None = None
        self.last_rejection_reason = ""

    @property
    def episode_id(self) -> int:
        return self._active_episode_id

    @property
    def canonical_text(self) -> str:
        return self._canonical_text

    def accept(
        self,
        text: str,
        *,
        now: float,
        cooldown_seconds: float,
        frame_had_text: bool | None = None,
    ) -> str | None:
        normalized = _drop_proven_noise_tokens(_trim_symbolic_edges(text))
        identity = subtitle_identity(normalized)
        self.last_char_similarity = None
        self.last_token_similarity = None
        self.last_rejection_reason = ""
        if not identity:
            self._visible_phrase = ""
            if frame_had_text:
                self._absent_streak = 0
            else:
                self._absent_streak += 1
                if self._absent_streak >= SUBTITLE_DISAPPEARANCE_OBSERVATIONS:
                    self._episode_identity = ""
                    self._active_episode_id = 0
            return None
        self._absent_streak = 0
        if self._episode_identity:
            metrics = _phrase_metrics(normalized, self._canonical_text)
            self.last_char_similarity = metrics.char_similarity
            self.last_token_similarity = metrics.token_similarity
            if _same_episode_variant(normalized, self._canonical_text):
                self.last_rejection_reason = "active_episode_duplicate"
                return None
        if identity == self._visible_phrase:
            self.last_rejection_reason = "visible_duplicate"
            return None
        expired = [
            spoken_identity
            for spoken_identity, spoken_at in self._spoken_at.items()
            if now - spoken_at >= cooldown_seconds
        ]
        for spoken_identity in expired:
            self._spoken_at.pop(spoken_identity, None)
        previous = self._spoken_at.get(identity)
        if previous is not None:
            self.last_rejection_reason = "cooldown_exact"
            return None
        for spoken_identity in self._spoken_at:
            if _same_episode_variant(identity, spoken_identity):
                self.last_rejection_reason = "cooldown_variant"
                return None
        self._visible_phrase = identity
        self._next_episode_id += 1
        self._active_episode_id = self._next_episode_id
        self._episode_identity = identity
        self._canonical_text = normalized
        return normalized

    def mark_spoken(self, text: str, *, now: float) -> None:
        identity = subtitle_identity(text)
        if identity:
            self._spoken_at[identity] = now


def _region_pixel_rect(
    frame: CaptureFrame, region: object
) -> tuple[int, int, int, int]:
    x = max(0, min(frame.width - 1, round(float(getattr(region, "x")) * frame.width)))
    y = max(0, min(frame.height - 1, round(float(getattr(region, "y")) * frame.height)))
    width = max(
        1,
        min(frame.width - x, round(float(getattr(region, "width")) * frame.width)),
    )
    height = max(
        1,
        min(frame.height - y, round(float(getattr(region, "height")) * frame.height)),
    )
    return x, y, width, height


def crop_frame(frame: CaptureFrame, region: object) -> CaptureFrame:
    channels_by_format = {
        "rgba8888": 4,
        "bgra8888": 4,
        "rgb888": 3,
        "bgr888": 3,
        "gray8": 1,
    }
    channels = channels_by_format.get(frame.pixel_format.casefold())
    if channels is None:
        raise ValueError(f"unsupported capture pixel format: {frame.pixel_format}")
    x, y, width, height = _region_pixel_rect(frame, region)
    output_stride = width * channels
    output = bytearray(output_stride * height)
    source = memoryview(frame.pixels)
    for row in range(height):
        start = (y + row) * frame.stride + x * channels
        target = row * output_stride
        output[target : target + output_stride] = source[start : start + output_stride]
    return CaptureFrame(
        session_id=frame.session_id,
        generation=frame.generation,
        timestamp_monotonic=frame.timestamp_monotonic,
        width=width,
        height=height,
        stride=output_stride,
        pixel_format=frame.pixel_format,
        pixels=bytes(output),
        source_id=frame.source_id,
    )


class SubtitleRegionStabilizer:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._accepted_signature = b""
        self._pending_signature = b""
        self._pending_since = 0.0
        self._pending_frame: CaptureFrame | None = None
        self.last_localized_difference: float | None = None
        self.last_decision = "not_observed"

    @staticmethod
    def _signature(frame: CaptureFrame) -> bytes:
        pixels = frame.pixels
        if not pixels:
            return b""
        target = 2048
        step = max(1, len(pixels) // target)
        return bytes(pixels[::step][:target])

    @staticmethod
    def _difference(first: bytes, second: bytes) -> float:
        if not first or not second or len(first) != len(second):
            return 1.0
        return sum(abs(left - right) for left, right in zip(first, second)) / (
            255.0 * len(first)
        )

    @staticmethod
    def _localized_difference(first: bytes, second: bytes) -> float:
        """Measure localized changes without diluting subtitle pixels in a large ROI."""
        if not first or not second or len(first) != len(second):
            return 1.0
        differences = sorted(
            (abs(left - right) for left, right in zip(first, second)),
            reverse=True,
        )
        sample_count = max(1, len(differences) // 10)
        return sum(differences[:sample_count]) / (255.0 * sample_count)

    def consider_for_ocr(
        self,
        frame: CaptureFrame,
        *,
        threshold: float,
    ) -> CaptureFrame | None:
        """Promptly sample localized subtitle changes; text consensus adds safety."""
        signature = self._signature(frame)
        if self._accepted_signature:
            difference = self._localized_difference(
                signature, self._accepted_signature
            )
            self.last_localized_difference = difference
            if difference < threshold:
                self.last_decision = "unchanged"
                return None
            self.last_decision = "localized_change"
        else:
            self.last_localized_difference = None
            self.last_decision = "initial_probe"
        self._accepted_signature = signature
        self._pending_signature = b""
        self._pending_frame = None
        self._pending_since = 0.0
        return frame

    def consider(
        self,
        frame: CaptureFrame,
        *,
        threshold: float,
        stabilization_seconds: float,
    ) -> CaptureFrame | None:
        signature = self._signature(frame)
        now = frame.timestamp_monotonic
        if self._accepted_signature and self._difference(
            signature, self._accepted_signature
        ) < threshold:
            self._pending_signature = b""
            self._pending_frame = None
            self._pending_since = 0.0
            return None
        if not self._pending_signature or self._difference(
            signature, self._pending_signature
        ) >= threshold:
            self._pending_signature = signature
            self._pending_since = now
            self._pending_frame = frame
            return None
        self._pending_frame = frame
        if now - self._pending_since < stabilization_seconds:
            return None
        accepted = self._pending_frame
        self._accepted_signature = signature
        self._pending_signature = b""
        self._pending_frame = None
        return accepted


class UnavailableOcrProvider:
    provider_id = "unavailable"
    available = False

    def recognize(self, frame: CaptureFrame, *, language: str) -> OcrResult:
        del frame, language
        raise RuntimeError("No local OCR provider is installed")


class UnavailableTranslationProvider:
    provider_id = "unavailable"
    available = False

    def translate(
        self,
        text: str,
        *,
        source_language: str,
        target_language: str,
        profile_id: str,
    ) -> TranslationResult:
        del text, source_language, target_language, profile_id
        raise RuntimeError("No local translation provider is installed")

    def cancel(self) -> None:
        return


class UnavailableTtsProvider:
    provider_id = "unavailable"
    available = False

    def synthesize(
        self,
        text: str,
        *,
        language: str,
        voice_id: str,
        speech_rate: float,
    ) -> PcmAudio:
        del text, language, voice_id, speech_rate
        raise RuntimeError("No Polish TTS provider is installed")

    def cancel(self) -> None:
        return


class UnavailableAudioOutput:
    provider_id = "unavailable"
    available = False

    def play(
        self,
        audio: PcmAudio,
        *,
        volume: float,
        request_id: int,
        started_callback: Callable[[float], None],
        completed_callback: Callable[[], None],
        error_callback: Callable[[str], None],
        text: str = "",
    ) -> None:
        del (
            audio,
            volume,
            request_id,
            started_callback,
            completed_callback,
            error_callback,
            text,
        )
        raise RuntimeError("Narrator audio output is unavailable")

    def stop(self) -> None:
        return


class NarratorPipeline:
    """Run capture and inference without blocking or calling Qt from workers."""

    def __init__(
        self,
        capture: ScreenCaptureProvider,
        ocr: OcrProvider,
        translator: TranslationProvider,
        tts: TtsProvider,
        audio: NarratorAudioOutput,
        activity: GameActivityProvider,
        translation_cache: TranslationCache | None = None,
        *,
        executor: Executor | None = None,
        tts_executor: Executor | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capture = capture
        self.ocr = ocr
        self.translator = translator
        self.tts = tts
        self.audio = audio
        self.activity = activity
        self.cache = translation_cache or TranslationCache()
        self._executor = executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="game-optimization-narrator"
        )
        self._owns_executor = executor is None
        if tts_executor is not None:
            self._tts_executor = tts_executor
            self._owns_tts_executor = False
        elif executor is None:
            self._tts_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="game-optimization-narrator-tts",
            )
            self._owns_tts_executor = True
        else:
            # Lightweight deterministic tests may intentionally share one
            # injected executor; production always takes the dedicated branch.
            self._tts_executor = self._executor
            self._owns_tts_executor = False
        self._clock = clock
        self._events: SimpleQueue[NarratorEvent] = SimpleQueue()
        self._lock = RLock()
        self._settings: NarratorGameSettings | None = None
        self._session_id = ""
        self._generation = 0
        self._request_id = 0
        self._work_id = 0
        self._latest_audio_request_id = 0
        self._stabilizer = SubtitleRegionStabilizer()
        self._text_gate = SubtitleTextGate()
        self._deduplicator = PhraseDeduplicator()
        self._ocr_rejection_counts: dict[str, int] = {}
        # Per-session loss funnel. Bounded: one integer per known decision name.
        self._funnel: dict[str, int] = {}
        # Bounded: subtitles that had a readable line and were rejected anyway.
        self._lost_strong_lines: deque[dict[str, object]] = deque(maxlen=10)
        self._last_ocr_diagnostic: tuple[str, str, float | None] | None = None
        self._last_ocr_backend_diagnostic: tuple[str, str] | None = None
        self._roi_diagnostic: dict[str, object] | None = None
        self._roi_diagnostic_logged = False
        self._ocr_future: Future[OcrResult] | None = None
        self._ocr_prepare_future: Future[object] | None = None
        self._tts_prepare_future: Future[object] | None = None
        self._tts_prepare_started_at: float | None = None
        self._request_active = False
        self._pending_frame: CaptureFrame | None = None
        self._pending_frame_stabilization_ms = 0.0
        self._pending_frame_visual_decision = ""
        self._last_sampled_at: float | None = None
        self._first_visible_frame_timestamp: float | None = None
        self._inactive_since: float | None = None
        self._stage_futures: set[Future[object]] = set()
        self._stage_kinds: dict[Future[object], str] = {}
        self._tts_futures: set[Future[object]] = set()
        self._audio_supersession_baseline = 0
        self._latest_capture_frame: CaptureFrame | None = None
        self._latest_capture_game_key = ""
        self._preview_generation = 0
        self._preview_session_id = ""
        self._preview_game_key = ""
        self._preview_temporary = False
        self._preview_delivered = False
        self._preview_frame_callback: Callable[[CaptureFrame], None] | None = None
        self._preview_state_callback: Callable[[CaptureState, str], None] | None = None
        self._snapshot = NarratorSessionSnapshot()

    @property
    def snapshot(self) -> NarratorSessionSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def active(self) -> bool:
        return self.snapshot.status not in {
            NarratorSessionStatus.IDLE,
            NarratorSessionStatus.STOPPED,
            NarratorSessionStatus.ERROR,
        }

    def _tts_queue_depth(self) -> int:
        with self._lock:
            return sum(not future.done() for future in self._tts_futures)

    def _audio_queue_depth(self) -> int:
        try:
            return max(0, int(getattr(self.audio, "queue_depth", 0)))
        except (TypeError, ValueError):
            return 0

    def _log_stage(
        self,
        event: str,
        *,
        session_id: str,
        phrase_id: int,
        duration_ms: float,
        queue_depth: int,
        model: str,
    ) -> None:
        worker_pid = int(getattr(self.tts, "worker_pid", 0) or 0)
        worker_thread = str(
            getattr(self.tts, "worker_thread_name", "") or "unknown"
        )
        worker_nice = getattr(self.tts, "worker_process_nice", None)
        intra = int(getattr(self.tts, "worker_onnx_intra_op_threads", 0) or 0)
        inter = int(getattr(self.tts, "worker_onnx_inter_op_threads", 0) or 0)
        mode = str(
            getattr(self.tts, "worker_onnx_execution_mode", "") or "unknown"
        )
        logger.info(
            "Narrator stage event=%s session=%s phrase_id=%d duration_ms=%.1f "
            "queue_depth=%d pid=%d thread=%s model=%s worker_pid=%d "
            "worker_thread=%s worker_nice=%s onnx_intra=%d onnx_inter=%d "
            "onnx_mode=%s",
            event,
            session_id,
            phrase_id,
            max(0.0, duration_ms),
            max(0, queue_depth),
            os.getpid(),
            current_thread().name,
            model,
            worker_pid,
            worker_thread,
            worker_nice if worker_nice is not None else "unknown",
            intra,
            inter,
            mode,
        )

    def latest_preview_frame(self, game_key: str) -> CaptureFrame | None:
        """Return the last in-memory full capture frame for one game."""

        with self._lock:
            if self._latest_capture_game_key != str(game_key):
                return None
            return self._latest_capture_frame

    def request_preview_frame(
        self,
        settings: NarratorGameSettings,
        *,
        frame_callback: Callable[[CaptureFrame], None],
        state_callback: Callable[[CaptureState, str], None],
    ) -> None:
        """Reuse an active stream or request one portal frame for ROI setup."""

        self.cancel_preview_frame()
        with self._lock:
            if self.active:
                if self._snapshot.game_key != settings.game_key:
                    raise RuntimeError(
                        "Stop the active Narrator session for the other game first"
                    )
                frame = (
                    self._latest_capture_frame
                    if self._latest_capture_game_key == settings.game_key
                    else None
                )
                if frame is None:
                    self._preview_game_key = settings.game_key
                    self._preview_temporary = False
                    self._preview_frame_callback = frame_callback
                    self._preview_state_callback = state_callback
                else:
                    frame_callback(frame)
                state_callback(CaptureState.ACTIVE, "")
                return
            capabilities = self.capture.capabilities()
            if not capabilities.available:
                raise RuntimeError(
                    capabilities.message or "Screen capture is unavailable"
                )
            self._preview_generation += 1
            generation = self._preview_generation
            session_id = f"region-{uuid4().hex}"
            self._preview_session_id = session_id
            self._preview_game_key = settings.game_key
            self._preview_temporary = True
            self._preview_delivered = False
            self._preview_frame_callback = frame_callback
            self._preview_state_callback = state_callback
            request = CaptureRequest(
                session_id=session_id,
                game_key=settings.game_key,
                generation=generation,
                source_type=settings.capture_source,
                sampling_hz=1.0,
            )
        self.capture.start(
            request,
            frame_callback=self._preview_frame_received,
            state_callback=self._preview_state_changed,
        )

    def cancel_preview_frame(self) -> None:
        """Cancel only a temporary selector capture, never active narration."""

        with self._lock:
            temporary = self._preview_temporary
            self._preview_generation += 1
            self._preview_session_id = ""
            self._preview_game_key = ""
            self._preview_temporary = False
            self._preview_delivered = False
            self._preview_frame_callback = None
            self._preview_state_callback = None
        if temporary:
            self.capture.stop()

    def _preview_frame_received(self, frame: CaptureFrame) -> None:
        with self._lock:
            if (
                not self._preview_temporary
                or self._preview_delivered
                or frame.session_id != self._preview_session_id
                or frame.generation != self._preview_generation
            ):
                return
            callback = self._preview_frame_callback
            game_key = self._preview_game_key
            self._preview_delivered = True
            self._latest_capture_frame = frame
            self._latest_capture_game_key = game_key
        if callback is not None:
            callback(frame)

    def _preview_state_changed(self, state: CaptureState, message: str) -> None:
        with self._lock:
            callback = self._preview_state_callback
            temporary = self._preview_temporary
        if temporary and callback is not None:
            callback(state, message)

    def missing_requirements(
        self, settings: NarratorGameSettings | None = None
    ) -> tuple[str, ...]:
        selected = settings or self._settings
        polish_mode = bool(
            selected is not None
            and selected.subtitle_language_mode
            is NarratorSubtitleLanguageMode.POLISH
        )
        missing: list[str] = []
        if not self.capture.capabilities().available:
            missing.append("capture")
        if not self._ocr_language_available("pl" if polish_mode else "en"):
            missing.append("ocr")
        if not polish_mode and not self.translator.available:
            missing.append("translation")
        if not self.tts.available:
            missing.append("tts")
        if not self.audio.available:
            missing.append("audio")
        return tuple(missing)

    def full_narration_available(
        self, settings: NarratorGameSettings | None = None
    ) -> bool:
        selected = settings or self._settings
        translator_ready = bool(
            selected is not None
            and selected.subtitle_language_mode
            is NarratorSubtitleLanguageMode.POLISH
        ) or self.translator.available
        return bool(
            translator_ready and self.tts.available and self.audio.available
        )

    def _ocr_language_available(self, language: str) -> bool:
        language_available = getattr(self.ocr, "language_available", None)
        if callable(language_available):
            return bool(language_available(language))
        return bool(self.ocr.available)

    def start(self, settings: NarratorGameSettings) -> NarratorSessionSnapshot:
        self.cancel_preview_frame()
        with self._lock:
            if self.active:
                raise RuntimeError("A narrator session is already active")
            if not settings.enabled:
                raise RuntimeError("Narrator is disabled for this game")
            if settings.source_mode is NarratorSourceMode.ADAPTER:
                raise RuntimeError("No game subtitle adapter is available")
            settings = self._validated_settings(settings)
            missing = self.missing_requirements(settings)
            if missing:
                raise RuntimeError(
                    "Narrator components are unavailable: " + ", ".join(missing)
                )
            activity = self.activity.is_active(settings.game_key)
            if activity is False:
                raise RuntimeError("The selected game is not running")
            self._generation += 1
            self._request_id = 0
            self._work_id = 0
            self._latest_audio_request_id = 0
            self._session_id = uuid4().hex
            self._settings = settings
            self._stabilizer = SubtitleRegionStabilizer()
            self._text_gate = SubtitleTextGate(
                min_confidence=settings.ocr_min_confidence
            )
            self._deduplicator = PhraseDeduplicator()
            self._ocr_rejection_counts = {}
            self._funnel = {}
            self._lost_strong_lines.clear()
            self._last_ocr_diagnostic = None
            self._last_ocr_backend_diagnostic = None
            self._roi_diagnostic = None
            self._roi_diagnostic_logged = False
            self._request_active = False
            self._pending_frame = None
            self._pending_frame_stabilization_ms = 0.0
            self._pending_frame_visual_decision = ""
            self._last_sampled_at = None
            self._first_visible_frame_timestamp = None
            self._inactive_since = None
            self._audio_supersession_baseline = int(
                getattr(self.audio, "superseded_count", 0)
            )
            self._snapshot = NarratorSessionSnapshot(
                session_id=self._session_id,
                game_key=settings.game_key,
                status=NarratorSessionStatus.STARTING,
                generation=self._generation,
                capture_state=CaptureState.STARTING.value,
                ocr_status="ready",
                translation_status=(
                    "bypassed"
                    if settings.subtitle_language_mode
                    is NarratorSubtitleLanguageMode.POLISH
                    else (
                        "ready"
                        if self.translator.available
                        else "component_missing"
                    )
                ),
                tts_status="ready" if self.tts.available else "component_missing",
                audio_status="ready" if self.audio.available else "unavailable",
            )
            self._emit(NarratorSessionStatus.STARTING)
            logger.info(
                "Narrator session event=started session=%s pid=%d thread=%s",
                self._session_id,
                os.getpid(),
                current_thread().name,
            )
            ocr_language = (
                "pl"
                if settings.subtitle_language_mode
                is NarratorSubtitleLanguageMode.POLISH
                else "en"
            )
            prepare_ocr = getattr(self.ocr, "prepare", None)
            if callable(prepare_ocr):
                self._ocr_prepare_future = self._executor.submit(
                    prepare_ocr, ocr_language
                )
                self._ocr_prepare_future.add_done_callback(
                    self._ocr_preparation_finished
                )
            prepare_tts = getattr(self.tts, "prepare", None)
            if callable(prepare_tts):
                self._tts_prepare_started_at = self._clock()
                self._tts_prepare_future = self._tts_executor.submit(
                    prepare_tts, settings.voice_id
                )
                self._tts_futures.add(self._tts_prepare_future)
                self._tts_prepare_future.add_done_callback(
                    self._tts_preparation_finished
                )
            request = CaptureRequest(
                session_id=self._session_id,
                game_key=settings.game_key,
                generation=self._generation,
                source_type=settings.capture_source,
                sampling_hz=settings.capture_sampling_hz,
            )
        self.capture.start(
            request,
            frame_callback=self.submit_frame,
            state_callback=self._capture_state_changed,
        )
        return self.snapshot

    def _validated_settings(
        self, settings: NarratorGameSettings
    ) -> NarratorGameSettings:
        polish_mode = (
            settings.subtitle_language_mode is NarratorSubtitleLanguageMode.POLISH
        )
        selected_providers = [
            ("OCR", settings.ocr_provider_id, self.ocr.provider_id),
            ("speech", settings.tts_provider_id, self.tts.provider_id),
        ]
        if not polish_mode:
            selected_providers.insert(
                1,
                (
                    "translation",
                    settings.translation_provider_id,
                    self.translator.provider_id,
                ),
            )
        for label, selected, active in selected_providers:
            if selected and selected != active:
                raise RuntimeError(
                    f"The selected {label} provider is not available: {selected}"
                )

        profile_id = settings.translation_profile_id
        if not polish_mode:
            profiles = tuple(
                str(value) for value in getattr(self.translator, "profile_ids", ())
            )
            profile_id = profile_id or str(
                getattr(self.translator, "default_profile_id", "")
            )
            if not profile_id and profiles:
                profile_id = profiles[0]
            if profiles and profile_id not in profiles:
                raise RuntimeError(
                    f"The selected translation profile is not available: {profile_id}"
                )

        voices = tuple(
            str(value) for value in getattr(self.tts, "available_voice_ids", ())
        )
        voice_id = settings.voice_id or str(
            getattr(self.tts, "default_voice_id", "")
        )
        if voices and voice_id not in voices:
            # Never require one specific voice: use the first installed one.
            voice_id = voices[0]

        return replace(
            settings,
            ocr_provider_id=settings.ocr_provider_id or self.ocr.provider_id,
            translation_provider_id=(
                settings.translation_provider_id
                if polish_mode
                else settings.translation_provider_id or self.translator.provider_id
            ),
            translation_profile_id=profile_id,
            tts_provider_id=settings.tts_provider_id or self.tts.provider_id,
            voice_id=voice_id,
        )

    def stop(self, message: str = "") -> NarratorSessionSnapshot:
        with self._lock:
            if not self.active:
                return self._snapshot
            logger.debug(
                "Narrator work counters capture_sampling=%d "
                "capture_coalesced=%d unstable_ocr=%d "
                "translation_queued=%d translation_running=%d "
                "tts_queued=%d tts_running=%d audio_supersessions=%d",
                self._snapshot.dropped_capture_sampling,
                self._snapshot.dropped_capture_coalesced,
                self._snapshot.unstable_ocr_observations,
                self._snapshot.stale_queued_translation,
                self._snapshot.stale_running_translation_results,
                self._snapshot.stale_queued_tts,
                self._snapshot.stale_running_tts_results,
                self._snapshot.audio_supersessions,
            )
            settings = self._settings
            self._generation += 1
            self._request_id += 1
            self._work_id += 1
            self._latest_audio_request_id = 0
            self._request_active = False
            self._pending_frame = None
            self._pending_frame_stabilization_ms = 0.0
            self._pending_frame_visual_decision = ""
            self._first_visible_frame_timestamp = None
            if self._ocr_future is not None:
                self._ocr_future.cancel()
            if self._ocr_prepare_future is not None:
                self._ocr_prepare_future.cancel()
                self._ocr_prepare_future = None
            if self._tts_prepare_future is not None:
                self._tts_prepare_future.cancel()
                self._tts_prepare_future = None
            self._tts_prepare_started_at = None
            for future in tuple(self._stage_futures):
                future.cancel()
            self._stage_futures.clear()
            self._stage_kinds.clear()
            self._cancel_provider_work()
            game_key = self._snapshot.game_key
            session_id = self._snapshot.session_id
        self.capture.stop()
        self.audio.stop()
        with self._lock:
            self._settings = None
            self._snapshot = NarratorSessionSnapshot(
                session_id=session_id,
                game_key=game_key,
                status=NarratorSessionStatus.STOPPED,
                message=message,
                generation=self._generation,
                capture_state=CaptureState.STOPPED.value,
                ocr_status=(
                    "ready"
                    if self._ocr_language_available(
                        "pl"
                        if settings is not None
                        and settings.subtitle_language_mode
                        is NarratorSubtitleLanguageMode.POLISH
                        else "en"
                    )
                    else "component_missing"
                ),
                translation_status=(
                    "bypassed"
                    if settings is not None
                    and settings.subtitle_language_mode
                    is NarratorSubtitleLanguageMode.POLISH
                    else (
                        "ready"
                        if self.translator.available
                        else "component_missing"
                    )
                ),
                tts_status="ready" if self.tts.available else "component_missing",
                audio_status="stopped" if self.audio.available else "unavailable",
            )
            self._events.put(
                NarratorEvent(
                    session_id=session_id,
                    game_key=game_key,
                    generation=self._generation,
                    status=NarratorSessionStatus.STOPPED,
                    message=message,
                )
            )
            logger.info(
                "Narrator session event=stopped session=%s pid=%d thread=%s",
                session_id,
                os.getpid(),
                current_thread().name,
            )
            return self._snapshot

    def poll_game_activity(self) -> None:
        snapshot = self.snapshot
        if not self.active or not snapshot.game_key:
            return
        active = self.activity.is_active(snapshot.game_key)
        with self._lock:
            if active is not False:
                self._inactive_since = None
                return
            now = self._clock()
            if self._inactive_since is None:
                self._inactive_since = now
                return
            if now - self._inactive_since < 2.0:
                return
        self.stop("The game exited")

    def submit_frame(self, frame: CaptureFrame) -> None:
        with self._lock:
            received_at = self._clock()
            settings = self._settings
            if (
                settings is None
                or frame.session_id != self._session_id
                or frame.generation != self._generation
            ):
                return
            self._latest_capture_frame = frame
            self._latest_capture_game_key = settings.game_key
            if (
                not self._preview_temporary
                and self._preview_game_key == settings.game_key
                and self._preview_frame_callback is not None
            ):
                preview_callback = self._preview_frame_callback
                self._preview_frame_callback = None
                self._preview_state_callback = None
                self._preview_game_key = ""
                preview_callback(frame)
            sampling_interval = 1.0 / settings.capture_sampling_hz
            if (
                self._last_sampled_at is not None
                and frame.timestamp_monotonic >= self._last_sampled_at
                and frame.timestamp_monotonic - self._last_sampled_at
                < sampling_interval
            ):
                self._snapshot = replace(
                    self._snapshot,
                    dropped_frames=self._snapshot.dropped_frames + 1,
                    dropped_capture_sampling=(
                        self._snapshot.dropped_capture_sampling + 1
                    ),
                )
                return
            self._last_sampled_at = frame.timestamp_monotonic
            frame_acquisition_ms = max(
                0.0, (received_at - frame.timestamp_monotonic) * 1000.0
            )
            started = self._clock()
            try:
                pixel_rect = _region_pixel_rect(frame, settings.subtitle_region)
                cropped = crop_frame(frame, settings.subtitle_region)
            except Exception as error:
                self._recoverable_error(f"Could not crop the subtitle region: {error}")
                return
            if self._roi_diagnostic is None:
                region = settings.subtitle_region
                self._roi_diagnostic = {
                    "frame_width": frame.width,
                    "frame_height": frame.height,
                    "region_x": region.x,
                    "region_y": region.y,
                    "region_width": region.width,
                    "region_height": region.height,
                    "region_source": settings.subtitle_region_source,
                    "pixel_rect": pixel_rect,
                    "crop_width": cropped.width,
                    "crop_height": cropped.height,
                }
            capture_ms = max(0.0, (self._clock() - started) * 1000.0)
            stabilization_started = self._clock()
            if self._text_gate.needs_confirmation:
                stable = cropped
                visual_decision = "text_confirmation"
                visual_difference = None
            else:
                consider_for_ocr = getattr(
                    self._stabilizer, "consider_for_ocr", None
                )
                if callable(consider_for_ocr):
                    stable = consider_for_ocr(
                        cropped,
                        threshold=settings.visual_change_threshold,
                    )
                    visual_decision = str(
                        getattr(self._stabilizer, "last_decision", "unknown")
                    )
                    visual_difference = getattr(
                        self._stabilizer, "last_localized_difference", None
                    )
                else:
                    # Compatibility for lightweight test/provider stabilizers.
                    stable = self._stabilizer.consider(
                        cropped,
                        threshold=settings.visual_change_threshold,
                        stabilization_seconds=settings.stabilization_ms / 1000.0,
                    )
                    visual_decision = "legacy_stabilizer"
                    visual_difference = None
            stabilization_ms = max(
                0.0, (self._clock() - stabilization_started) * 1000.0
            )
            if stable is None:
                self._snapshot = replace(
                    self._snapshot,
                    frame_acquisition_ms=frame_acquisition_ms,
                    capture_ms=capture_ms,
                    stabilization_dedup_ms=stabilization_ms,
                    capture_width=frame.width,
                    capture_height=frame.height,
                    last_visual_change_decision=visual_decision,
                    last_visual_change_score=visual_difference,
                )
                return
            logger.debug(
                "Narrator subtitle ROI observation source=%s size=%dx%d "
                "decision=%s localized_difference=%s sent_to_ocr=true",
                stable.source_id or "unknown",
                stable.width,
                stable.height,
                visual_decision,
                (
                    f"{visual_difference:.4f}"
                    if visual_difference is not None
                    else "none"
                ),
            )
            self._snapshot = replace(
                self._snapshot,
                frame_acquisition_ms=frame_acquisition_ms,
                capture_ms=capture_ms,
                stabilization_dedup_ms=stabilization_ms,
                capture_width=frame.width,
                capture_height=frame.height,
                last_visual_change_decision=visual_decision,
                last_visual_change_score=visual_difference,
            )
            if self._request_active:
                if self._pending_frame is not None:
                    self._snapshot = replace(
                        self._snapshot,
                        dropped_frames=self._snapshot.dropped_frames + 1,
                        dropped_capture_coalesced=(
                            self._snapshot.dropped_capture_coalesced + 1
                        ),
                )
                self._pending_frame = stable
                self._pending_frame_stabilization_ms = stabilization_ms
                self._pending_frame_visual_decision = visual_decision
                return
            self._start_ocr(
                stable,
                stabilization_ms=stabilization_ms,
                visual_decision=visual_decision,
            )

    def drain_events(self) -> list[NarratorEvent]:
        events: list[NarratorEvent] = []
        while not self._events.empty():
            events.append(self._events.get())
        return events

    def shutdown(self) -> None:
        self.cancel_preview_frame()
        self.stop()
        if self._owns_tts_executor:
            self._tts_executor.shutdown(wait=True, cancel_futures=True)
        close_capture = getattr(self.capture, "close", None)
        if callable(close_capture):
            close_capture()
        for provider in (self.ocr, self.translator, self.tts):
            close = getattr(provider, "close", None)
            if callable(close):
                close()
        self.cache.close()
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _ocr_preparation_finished(self, future: Future[object]) -> None:
        with self._lock:
            if future is not self._ocr_prepare_future:
                return
            self._ocr_prepare_future = None
        if future.cancelled():
            return
        try:
            future.result()
        except Exception as error:
            logger.debug(
                "Persistent OCR warm-up did not complete; recognition will "
                "retry and retain the CLI fallback: %s",
                error,
            )

    def _tts_preparation_finished(self, future: Future[object]) -> None:
        with self._lock:
            self._tts_futures.discard(future)
            if future is not self._tts_prepare_future:
                return
            self._tts_prepare_future = None
            started_at = self._tts_prepare_started_at
            self._tts_prepare_started_at = None
            session_id = self._session_id
        if future.cancelled():
            return
        try:
            future.result()
        except Exception as error:
            logger.debug(
                "Persistent Piper warm-up did not complete; synthesis will "
                "retry on demand: %s",
                error,
            )
            return
        reused = getattr(self.tts, "model_prepare_reused", None)
        model = "warm" if reused is True else "cold" if reused is False else "unknown"
        duration_ms = (
            max(0.0, (self._clock() - started_at) * 1000.0)
            if started_at is not None
            else float(getattr(self.tts, "worker_initialization_ms", 0.0) or 0.0)
        )
        self._log_stage(
            "tts_model_ready",
            session_id=session_id,
            phrase_id=0,
            duration_ms=duration_ms,
            queue_depth=self._tts_queue_depth(),
            model=model,
        )

    def _start_ocr(
        self,
        frame: CaptureFrame,
        *,
        stabilization_ms: float = 0.0,
        visual_decision: str = "",
    ) -> None:
        settings = self._settings
        if settings is None:
            return
        generation = self._generation
        self._request_id += 1
        request_id = self._request_id
        self._request_active = True
        self._snapshot = replace(
            self._snapshot,
            ocr_status="processing",
            ocr_execution_count=self._snapshot.ocr_execution_count + 1,
            ocr_roi_width=frame.width,
            ocr_roi_height=frame.height,
        )
        self._emit(NarratorSessionStatus.OCR)
        language = (
            "pl"
            if settings.subtitle_language_mode is NarratorSubtitleLanguageMode.POLISH
            else "en"
        )
        logger.debug(
            "Narrator OCR ROI received request=%d generation=%d source=%s "
            "language=%s size=%dx%d frame_timestamp=%.6f",
            request_id,
            generation,
            frame.source_id or "unknown",
            language,
            frame.width,
            frame.height,
            frame.timestamp_monotonic,
        )
        future = self._executor.submit(
            self.ocr.recognize, frame, language=language
        )
        self._ocr_future = future
        future.add_done_callback(
            lambda completed: self._ocr_finished(
                completed,
                generation=generation,
                request_id=request_id,
                frame_timestamp=frame.timestamp_monotonic,
                stabilization_ms=stabilization_ms,
                visual_decision=visual_decision,
            )
        )

    def _ocr_finished(
        self,
        future: Future[OcrResult],
        *,
        generation: int,
        request_id: int,
        frame_timestamp: float,
        stabilization_ms: float,
        visual_decision: str,
    ) -> None:
        try:
            result = future.result()
        except Exception as error:
            with self._lock:
                if (
                    generation == self._generation
                    and request_id == self._request_id
                ):
                    self._recoverable_error(f"OCR failed: {error}")
                    self._finish_ocr_request()
            return
        with self._lock:
            settings = self._settings
            if settings is None or generation != self._generation:
                return
            gate_started = self._clock()
            now = gate_started
            frame_had_text = bool(
                (result.raw_text or result.filtered_text or result.text).strip()
            )
            observation = self._text_gate.observe(
                result.filtered_text or result.text,
                result.confidence,
                now=now,
                raw_text=result.raw_text or result.text,
                strong_short_phrase_evidence=(
                    result.clean_short_phrase_evidence
                    and visual_decision in {"initial_probe", "localized_change"}
                ),
                quality_score=result.quality_score,
                selected_line_confidence=result.selected_line_confidence,
                leading_low_quality_tokens=result.leading_low_quality_tokens,
                trailing_low_quality_tokens=result.trailing_low_quality_tokens,
                frame_had_text=frame_had_text,
            )
            if observation.reason_code == "confirmed_disappearance":
                reset_stabilizer = getattr(self._stabilizer, "reset", None)
                if callable(reset_stabilizer):
                    reset_stabilizer()
            if not self._roi_diagnostic_logged and self._roi_diagnostic is not None:
                diagnostic = self._roi_diagnostic
                pixel_x, pixel_y, pixel_width, pixel_height = diagnostic["pixel_rect"]
                processed_width = (
                    result.preprocessed_width
                    or result.input_width
                    or int(diagnostic["crop_width"])
                )
                processed_height = (
                    result.preprocessed_height
                    or result.input_height
                    or int(diagnostic["crop_height"])
                )
                logger.info(
                    "Narrator OCR ROI: frame=%dx%d "
                    "region=(%.6f,%.6f,%.6f,%.6f) source=%s "
                    "rect=(%d,%d,%d,%d) crop=%dx%d tesseract=%dx%d",
                    diagnostic["frame_width"],
                    diagnostic["frame_height"],
                    diagnostic["region_x"],
                    diagnostic["region_y"],
                    diagnostic["region_width"],
                    diagnostic["region_height"],
                    diagnostic["region_source"],
                    pixel_x,
                    pixel_y,
                    pixel_width,
                    pixel_height,
                    diagnostic["crop_width"],
                    diagnostic["crop_height"],
                    processed_width,
                    processed_height,
                )
                self._roi_diagnostic_logged = True
            backend_diagnostic = (result.backend, result.fallback_reason)
            if backend_diagnostic != self._last_ocr_backend_diagnostic:
                logger.debug(
                    "Narrator OCR backend=%s complete=%.1fms image=%.1fms "
                    "png=%.1fms lock_wait=%.1fms roundtrip=%.1fms "
                    "worker=%.1fms decode=%.1fms recognition=%.1fms "
                    "client_overhead=%.1fms startup=%.1fms restarted=%s "
                    "fallback_reason=%s debug_capture=%s",
                    result.backend or "unknown",
                    result.elapsed_ms,
                    result.image_preprocessing_ms,
                    result.png_encoding_ms,
                    result.worker_lock_wait_ms or 0.0,
                    result.worker_roundtrip_ms or 0.0,
                    result.worker_execution_ms or 0.0,
                    result.worker_decode_ms or 0.0,
                    result.recognition_ms or 0.0,
                    result.client_overhead_ms or 0.0,
                    result.worker_startup_ms or 0.0,
                    result.worker_restarted,
                    result.fallback_reason or "none",
                    result.debug_capture_path or "none",
                )
                self._last_ocr_backend_diagnostic = backend_diagnostic
            if observation.candidate_started:
                self._first_visible_frame_timestamp = frame_timestamp
            rejection_reason = observation.rejection_reason
            if rejection_reason:
                self._ocr_rejection_counts[rejection_reason] = (
                    self._ocr_rejection_counts.get(rejection_reason, 0) + 1
                )
            if rejection_reason == "unstable":
                self._snapshot = replace(
                    self._snapshot,
                    unstable_ocr_observations=(
                        self._snapshot.unstable_ocr_observations + 1
                    ),
                )
            logger.debug(
                "Narrator OCR observation request=%d backend=%s fallback=%s "
                "raw=%r normalized=%r confidence=%s credible=%s decision=%s "
                "rejection=%s candidate=%r observations=%d/%d similarity=%s "
                "match=%s accepted=%r recognition=%.1fms complete=%.1fms "
                "debug_capture=%s",
                request_id,
                result.backend or "unknown",
                result.fallback_reason or "none",
                observation.raw_text,
                observation.filtered_text,
                (
                    f"{observation.confidence:.3f}"
                    if observation.confidence is not None
                    else "none"
                ),
                observation.credible,
                observation.decision or "none",
                rejection_reason or "none",
                observation.candidate_text,
                observation.candidate_observation_count,
                observation.required_observations,
                (
                    f"{observation.candidate_similarity:.3f}"
                    if observation.candidate_similarity is not None
                    else "none"
                ),
                observation.candidate_match_kind or "none",
                observation.accepted_text,
                result.recognition_ms or 0.0,
                result.elapsed_ms,
                result.debug_capture_path or "none",
            )

            # Gate and episode latch consume the same raw-text presence signal.
            # Therefore one empty frame cannot mean two different states.
            canonical_before = self._deduplicator.canonical_text
            episode_metrics = (
                _phrase_metrics(observation.filtered_text, canonical_before)
                if observation.filtered_text and canonical_before
                else None
            )
            self._deduplicator.accept(
                "",
                now=now,
                cooldown_seconds=settings.duplicate_cooldown_ms / 1000.0,
                frame_had_text=frame_had_text,
            )
            phrase = (
                self._deduplicator.accept(
                    observation.accepted_text,
                    now=self._clock(),
                    cooldown_seconds=settings.duplicate_cooldown_ms / 1000.0,
                )
                if observation.accepted_text
                else None
            )
            final_decision = observation.decision or "rejected_unknown"
            reason_code = observation.reason_code or rejection_reason or final_decision
            if observation.accepted_text and phrase is None:
                final_decision = "rejected_duplicate"
                rejection_reason = "duplicate"
                reason_code = (
                    self._deduplicator.last_rejection_reason
                    or "active_episode_duplicate"
                )
                self._ocr_rejection_counts["duplicate"] = (
                    self._ocr_rejection_counts.get("duplicate", 0) + 1
                )
            elif phrase:
                final_decision = "accepted"
                reason_code = "new_episode_consensus"
            line_similarity = self._text_gate.last_line_similarity
            char_similarity = (
                observation.char_similarity
                if observation.char_similarity is not None
                else episode_metrics.char_similarity if episode_metrics is not None else None
            )
            token_similarity = (
                observation.token_similarity
                if observation.token_similarity is not None
                else episode_metrics.token_similarity if episode_metrics is not None else None
            )
            canonical_text = (
                self._deduplicator.canonical_text
                or observation.canonical_text
                or canonical_before
            )
            ocr_log = (
                logger.info
                if (
                    final_decision == "accepted"
                    or final_decision.startswith("candidate_reset_")
                    or reason_code in {"candidate_timeout", "confirmed_disappearance"}
                )
                else logger.debug
            )
            ocr_log(
                "Narrator OCR: raw=%r cleaned=%r similarity=%s decision=%s "
                "confidence=%s quality=%s line_confidence=%s episode_id=%d "
                "candidate_id=%d strong_votes=%d required_votes=%d canonical=%r "
                "char_similarity=%s token_similarity=%s reason=%s",
                observation.raw_text,
                observation.filtered_text,
                f"{line_similarity:.3f}" if line_similarity is not None else "none",
                final_decision,
                (
                    f"{observation.confidence:.3f}"
                    if observation.confidence is not None
                    else "none"
                ),
                (
                    f"{observation.quality_score:.3f}"
                    if observation.quality_score is not None
                    else "none"
                ),
                (
                    f"{observation.selected_line_confidence:.3f}"
                    if observation.selected_line_confidence is not None
                    else "none"
                ),
                self._deduplicator.episode_id,
                observation.candidate_id,
                observation.candidate_observation_count,
                observation.required_observations,
                canonical_text,
                (
                    f"{char_similarity:.3f}"
                    if char_similarity is not None
                    else "none"
                ),
                (
                    f"{token_similarity:.3f}"
                    if token_similarity is not None
                    else "none"
                ),
                reason_code,
            )
            # Loss funnel. Every observation is counted exactly once under the
            # decision the pipeline already assigned it, so no new vocabulary is
            # invented and the rows stay traceable to the decision history.
            self._funnel["observations"] = self._funnel.get("observations", 0) + 1
            self._funnel[final_decision] = self._funnel.get(final_decision, 0) + 1
            if rejection_reason == "low_confidence":
                # One number merged two unrelated outcomes: a frame with no
                # subtitle on it (correct rejection) and a frame that held a
                # readable line and lost it anyway (a real loss). Split them on
                # the same 0.80 the weak-line rule uses as its strong reference.
                strongest = result.strongest_line_confidence
                had_strong_line = (
                    strongest is not None
                    and strongest >= OCR_STRONG_LINE_CONFIDENCE
                )
                key = (
                    "rejected_despite_strong_line"
                    if had_strong_line
                    else "rejected_no_strong_line"
                )
                self._funnel[key] = self._funnel.get(key, 0) + 1
                if had_strong_line:
                    # Record which subtitles were lost, not merely how many.
                    self._lost_strong_lines.append(
                        {
                            "observation": request_id,
                            "confidence": round(float(strongest), 4),
                            "text": str(result.strongest_line_text)[:160],
                        }
                    )
            if observation.candidate_started:
                self._funnel["candidate_started"] = (
                    self._funnel.get("candidate_started", 0) + 1
                )
            if phrase:
                self._funnel["accepted"] = self._funnel.get("accepted", 0) + 1
            self._append_ocr_decision(
                OcrDecisionObservation(
                    observation_id=request_id,
                    observed_at_monotonic=now,
                    raw_text=observation.raw_text,
                    filtered_text=result.filtered_text or result.text,
                    normalized_text=observation.filtered_text,
                    confidence=observation.confidence,
                    decision=final_decision,
                    rejection_reason=rejection_reason,
                    candidate_text=observation.candidate_text,
                    candidate_observation_count=(
                        observation.candidate_observation_count
                    ),
                    candidate_required_observations=(
                        observation.required_observations
                    ),
                    candidate_similarity=observation.candidate_similarity,
                    candidate_match_kind=observation.candidate_match_kind,
                    candidate_replaced=observation.candidate_replaced,
                    candidate_id=observation.candidate_id,
                    replaced_candidate_id=observation.replaced_candidate_id,
                    replaced_candidate_text=observation.replaced_candidate_text,
                    accepted_text=phrase or "",
                    accepted=bool(phrase),
                    roi_width=self._snapshot.ocr_roi_width,
                    roi_height=self._snapshot.ocr_roi_height,
                    backend=result.backend,
                    recognition_ms=result.recognition_ms,
                    token_count=result.token_count,
                    included_token_count=result.included_token_count,
                    line_count=result.line_count,
                    dropped_token_count=result.dropped_token_count,
                    minimum_token_confidence=result.minimum_token_confidence,
                    geometry_coherent=result.geometry_coherent,
                    clean_short_phrase_evidence=(
                        result.clean_short_phrase_evidence
                    ),
                    visual_change_decision=visual_decision,
                    filter_summary=result.filter_summary,
                    episode_id=self._deduplicator.episode_id,
                    canonical_text=canonical_text,
                    selected_line_confidence=(
                        result.selected_line_confidence
                    ),
                    quality_score=result.quality_score,
                    char_similarity=char_similarity,
                    token_similarity=token_similarity,
                    reason_code=reason_code,
                )
            )
            stabilization_dedup_ms = stabilization_ms + max(
                0.0, (self._clock() - gate_started) * 1000.0
            )
            self._snapshot = replace(
                self._snapshot,
                last_detected_text=(
                    phrase or self._snapshot.last_detected_text
                ),
                ocr_preprocessing_ms=result.preprocessing_ms,
                ocr_image_preprocessing_ms=result.image_preprocessing_ms,
                ocr_png_encoding_ms=result.png_encoding_ms,
                ocr_ms=result.elapsed_ms,
                ocr_backend=result.backend,
                ocr_recognition_ms=result.recognition_ms,
                ocr_worker_execution_ms=result.worker_execution_ms,
                ocr_worker_decode_ms=result.worker_decode_ms,
                ocr_worker_roundtrip_ms=result.worker_roundtrip_ms,
                ocr_worker_lock_wait_ms=result.worker_lock_wait_ms,
                ocr_client_overhead_ms=result.client_overhead_ms,
                ocr_worker_startup_ms=result.worker_startup_ms,
                ocr_worker_restart_count=int(
                    getattr(self.ocr, "worker_restart_count", 0)
                ),
                ocr_cli_fallback_count=int(
                    getattr(self.ocr, "cli_fallback_count", 0)
                ),
                ocr_fallback_reason=result.fallback_reason,
                ocr_debug_capture_path=result.debug_capture_path,
                stabilization_dedup_ms=stabilization_dedup_ms,
                total_capture_to_text_ms=max(
                    0.0, (self._clock() - frame_timestamp) * 1000.0
                ),
                ocr_confidence=result.confidence,
                last_raw_ocr_text=observation.raw_text,
                last_filtered_ocr_text=result.filtered_text or result.text,
                last_normalized_ocr_text=observation.filtered_text,
                last_ocr_rejection_reason=rejection_reason,
                last_ocr_observation_credible=observation.credible,
                last_ocr_gate_decision=final_decision,
                ocr_candidate_text=observation.candidate_text,
                ocr_candidate_observation_count=(
                    observation.candidate_observation_count
                ),
                ocr_candidate_required_observations=(
                    observation.required_observations
                ),
                ocr_candidate_similarity=observation.candidate_similarity,
                ocr_candidate_match_kind=observation.candidate_match_kind,
                last_accepted_ocr_text=(
                    observation.accepted_text
                    or self._snapshot.last_accepted_ocr_text
                ),
                ocr_rejection_counts=dict(self._ocr_rejection_counts),
                last_detected_at_monotonic=(
                    now if phrase else self._snapshot.last_detected_at_monotonic
                ),
                ocr_status="ready",
            )
            if phrase is None:
                if not observation.needs_confirmation:
                    self._first_visible_frame_timestamp = None
                self._emit(NarratorSessionStatus.LISTENING)
                self._finish_ocr_request()
                return
            if not self.full_narration_available(settings):
                self._first_visible_frame_timestamp = None
                self._update_ocr_decision(
                    request_id,
                    decision="accepted_tts_unavailable",
                )
                self._emit(NarratorSessionStatus.LISTENING, detected_text=phrase)
                self._finish_ocr_request()
                return
            accepted_at = self._clock()
            first_visible_frame_timestamp = (
                self._first_visible_frame_timestamp
                if self._first_visible_frame_timestamp is not None
                else frame_timestamp
            )
            self._first_visible_frame_timestamp = None
            timing = _AcceptedSubtitleTiming(
                first_visible_frame_timestamp=first_visible_frame_timestamp,
                frame_timestamp=frame_timestamp,
                accepted_at=accepted_at,
                frame_acquisition_ms=self._snapshot.frame_acquisition_ms or 0.0,
                roi_preparation_ms=self._snapshot.capture_ms or 0.0,
                ocr_preprocessing_ms=result.preprocessing_ms,
                ocr_ms=result.elapsed_ms,
                stabilization_dedup_ms=stabilization_dedup_ms,
            )
            work_id = self._begin_work(request_id)
            self._snapshot = replace(
                self._snapshot,
                last_translation="",
                translation_ms=None,
                tts_ms=None,
                tts_queue_wait_ms=None,
                tts_worker_roundtrip_ms=None,
                tts_inference_ms=None,
                tts_serialization_ms=None,
                tts_worker_startup_ms=None,
                tts_worker_reused=None,
                tts_audio_duration_ms=None,
                audio_start_ms=None,
                accepted_to_audio_start_ms=None,
                confirming_frame_to_audio_start_ms=None,
                first_visible_frame_to_audio_start_ms=None,
                total_capture_to_audio_start_ms=None,
                first_visible_frame_at_monotonic=first_visible_frame_timestamp,
                confirming_frame_at_monotonic=frame_timestamp,
                accepted_at_monotonic=accepted_at,
                tts_started_at_monotonic=None,
                tts_finished_at_monotonic=None,
                playback_started_at_monotonic=None,
                tts_status="ready",
            )
            if (
                settings.subtitle_language_mode
                is NarratorSubtitleLanguageMode.POLISH
            ):
                self._emit(NarratorSessionStatus.LISTENING, detected_text=phrase)
                self._start_tts(
                    phrase,
                    source_text=phrase,
                    generation=generation,
                    work_id=work_id,
                    timing=timing,
                    translation_status="bypassed",
                    translation_ms=None,
                    last_translation="",
                )
                self._finish_ocr_request()
                return
            self._update_ocr_decision(
                request_id,
                decision="accepted_pending_translation",
            )
            self._emit(NarratorSessionStatus.TRANSLATING, detected_text=phrase)
            self._snapshot = replace(
                self._snapshot,
                translation_status="processing",
            )
            cached = self.cache.get(
                phrase,
                provider_id=self.translator.provider_id,
                profile_id=settings.translation_profile_id,
            )
            if cached is not None:
                translation = TranslationResult(
                    source_text=phrase,
                    translated_text=cached,
                    provider_id=self.translator.provider_id,
                    cached=True,
                )
                self._translation_finished_value(
                    translation,
                    generation=generation,
                    work_id=work_id,
                    timing=timing,
                )
                self._finish_ocr_request()
                return
            translation_future = self._executor.submit(
                self.translator.translate,
                phrase,
                source_language="en",
                target_language="pl",
                profile_id=settings.translation_profile_id,
            )
            self._stage_futures.add(translation_future)
            self._stage_kinds[translation_future] = "translation"
            translation_future.add_done_callback(
                lambda completed: self._translation_finished(
                    completed,
                    generation=generation,
                    work_id=work_id,
                    timing=timing,
                )
            )
            self._finish_ocr_request()

    def _translation_finished(
        self,
        future: Future[TranslationResult],
        *,
        generation: int,
        work_id: int,
        timing: _AcceptedSubtitleTiming,
    ) -> None:
        with self._lock:
            self._stage_futures.discard(future)
            self._stage_kinds.pop(future, None)
        try:
            result = future.result()
        except Exception as error:
            with self._lock:
                if (
                    generation == self._generation
                    and work_id == self._work_id
                ):
                    self._recoverable_error(f"Translation failed: {error}")
            return
        with self._lock:
            if generation != self._generation or work_id != self._work_id:
                return
            settings = self._settings
            if settings is None:
                return
            self.cache.put(
                result.source_text,
                result.translated_text,
                provider_id=self.translator.provider_id,
                profile_id=settings.translation_profile_id,
            )
            self._translation_finished_value(
                result,
                generation=generation,
                work_id=work_id,
                timing=timing,
            )

    def _translation_finished_value(
        self,
        result: TranslationResult,
        *,
        generation: int,
        work_id: int,
        timing: _AcceptedSubtitleTiming,
    ) -> None:
        settings = self._settings
        if (
            settings is None
            or generation != self._generation
            or work_id != self._work_id
        ):
            return
        translated = normalize_subtitle(result.translated_text)
        if not translated:
            self._recoverable_error("Translation returned empty text")
            return
        self._start_tts(
            translated,
            source_text=result.source_text,
            generation=generation,
            work_id=work_id,
            timing=timing,
            translation_status="ready",
            translation_ms=result.elapsed_ms,
            last_translation=translated,
        )

    def _start_tts(
        self,
        spoken_text: str,
        *,
        source_text: str,
        generation: int,
        work_id: int,
        timing: _AcceptedSubtitleTiming,
        translation_status: str,
        translation_ms: float | None,
        last_translation: str,
    ) -> None:
        settings = self._settings
        if (
            settings is None
            or generation != self._generation
            or work_id != self._work_id
        ):
            return
        speech_text = sanitize_tts_text(spoken_text)
        if speech_text and not settings.read_speaker_names:
            speech_text = strip_speaker_label(speech_text)
        logger.debug(
            "Narrator TTS text filter phrase_id=%d removed_characters=%d",
            work_id,
            max(0, len(spoken_text) - len(speech_text)),
        )
        if not speech_text:
            self._update_ocr_decision(
                work_id, decision="accepted_tts_empty_after_filter"
            )
            self._snapshot = replace(
                self._snapshot,
                last_translation=last_translation,
                translation_ms=translation_ms,
                translation_status=translation_status,
            )
            return
        self._snapshot = replace(
            self._snapshot,
            last_translation=last_translation,
            translation_ms=translation_ms,
            translation_status=translation_status,
            tts_status="processing",
        )
        self._update_ocr_decision(
            work_id,
            decision="accepted_tts_submitted",
            tts_submitted=True,
        )
        self._funnel["tts_submitted"] = self._funnel.get("tts_submitted", 0) + 1
        queued_at = self._clock()
        self._log_stage(
            "tts_queued",
            session_id=self._session_id,
            phrase_id=work_id,
            duration_ms=max(0.0, (queued_at - timing.accepted_at) * 1000.0),
            queue_depth=self._tts_queue_depth() + 1,
            model="unknown",
        )
        tts_future = self._tts_executor.submit(
            self._synthesize_timed,
            speech_text,
            settings.voice_id,
            settings.speech_rate,
            settings.noise_scale,
            settings.noise_w_scale,
            self._session_id,
            work_id,
            queued_at,
        )
        self._stage_futures.add(tts_future)
        self._stage_kinds[tts_future] = "tts"
        self._tts_futures.add(tts_future)
        tts_future.add_done_callback(
            lambda completed: self._tts_finished(
                completed,
                translated=spoken_text,
                source_text=source_text,
                translation_bypassed=translation_status == "bypassed",
                generation=generation,
                work_id=work_id,
                timing=timing,
            )
        )

    def _synthesize_timed(
        self,
        text: str,
        voice_id: str,
        speech_rate: float,
        noise_scale: float | None = None,
        noise_w_scale: float | None = None,
        session_id: str = "",
        phrase_id: int = 0,
        queued_at: float = 0.0,
    ) -> _TtsStageResult:
        started_at = self._clock()
        self._log_stage(
            "tts_started",
            session_id=session_id,
            phrase_id=phrase_id,
            duration_ms=(
                max(0.0, (started_at - queued_at) * 1000.0)
                if queued_at
                else 0.0
            ),
            queue_depth=max(0, self._tts_queue_depth() - 1),
            model="unknown",
        )
        # Only forward the advanced overrides when the user actually set them,
        # so providers that do not accept them keep working and Piper falls back
        # to each voice's own configured values.
        advanced: dict[str, float] = {}
        if noise_scale is not None:
            advanced["noise_scale"] = noise_scale
        if noise_w_scale is not None:
            advanced["noise_w_scale"] = noise_w_scale
        audio = self.tts.synthesize(
            text,
            language="pl",
            voice_id=voice_id,
            speech_rate=speech_rate,
            **advanced,
        )
        finished_at = self._clock()
        model = "warm" if audio.worker_reused else "cold"
        self._log_stage(
            "tts_model_ready",
            session_id=session_id,
            phrase_id=phrase_id,
            duration_ms=audio.worker_startup_ms or 0.0,
            queue_depth=max(0, self._tts_queue_depth() - 1),
            model=model,
        )
        self._log_stage(
            "tts_inference_finished",
            session_id=session_id,
            phrase_id=phrase_id,
            duration_ms=(
                audio.inference_ms
                if audio.inference_ms is not None
                else max(0.0, (finished_at - started_at) * 1000.0)
            ),
            queue_depth=max(0, self._tts_queue_depth() - 1),
            model=model,
        )
        return _TtsStageResult(
            audio=audio,
            started_at=started_at,
            finished_at=finished_at,
        )

    def _tts_finished(
        self,
        future: Future[_TtsStageResult],
        *,
        translated: str,
        source_text: str,
        translation_bypassed: bool,
        generation: int,
        work_id: int,
        timing: _AcceptedSubtitleTiming,
    ) -> None:
        with self._lock:
            self._stage_futures.discard(future)
            self._stage_kinds.pop(future, None)
            self._tts_futures.discard(future)
        if future.cancelled():
            return
        try:
            stage = future.result()
        except Exception as error:
            with self._lock:
                if (
                    generation == self._generation
                    and work_id == self._work_id
                ):
                    self._recoverable_error(f"Speech synthesis failed: {error}")
            return
        with self._lock:
            settings = self._settings
            if (
                settings is None
                or generation != self._generation
                or work_id != self._work_id
            ):
                return
            audio = stage.audio
            self._snapshot = replace(
                self._snapshot,
                tts_ms=audio.elapsed_ms,
                tts_queue_wait_ms=audio.queue_wait_ms,
                tts_worker_roundtrip_ms=audio.worker_roundtrip_ms,
                tts_inference_ms=audio.inference_ms,
                tts_serialization_ms=audio.serialization_ms,
                tts_worker_startup_ms=audio.worker_startup_ms,
                tts_worker_reused=audio.worker_reused,
                tts_audio_duration_ms=audio.audio_duration_ms,
                tts_started_at_monotonic=stage.started_at,
                tts_finished_at_monotonic=stage.finished_at,
                tts_status="ready",
                audio_status="waiting",
            )
            logger.debug(
                "Narrator TTS total=%.1fms queue=%.1fms worker=%.1fms "
                "inference=%.1fms serialization=%.1fms startup=%.1fms "
                "reused=%s audio_duration=%.1fms chars=%d words=%d",
                audio.elapsed_ms,
                audio.queue_wait_ms,
                audio.worker_roundtrip_ms or 0.0,
                audio.inference_ms or 0.0,
                audio.serialization_ms or 0.0,
                audio.worker_startup_ms or 0.0,
                audio.worker_reused,
                audio.audio_duration_ms or 0.0,
                len(translated),
                len(translated.split()),
            )
            self._log_stage(
                "audio_queued",
                session_id=self._session_id,
                phrase_id=work_id,
                duration_ms=max(
                    0.0, (self._clock() - timing.accepted_at) * 1000.0
                ),
                queue_depth=self._audio_queue_depth() + 1,
                model="warm" if audio.worker_reused else "cold",
            )
            try:
                self._latest_audio_request_id = work_id
                self.audio.play(
                    audio,
                    volume=settings.volume,
                    request_id=work_id,
                    # Diagnostics only: ties this request_id to the phrase, so a
                    # re-recognised variant of the same subtitle is visible.
                    text=translated,
                    started_callback=lambda elapsed: self._audio_started(
                        elapsed,
                        source_text=source_text,
                        translated=translated,
                        translation_bypassed=translation_bypassed,
                        timing=timing,
                        generation=generation,
                        request_id=work_id,
                        model="warm" if audio.worker_reused else "cold",
                    ),
                    completed_callback=lambda: self._audio_completed(
                        generation=generation,
                        request_id=work_id,
                        model="warm" if audio.worker_reused else "cold",
                    ),
                    error_callback=lambda message: self._audio_failed(
                        message,
                        generation=generation,
                        request_id=work_id,
                    ),
                )
            except Exception as error:
                self._recoverable_error(f"Audio playback failed: {error}")

    def _audio_started(
        self,
        elapsed_ms: float,
        *,
        source_text: str,
        translated: str,
        translation_bypassed: bool,
        timing: _AcceptedSubtitleTiming,
        generation: int,
        request_id: int,
        model: str,
    ) -> None:
        with self._lock:
            if (
                generation != self._generation
                or request_id != self._latest_audio_request_id
                or self._settings is None
            ):
                return
            self._deduplicator.mark_spoken(source_text, now=self._clock())
            now = self._clock()
            self._snapshot = replace(
                self._snapshot,
                last_spoken_text=translated,
                frame_acquisition_ms=timing.frame_acquisition_ms,
                capture_ms=timing.roi_preparation_ms,
                ocr_preprocessing_ms=timing.ocr_preprocessing_ms,
                ocr_ms=timing.ocr_ms,
                stabilization_dedup_ms=timing.stabilization_dedup_ms,
                audio_start_ms=elapsed_ms,
                accepted_to_audio_start_ms=max(
                    0.0, (now - timing.accepted_at) * 1000.0
                ),
                confirming_frame_to_audio_start_ms=max(
                    0.0, (now - timing.frame_timestamp) * 1000.0
                ),
                first_visible_frame_to_audio_start_ms=max(
                    0.0,
                    (now - timing.first_visible_frame_timestamp) * 1000.0,
                ),
                total_capture_to_audio_start_ms=max(
                    0.0, (now - timing.frame_timestamp) * 1000.0
                ),
                playback_started_at_monotonic=now,
                audio_status="speaking",
            )
            self._log_stage(
                "audio_started",
                session_id=self._session_id,
                phrase_id=request_id,
                duration_ms=elapsed_ms,
                queue_depth=self._audio_queue_depth(),
                model=model,
            )
            logger.debug(
                "Narrator latency accepted=%r frame_acquisition=%.1fms "
                "roi=%.1fms ocr_preprocess=%.1fms ocr=%.1fms "
                "stabilization_dedup=%.1fms translation=%s tts=%s "
                "audio_queue=%.1fms accepted_to_playback=%.1fms "
                "confirming_frame_to_playback=%.1fms "
                "first_visible_frame_to_playback=%.1fms",
                source_text,
                self._snapshot.frame_acquisition_ms or 0.0,
                self._snapshot.capture_ms or 0.0,
                self._snapshot.ocr_preprocessing_ms or 0.0,
                self._snapshot.ocr_ms or 0.0,
                self._snapshot.stabilization_dedup_ms or 0.0,
                (
                    f"{self._snapshot.translation_ms:.1f}ms"
                    if self._snapshot.translation_ms is not None
                    else "bypassed"
                ),
                (
                    f"{self._snapshot.tts_ms:.1f}ms"
                    if self._snapshot.tts_ms is not None
                    else "unknown"
                ),
                elapsed_ms,
                self._snapshot.accepted_to_audio_start_ms or 0.0,
                self._snapshot.confirming_frame_to_audio_start_ms or 0.0,
                self._snapshot.first_visible_frame_to_audio_start_ms or 0.0,
            )
            self._emit(
                NarratorSessionStatus.SPEAKING,
                translated_text="" if translation_bypassed else translated,
                spoken_text=translated,
            )

    def _audio_completed(
        self, *, generation: int, request_id: int, model: str
    ) -> None:
        with self._lock:
            if (
                generation != self._generation
                or request_id != self._latest_audio_request_id
                or self._settings is None
            ):
                return
            now = self._clock()
            playback_started = self._snapshot.playback_started_at_monotonic
            self._snapshot = replace(self._snapshot, audio_status="ready")
            self._funnel["played_to_completion"] = (
                self._funnel.get("played_to_completion", 0) + 1
            )
            self._log_stage(
                "audio_finished",
                session_id=self._session_id,
                phrase_id=request_id,
                duration_ms=(
                    max(0.0, (now - playback_started) * 1000.0)
                    if playback_started is not None
                    else 0.0
                ),
                queue_depth=self._audio_queue_depth(),
                model=model,
            )
            if not self._request_active:
                self._emit(NarratorSessionStatus.LISTENING)

    def _audio_failed(
        self, message: str, *, generation: int, request_id: int
    ) -> None:
        with self._lock:
            if (
                generation != self._generation
                or request_id != self._latest_audio_request_id
                or self._settings is None
            ):
                return
            self._snapshot = replace(self._snapshot, audio_status="error")
            self._recoverable_error(message)

    def _begin_work(self, accepted_request_id: int) -> int:
        """Supersede only downstream work for a newly accepted subtitle.

        Raw OCR observations never reach this method, so background noise cannot
        cancel a valid narration job. Futures which have not started are removed;
        an already-running provider call is allowed to finish and its result is
        ignored. This preserves persistent model workers and never stops audio
        which has already reached the output device.
        """

        previous_work_id = self._work_id
        work_id = accepted_request_id
        stale_futures = tuple(self._stage_futures)
        self._stage_futures.clear()
        cancelled = {"translation": 0, "tts": 0}
        running = {"translation": 0, "tts": 0}
        for future in stale_futures:
            kind = self._stage_kinds.pop(future, "translation")
            if future.cancel():
                cancelled[kind] += 1
            else:
                running[kind] += 1
        if any(cancelled.values()) or any(running.values()):
            if previous_work_id:
                self._update_ocr_decision(
                    previous_work_id,
                    decision="accepted_but_tts_stale",
                )
            self._snapshot = replace(
                self._snapshot,
                stale_queued_translation=(
                    self._snapshot.stale_queued_translation
                    + cancelled["translation"]
                ),
                stale_running_translation_results=(
                    self._snapshot.stale_running_translation_results
                    + running["translation"]
                ),
                stale_queued_tts=(
                    self._snapshot.stale_queued_tts + cancelled["tts"]
                ),
                stale_running_tts_results=(
                    self._snapshot.stale_running_tts_results + running["tts"]
                ),
            )
            logger.debug(
                "Narrator accepted newer subtitle; translation queued=%d "
                "running=%d; TTS queued=%d running=%d",
                cancelled["translation"],
                running["translation"],
                cancelled["tts"],
                running["tts"],
            )
        self._work_id = accepted_request_id
        return work_id

    def _append_ocr_decision(self, observation: OcrDecisionObservation) -> None:
        history = (*self._snapshot.ocr_decision_history, observation)
        self._snapshot = replace(
            self._snapshot,
            ocr_decision_history=history[-OCR_DECISION_HISTORY_LIMIT:],
        )

    def _update_ocr_decision(
        self,
        observation_id: int,
        **changes: object,
    ) -> None:
        history = list(self._snapshot.ocr_decision_history)
        for index in range(len(history) - 1, -1, -1):
            if history[index].observation_id != observation_id:
                continue
            history[index] = replace(history[index], **changes)
            latest = bool(history and history[-1].observation_id == observation_id)
            self._snapshot = replace(
                self._snapshot,
                ocr_decision_history=tuple(history),
                last_ocr_gate_decision=(
                    str(changes["decision"])
                    if latest and "decision" in changes
                    else self._snapshot.last_ocr_gate_decision
                ),
            )
            return

    def _finish_ocr_request(self) -> None:
        self._request_active = False
        if self._pending_frame is None:
            if (
                self._snapshot.status is NarratorSessionStatus.SPEAKING
                and self._snapshot.audio_status == "ready"
            ):
                self._emit(NarratorSessionStatus.LISTENING)
            return
        frame = self._pending_frame
        stabilization_ms = self._pending_frame_stabilization_ms
        visual_decision = self._pending_frame_visual_decision
        self._pending_frame = None
        self._pending_frame_stabilization_ms = 0.0
        self._pending_frame_visual_decision = ""
        self._start_ocr(
            frame,
            stabilization_ms=stabilization_ms,
            visual_decision=visual_decision,
        )

    def _capture_state_changed(self, state: CaptureState, message: str) -> None:
        with self._lock:
            if self._settings is None:
                return
            self._snapshot = replace(self._snapshot, capture_state=state.value)
            mapped = {
                CaptureState.PERMISSION_REQUIRED: NarratorSessionStatus.SELECTING_SOURCE,
                CaptureState.SELECTING_SOURCE: NarratorSessionStatus.SELECTING_SOURCE,
                CaptureState.STARTING: NarratorSessionStatus.STARTING,
                CaptureState.ACTIVE: NarratorSessionStatus.LISTENING,
            }.get(state)
            if mapped is not None:
                self._emit(mapped, message=message)
                return
            if state in {CaptureState.CANCELLED, CaptureState.PERMISSION_DENIED}:
                self._fail(message or "Screen capture permission was cancelled")
            elif state in {
                CaptureState.UNAVAILABLE,
                CaptureState.SOURCE_LOST,
                CaptureState.ERROR,
            }:
                self._fail(message or "Screen capture stopped")

    def _emit(
        self,
        status: NarratorSessionStatus,
        message: str = "",
        *,
        detected_text: str = "",
        translated_text: str = "",
        spoken_text: str = "",
    ) -> None:
        self._snapshot = replace(
            self._snapshot,
            status=status,
            message=message,
            last_detected_text=(
                detected_text or self._snapshot.last_detected_text
            ),
            last_translation=(
                translated_text or self._snapshot.last_translation
            ),
            last_spoken_text=spoken_text or self._snapshot.last_spoken_text,
            audio_supersessions=max(
                0,
                int(getattr(self.audio, "superseded_count", 0))
                - self._audio_supersession_baseline,
            ),
            # Read defensively: capture backends other than the GStreamer
            # transport do not expose these counters.
            capture_format=str(
                getattr(self.capture, "negotiated_variant", "") or ""
            ),
            capture_dmabuf=bool(getattr(self.capture, "dmabuf_supported", False)),
            capture_variants_tried=",".join(
                getattr(self.capture, "tried_variants", ()) or ()
            ),
            capture_variants_failed=",".join(
                getattr(self.capture, "failed_variants", ()) or ()
            ),
            narration_funnel=dict(self._funnel),
            lost_strong_lines=tuple(dict(entry) for entry in self._lost_strong_lines),
            playback_completed=int(getattr(self.audio, "completed_count", 0)),
            playback_interrupted=int(getattr(self.audio, "interrupted_count", 0)),
            playback_last_result=str(
                getattr(getattr(self.audio, "last_playback", None), "result", "")
                or ""
            ),
            playback_expected_ms=self._playback_ms("expected_seconds"),
            playback_actual_ms=self._playback_ms("processed_seconds"),
            capture_frames_received=int(
                getattr(self.capture, "frames_received", 0)
            ),
            capture_stream_errors=int(getattr(self.capture, "stream_errors", 0)),
            capture_restarts=int(getattr(self.capture, "restarts", 0)),
        )
        timings = {
            name: value
            for name, value in {
                "frameAcquisition": self._snapshot.frame_acquisition_ms,
                "capture": self._snapshot.capture_ms,
                "ocrPreprocessing": self._snapshot.ocr_preprocessing_ms,
                "ocr": self._snapshot.ocr_ms,
                "stabilizationDedup": self._snapshot.stabilization_dedup_ms,
                "translation": self._snapshot.translation_ms,
                "tts": self._snapshot.tts_ms,
                "audioStart": self._snapshot.audio_start_ms,
                "acceptedToAudioStart": (
                    self._snapshot.accepted_to_audio_start_ms
                ),
                "confirmingFrameToAudioStart": (
                    self._snapshot.confirming_frame_to_audio_start_ms
                ),
                "firstVisibleFrameToAudioStart": (
                    self._snapshot.first_visible_frame_to_audio_start_ms
                ),
                "captureToAudioStart": (
                    self._snapshot.total_capture_to_audio_start_ms
                ),
                "firstVisibleFrameAt": (
                    self._snapshot.first_visible_frame_at_monotonic
                ),
                "confirmingFrameAt": (
                    self._snapshot.confirming_frame_at_monotonic
                ),
                "acceptedAt": self._snapshot.accepted_at_monotonic,
                "ttsStartedAt": self._snapshot.tts_started_at_monotonic,
                "ttsFinishedAt": self._snapshot.tts_finished_at_monotonic,
                "playbackStartedAt": (
                    self._snapshot.playback_started_at_monotonic
                ),
            }.items()
            if value is not None
        }
        self._events.put(
            NarratorEvent(
                session_id=self._session_id,
                game_key=self._snapshot.game_key,
                generation=self._generation,
                status=status,
                message=message,
                detected_text=detected_text,
                translated_text=translated_text,
                spoken_text=spoken_text,
                timings=timings,
            )
        )

    def _recoverable_error(self, message: str) -> None:
        logger.warning(
            "Narrator recoverable failure session=%s game=%s: %s",
            self._session_id,
            self._snapshot.game_key,
            message,
        )
        if message.startswith("OCR failed"):
            self._snapshot = replace(self._snapshot, ocr_status="error")
        elif message.startswith("Translation"):
            self._snapshot = replace(self._snapshot, translation_status="error")
        elif message.startswith("Speech synthesis"):
            self._snapshot = replace(self._snapshot, tts_status="error")
        elif message.startswith("Audio") or message.startswith("Narrator audio"):
            self._snapshot = replace(self._snapshot, audio_status="error")
        self._emit(NarratorSessionStatus.LISTENING, message=message)

    def _fail(self, message: str) -> None:
        self._generation += 1
        self._request_id += 1
        self._work_id += 1
        self._latest_audio_request_id = 0
        if self._ocr_future is not None:
            self._ocr_future.cancel()
        if self._ocr_prepare_future is not None:
            self._ocr_prepare_future.cancel()
            self._ocr_prepare_future = None
        if self._tts_prepare_future is not None:
            self._tts_prepare_future.cancel()
            self._tts_prepare_future = None
        self._tts_prepare_started_at = None
        for future in tuple(self._stage_futures):
            future.cancel()
        self._stage_futures.clear()
        self._stage_kinds.clear()
        self._cancel_provider_work()
        self._snapshot = replace(
            self._snapshot,
            status=NarratorSessionStatus.ERROR,
            message=message,
            generation=self._generation,
        )
        self._events.put(
            NarratorEvent(
                session_id=self._session_id,
                game_key=self._snapshot.game_key,
                generation=self._generation,
                status=NarratorSessionStatus.ERROR,
                message=message,
            )
        )
        self.capture.stop()
        self.audio.stop()
        self._request_active = False
        self._pending_frame = None
        self._pending_frame_stabilization_ms = 0.0
        self._pending_frame_visual_decision = ""
        self._first_visible_frame_timestamp = None
        self._settings = None

    def _playback_ms(self, attribute: str) -> float | None:
        """Read one duration off the last playback record, in milliseconds."""

        record = getattr(self.audio, "last_playback", None)
        value = getattr(record, attribute, None) if record is not None else None
        if value is None:
            return None
        try:
            return max(0.0, float(value) * 1000.0)
        except (TypeError, ValueError):
            return None

    def _cancel_provider_work(self) -> None:
        for provider in (self.ocr, self.translator, self.tts):
            cancel = getattr(provider, "cancel", None)
            if callable(cancel):
                cancel()


__all__ = [
    "GameActivityProvider",
    "NarratorAudioOutput",
    "NarratorPipeline",
    "OcrProvider",
    "PhraseDeduplicator",
    "OcrGateObservation",
    "SubtitleRegionStabilizer",
    "SubtitleTextGate",
    "SubtitleSource",
    "TranslationProvider",
    "TtsProvider",
    "UnavailableAudioOutput",
    "UnavailableOcrProvider",
    "UnavailableTranslationProvider",
    "UnavailableTtsProvider",
    "crop_frame",
    "normalize_subtitle",
    "subtitle_identity",
]
