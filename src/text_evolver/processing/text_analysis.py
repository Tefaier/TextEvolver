import html
import re
import unicodedata
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from types import MappingProxyType

in_num_words = frozenset({"of", '', 'to', 'or', 'so'})
possible_mutations = ['s', "'", "'s", 'es', 'ов', 'ы', 'а', '’s']
digit_len_before = 7
MEANINGFULL_CHARACTER_PATTERN = re.compile(r"[^\W_]")
EMPTY_STRING_PATTERN = re.compile(r"\s*")
FEET_SEPARATOR_PATTERN = re.compile(r"['’]")
FEET_PATTERN = re.compile(
    rf"(?P<feet>\d+){FEET_SEPARATOR_PATTERN.pattern}(?P<inches>\d+)"
)
ERASE_WITH_PERIOD_SEPARATOR_PATTERN = re.compile(r"[^\w.'’]|_|(?<!\d)\.|\.(?!\d)")
ERASE_WITH_COMMA_SEPARATOR_PATTERN = re.compile(r"[^\w,'’]|_|(?<!\d),|,(?!\d)")
MULTIPLE_SPACES_PATTERN = re.compile(r" {2,}")

NUMBER_UNIT_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen",
)
NUMBER_TENS = ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
NUMBER_SCALES = ("hundred", "thousand", "million", "billion", "trillion")
NUMBER_DECIMALS = frozenset({"half", "quarter", "quarters"})
IMPLICIT_ONE_WORDS = frozenset({"dozen", *NUMBER_DECIMALS, *NUMBER_SCALES})
NUMBER_UNIT_WORD_SET = frozenset(NUMBER_UNIT_WORDS)
NUMBER_TENS_SET = frozenset(NUMBER_TENS)
NUMBER_WORDS = MappingProxyType(
    {
        "half": (0.5, 0),
        "quarter": (0.25, 0),
        "quarters": (0.25, 0),
        "dozen": (12, 0),
        **{word: (1, value) for value, word in enumerate(NUMBER_UNIT_WORDS)},
        **{word: (1, value * 10) for value, word in enumerate(NUMBER_TENS, start=2)},
        **{word: (10 ** (index * 3 or 2), 0) for index, word in enumerate(NUMBER_SCALES)},
    }
)
INTERIM_NUMBER_WORDS = MappingProxyType({"and": (1, 0), "an": (1, 0), "a": (1, 0)})


@dataclass(frozen=True, slots=True)
class ReplaceRules:
    replace_with: str
    lookup_length: int
    mutations: bool = False
    regex: bool = False
    units: float | None = None
    is_feet: bool = False
    can_be_word: bool | None = None

    def __post_init__(self) -> None:
        if self.lookup_length < 1:
            raise ValueError("Replacement lookup length must be positive")
        if self.is_feet and self.units is None:
            raise ValueError("Feet replacement rules require a unit conversion")


@dataclass(frozen=True, slots=True)
class NumberSpan:
    start: int
    end: int
    value: int | float


def string_with_meaning(text: str):
    return MEANINGFULL_CHARACTER_PATTERN.search(text) is not None


def string_empty(text: str) -> bool:
    return EMPTY_STRING_PATTERN.fullmatch(text) is not None


def _parse_float(string: str | None) -> float | None:
    if string is None:
        return None
    try:
        return float(string)
    except ValueError:
        return None


def is_float(string: str | None) -> bool:
    return _parse_float(string) is not None


def is_feet(string: str | None) -> bool:
    return string is not None and FEET_PATTERN.fullmatch(string) is not None


def convert_utf8_symbols(text: str):
    return unicodedata.normalize("NFKC", html.unescape(text))


def redistribute_transformed_text(original_parts: list[str], transformed_text: str) -> list[str]:
    """Map transformed block text back to native parts while retaining unchanged part ownership."""
    if not original_parts:
        raise ValueError("A document block must contain at least one native part")
    original_text = "".join(original_parts)
    if transformed_text == original_text:
        return original_parts.copy()

    boundaries = [0]
    for value in original_parts:
        boundaries.append(boundaries[-1] + len(value))
    redistributed: list[list[str]] = [[] for _ in original_parts]

    def part_index(offset: int) -> int:
        return min(max(bisect_right(boundaries, offset) - 1, 0), len(original_parts) - 1)

    for operation, original_start, original_end, transformed_start, transformed_end in SequenceMatcher(
        None,
        original_text,
        transformed_text,
        autojunk=False,
    ).get_opcodes():
        if operation == "equal":
            original_offset = original_start
            transformed_offset = transformed_start
            while original_offset < original_end:
                index = part_index(original_offset)
                current_piece_end = min(original_end, boundaries[index + 1])
                length = current_piece_end - original_offset
                if length <= 0:
                    index += 1
                    if index >= len(original_parts):
                        break
                    current_piece_end = min(original_end, boundaries[index + 1])
                    length = current_piece_end - original_offset
                redistributed[index].append(transformed_text[transformed_offset : transformed_offset + length])
                original_offset = current_piece_end
                transformed_offset += length
        elif operation in {"replace", "insert"}:
            redistributed[part_index(original_start)].append(transformed_text[transformed_start:transformed_end])
    return ["".join(values) for values in redistributed]


def _find_last_literal_match(value: str, text: str) -> re.Match[str] | None:
    if not value:
        raise ValueError("Replacement source cannot be empty")

    last_match = None
    for match in re.finditer(re.escape(value), text, flags=re.IGNORECASE):
        last_match = match
    return last_match


def replace_iteration(replace_map: list[list[str]], words: list[str]) -> list[str]:
    """
    Apply cleaned-word replacements while retaining native punctuation.
    replace_map: replacement rules from find_in_clean
    words: words from source text (not cleaned, symbols must be preserved)
    """
    if not replace_map:
        return words
    if not words:
        raise ValueError("Cannot apply replacements to an empty word list")

    word_index = len(words) - 1
    remaining_word = words[word_index]
    transformed_suffix = ""

    for source, replacement in reversed(replace_map):
        match = _find_last_literal_match(source, remaining_word)
        while match is None:
            words[word_index] = remaining_word + transformed_suffix
            word_index -= 1
            if word_index < 0:
                raise ValueError(f"Replacement source {source!r} was not found in the original words")
            remaining_word = words[word_index]
            transformed_suffix = ""
            match = _find_last_literal_match(source, remaining_word)

        transformed_suffix = replacement + remaining_word[match.end() :] + transformed_suffix
        remaining_word = remaining_word[: match.start()]

    words[word_index] = remaining_word + transformed_suffix
    return words


def _parse_number_words(words: Sequence[str], start: int) -> NumberSpan | None:
    total: int | float = 0
    group: int | float = 0
    index = start
    consumed_number = False
    after_and = False
    last_kind: str | None = None

    while index < len(words):
        word = words[index]
        if word == "and":
            next_index = index + 1
            article_found = False
            while next_index < len(words) and words[next_index] in {"a", "an"}:
                article_found = True
                next_index += 1
            valid_continuation = (
                next_index < len(words)
                and words[next_index] in NUMBER_WORDS
                and (not article_found or words[next_index] in IMPLICIT_ONE_WORDS)
            )
            if not consumed_number or not valid_continuation:
                # invalid continuation, just finish
                break
            after_and = True
            index += 1
            continue

        if word in {"a", "an"}:
            if after_and:
                index += 1
                continue
            if (
                consumed_number
                or index + 1 >= len(words)
                or words[index + 1] not in IMPLICIT_ONE_WORDS
            ):
                # abnormal "a"
                break
            # implicit 1
            group = 1
            consumed_number = True
            last_kind = "unit"
            index += 1
            continue

        if word in NUMBER_UNIT_WORD_SET:
            value = NUMBER_WORDS[word][1]
            if not after_and and (
                last_kind == "unit"
                or (last_kind == "tens" and value > 9)
                or last_kind == "fraction"
            ):
                break
            group += value
            consumed_number = True
            after_and = False
            last_kind = "unit"
        elif word in NUMBER_TENS_SET:
            if not after_and and last_kind in {"unit", "tens", "fraction"}:
                break
            group += NUMBER_WORDS[word][1]
            consumed_number = True
            after_and = False
            last_kind = "tens"
        elif word == "hundred":
            if last_kind in {"hundred", "fraction"}:
                break
            group = (group or 1) * 100
            consumed_number = True
            after_and = False
            last_kind = "hundred"
        elif word == "dozen":
            if last_kind in {"dozen", "fraction"}:
                break
            group = (group or 1) * 12
            consumed_number = True
            after_and = False
            last_kind = "dozen"
        elif word in NUMBER_DECIMALS:
            fraction = NUMBER_WORDS[word][0]
            group = group + fraction if after_and else (group or 1) * fraction
            consumed_number = True
            after_and = False
            last_kind = "fraction"
        elif word in NUMBER_SCALES[1:]:
            scale = NUMBER_WORDS[word][0]
            total += (group or 1) * scale
            group = 0
            consumed_number = True
            after_and = False
            last_kind = "scale"
        else:
            break
        index += 1

    if not consumed_number:
        return None
    return NumberSpan(start, index, total + group)


def _text2int(words: Sequence[str], parse_feet: bool = False) -> NumberSpan | None:
    """Return only the last numeric group found in an existing token sequence."""
    last_span: NumberSpan | None = None
    index = 0
    while index < len(words):
        word = words[index]
        feet_match = FEET_PATTERN.fullmatch(word) if parse_feet else None
        if feet_match is not None:
            last_span = NumberSpan(
                index,
                index + 1,
                float(feet_match.group("feet")) + float(feet_match.group("inches")) / 12,
            )
            index += 1
            continue

        float_value = _parse_float(word)
        if float_value is not None:
            last_span = NumberSpan(index, index + 1, float_value)
            index += 1
            continue

        number_span = _parse_number_words(words, index)
        if number_span is None:
            index += 1
            continue
        last_span = number_span
        index = number_span.end
    return last_span


def _convert_unit_value(value: int | float, conversion: float) -> str:
    return str(round(float(value) * conversion, 1)).replace('.0', '')


def _text_modifier(
    start_location: int,
    words_num: int,
    replace_map_part: list[list[str]],
    replace_rules: ReplaceRules,
) -> list[list[str]] | None:
    '''
    Builds replacement and improves it by preserving case
    Returns None if unit convertation was expected but failed to find digit in any form
    '''
    # replacer is list with words to replace with on words_num locations
    replacer = replace_rules.replace_with.split(' ')
    replacer = ['' if i >= len(replacer) else (' '.join(replacer[i:]) if (i == words_num - 1) else replacer[i]) for
                i in range(0, words_num)]
    # inherit case from origin and apply replacer to replace_map_part
    for index in range(start_location, start_location + words_num):
        if replace_map_part[index][1][0].islower():
            replacer[index - start_location] = replacer[index - start_location][0].lower() + replacer[index - start_location][1:]
        else:
            replacer[index - start_location] = replacer[index - start_location][0].upper() + replacer[index - start_location][1:]
        replace_map_part[index][1] = replacer[index - start_location]
    del replacer

    # go to past words and alter based on unit convertation
    if replace_rules.units is not None:
        digit_check_start = max(0, start_location - digit_len_before)
        digit_words = [entry[1] for entry in replace_map_part[digit_check_start:start_location]]
        digit_span = _text2int(
            digit_words,
            parse_feet=replace_rules.is_feet,
        )
        usable_digit = digit_span is not None and all(
            digit_words[index] in in_num_words
            for index in range(digit_span.end, len(digit_words))
        )
        if usable_digit:
            # replace value and erase all other number parts
            span_start = digit_check_start + digit_span.start
            span_end = digit_check_start + digit_span.end
            replace_map_part[span_start][1] = _convert_unit_value(
                digit_span.value,
                replace_rules.units,
            )
            for index in range(span_start + 1, span_end):
                replace_map_part[index][1] = ''
        elif replace_rules.can_be_word:
            # if can be just word then do not replace at all and treat it as not a unit conversion at all
            return None
        else:
            # implicit 1 of unit
            replace_map_part[start_location][1] = str(replace_rules.units) + (
                (" " + replace_map_part[start_location][1]) if replace_map_part[start_location][1] != '' else '')
    return replace_map_part


def find_in_clean(
    clean_words: list,
    to_find: str,
    replace_rules: ReplaceRules,
):
    words_num = replace_rules.lookup_length
    if clean_words == [''] or len(clean_words) < words_num:
        return {"found": False}
    results = {"found": False, "replace_map": [[x, x] for x in clean_words]}
    clean_text = ' '.join(clean_words)
    word_starts: list[int] = []
    next_word_start = 0
    for word in clean_words:
        word_starts.append(next_word_start)
        next_word_start += len(word) + 1
    lookup_pattern = re.compile(
        to_find if replace_rules.regex else re.escape(to_find),
        flags=re.IGNORECASE,
    )

    i = 0
    while i < len(clean_words) + 1 - words_num:
        # match in part of string using precomputed offsets
        last_word_index = i + words_num - 1
        window_end = word_starts[last_word_index] + len(clean_words[last_word_index])
        match = lookup_pattern.match(clean_text, word_starts[i], window_end)
        match_end = match.end() if match is not None else -1
        if match is not None and (
            match_end == window_end or (
                replace_rules.mutations and any(
                    match_end + len(mutation) == window_end and clean_text.startswith(mutation, match_end, window_end)
                    for mutation in possible_mutations
                )
            )
        ):
            part_start = max(i - digit_len_before, 0) if replace_rules.units is not None else i
            part_end = i + words_num
            replace_map_part = [
                entry.copy() for entry in results["replace_map"][part_start:part_end]
            ]
            modified_part = _text_modifier(
                i - part_start,
                words_num,
                replace_map_part,
                replace_rules,
            )
            if modified_part is not None:
                results["found"] = True
                results["replace_map"][part_start:part_end] = modified_part
                i += words_num - 1
        i += 1

    # afterwards go through just feet digits and convert them
    if replace_rules.is_feet:
        for source_and_replacement in results["replace_map"]:
            source, replacement = source_and_replacement
            if source != replacement or not is_feet(source):
                continue
            feet_span = _text2int([source], parse_feet=True)
            if feet_span is None:
                continue
            converted_value = _convert_unit_value(feet_span.value, replace_rules.units)
            source_and_replacement[1] = f"{converted_value} {replace_rules.replace_with}".strip()
            results["found"] = True
    return results


def text_cleaner(string: str, settings: dict) -> str:
    erase_pattern = (
        ERASE_WITH_COMMA_SEPARATOR_PATTERN
        if settings["coma in digits"]
        else ERASE_WITH_PERIOD_SEPARATOR_PATTERN
    )
    cleaned = erase_pattern.sub(" ", string)
    return MULTIPLE_SPACES_PATTERN.sub(" ", cleaned).strip(" ")
