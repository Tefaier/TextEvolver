import html
import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from difflib import SequenceMatcher

in_num_words = ["of", '', 'to', 'or', 'so']
possible_mutations = ['s', "'", "'s", 'es', 'ов', 'ы', 'а', '’s']
digit_len_before = 7
MEANINGFULL_CHARACTER_PATTERN = re.compile(r"[^\W_]")
EMPTY_STRING_PATTERN = re.compile(r"\s*")
FEET_SEPARATOR_PATTERN = re.compile(r"['’]")
FEET_PATTERN = re.compile(rf"\d+{FEET_SEPARATOR_PATTERN.pattern}\d+")
ERASE_WITH_PERIOD_SEPARATOR_PATTERN = re.compile(r"[^\w.'’]|_|(?<!\d)\.|\.(?!\d)")
ERASE_WITH_COMMA_SEPARATOR_PATTERN = re.compile(r"[^\w,'’]|_|(?<!\d),|,(?!\d)")
MULTIPLE_SPACES_PATTERN = re.compile(r" {2,}")


@dataclass(frozen=True, slots=True)
class ReplaceRules:
    replace_with: str
    lookup_length: int
    mutations: bool = False
    units: float | None = None
    is_feet: bool = False
    can_be_word: bool | None = None

    def __post_init__(self) -> None:
        if self.lookup_length < 1:
            raise ValueError("Replacement lookup length must be positive")
        if self.is_feet and self.units is None:
            raise ValueError("Feet replacement rules require a unit conversion")


def string_with_meaning(text: str):
    return MEANINGFULL_CHARACTER_PATTERN.search(text) is not None


def string_empty(text: str) -> bool:
    return EMPTY_STRING_PATTERN.fullmatch(text) is not None


def is_float(string: str):
    if string is None:
        return False
    try:
        float(string)
        return True
    except ValueError:
        return False


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


def _text2int(textnum, numwords={}, interimwords={}, parse_feet: bool = False):
    units = [
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
        "sixteen", "seventeen", "eighteen", "nineteen",
    ]

    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

    scales = ["hundred", "thousand", "million", "billion", "trillion"]

    decimals = ["half", "quarter", "quarters"]

    interimwords["and"] = (1, 0)
    interimwords["an"] = (1, 0)
    interimwords["a"] = (1, 0)

    if not numwords:
        numwords["half"] = (0.5, 0)
        numwords["quarter"] = (0.25, 0)
        numwords["quarters"] = (0.25, 0)
        numwords["dozen"] = (12, 0)
        for idx, word in enumerate(units):
            numwords[word] = (1, idx)
        for idx, word in enumerate(tens):
            numwords[word] = (1, idx * 10)
        for idx, word in enumerate(scales):
            numwords[word] = (10 ** (idx * 3 or 2), 0)

    numbers_res = textnum.split()
    digit_start = 0
    digit_length = 0
    current = result = 0
    was_tens = False
    was_units = False
    was_decimal = False
    new_digit = False

    for word in textnum.split():
        new_digit = (
            (parse_feet and is_feet(word))
            or is_float(word)
            or (was_tens and word in tens)
            or (was_units and word in tens + units)
            or was_decimal
        )
        interim_ignore = (digit_length == 0 and word in interimwords)
        if (word not in numwords and word not in interimwords) or new_digit or interim_ignore: # apply changes and start new digit
            if digit_length > 0:
                numbers_res = numbers_res[:digit_start] + [str(result + current)] + [''] * (
                            digit_length - 1) + numbers_res[digit_start + digit_length:]
            digit_start += digit_length if new_digit else digit_length + 1
            digit_length = 0
            current = result = 0
            was_tens = False
            was_units = False
            was_decimal = False
        if is_float(word): # number in words format
            digit_length += 1
            current += float(word)
        elif parse_feet and is_feet(word): # feet'inches
            parts = FEET_SEPARATOR_PATTERN.split(word)
            digit_length += 1
            current += float(float(parts[0]) + float(parts[1]) * 0.0833)
        elif word in numwords or (digit_length > 0 and word in interimwords): # number is to be continued and is to be used
            digit_length += 1
            scale, increment = numwords.get(word, interimwords.get(word))
            if word in units:
                was_tens = False
                was_units = True
            elif word in tens:
                was_tens = True
                was_units = False
            elif word in decimals:
                was_tens = False
                was_units = False
                was_decimal = True
            else:
                was_tens = False
                was_units = False
            if current == 0 and increment == 0 and word not in interimwords:
                current = 1 * scale
            else:
                current = current * scale + increment
            if scale > 100 or word in interimwords:
                result += current
                current = 0

    if digit_length > 0:
        numbers_res = numbers_res[:digit_start] + [str(result + current)] + [''] * (digit_length - 1) + numbers_res[digit_start + digit_length:]
    return numbers_res


def _convert_unit_value(value: str, conversion: float) -> str:
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
        digit_version = _text2int(
            " ".join([x[1] for x in replace_map_part[digit_check_start:start_location]]),
            parse_feet=replace_rules.is_feet,
        )
        for i in range(digit_check_start, start_location):
            replace_map_part[i][1] = digit_version[i - digit_check_start]
        digit_found = False
        for index in range(start_location - 1, digit_check_start-1, -1):
            if is_float(replace_map_part[index][1]):
                digit_found = True
                replace_map_part[index][1] = _convert_unit_value(
                    replace_map_part[index][1],
                    replace_rules.units,
                )
            elif replace_map_part[index][1] in in_num_words:  # 'or' and 'so' added for special cases
                pass
            elif not digit_found and replace_rules.can_be_word:
                # if can be just word then do not replace at all and treat it as not a unit conversion at all
                return None
            elif not digit_found:
                # implicit 1 of unit
                replace_map_part[start_location][1] = str(replace_rules.units) + (
                    (" " + replace_map_part[start_location][1]) if replace_map_part[start_location][1] != '' else '')
                break
            else:
                break
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
    lookup_pattern = re.compile(to_find, flags=re.IGNORECASE)

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
            decimal_feet = _text2int(source, parse_feet=True)[0]
            converted_value = _convert_unit_value(decimal_feet, replace_rules.units)
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
