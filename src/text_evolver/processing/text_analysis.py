import copy
import html
import re
import unicodedata
from bisect import bisect_right
from difflib import SequenceMatcher

in_num_words = ["of", '', 'to', 'or', 'so']
possible_mutations = ['s', "'", "'s", 'es', 'ов', 'ы', 'а', '’s']
digit_len_before = 7
MEANINGFULL_CHARACTER_PATTERN = re.compile(r"[^\W_]")
EMPTY_STRING_PATTERN = re.compile(r"\s*")
FEET_PATTERN = re.compile(r"\d+['’]\d+")
ERASE_WITH_PERIOD_SEPARATOR_PATTERN = re.compile(r"[^\w.'’]|_|(?<!\d)\.|\.(?!\d)")
ERASE_WITH_COMMA_SEPARATOR_PATTERN = re.compile(r"[^\w,'’]|_|(?<!\d),|,(?!\d)")
MULTIPLE_SPACES_PATTERN = re.compile(r" {2,}")


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


def _text2int(textnum, numwords={}, interimwords={}):
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
        new_digit = is_feet(word) or is_float(word) or (was_tens and word in tens) or (was_units and word in tens + units) or was_decimal
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
        elif is_feet(word): # feet'inches
            parts = word.split("'")
            if len(parts)!=2:
                parts = word.split("’")
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


def _text_modifier(start_location: int, words_num: int, replace_map: list, replace_rules: dict):
    replace_map_copy = copy.deepcopy(replace_map)
    replacer = replace_rules["replace_with"].split(' ')
    replacer = ['' if i >= len(replacer) else (' '.join(replacer[i:]) if (i == words_num - 1) else replacer[i]) for
                i in range(0, words_num)]
    for index in range(start_location, start_location + words_num):
        if replace_map_copy[index][1][0].islower():
            replacer[index - start_location] = replacer[index - start_location][0].lower() + replacer[index - start_location][1:]
        else:
            replacer[index - start_location] = replacer[index - start_location][0].upper() + replacer[index - start_location][1:]
        replace_map_copy[index][1] = replacer[index - start_location]
    if replace_rules["units"] != None:
        digit_check_start = (start_location - digit_len_before) if (start_location - digit_len_before >= 0) else 0
        digit_version = _text2int(" ".join([x[1] for x in replace_map_copy[digit_check_start:start_location]]))
        for i in range(digit_check_start, start_location):
            replace_map_copy[i][1] = digit_version[i - digit_check_start]
        digit_found = False
        for index in range(start_location - 1, digit_check_start-1, -1):
            if is_float(replace_map_copy[index][1]):
                digit_found = True
                replace_map_copy[index][1] = str(round(float(replace_map_copy[index][1]) * replace_rules["units"], 1)).replace('.0', '')
            elif replace_map_copy[index][1] in in_num_words:  # 'or' and 'so' added for special cases
                pass
            else:
                if not digit_found:
                    if not replace_rules["can be word"]:
                        replace_map_copy[start_location][1] = str(replace_rules["units"]) + (
                            (" " + replace_map_copy[start_location][1]) if replace_map_copy[start_location][1] != '' else '')
                    else:
                        return None
                        # return results
                break
    return replace_map_copy


def find_in_clean(clean_words: list, look_for_words: list, to_find: str, mutations: bool, replace_rules: dict): # time eater - try to solve
    words_num = len(look_for_words)
    if clean_words == [''] or look_for_words == [''] or len(clean_words) < words_num:
        return {"found": False}
    results = {"found": False, "replace_map": [[x, x] for x in clean_words]}
    start_location = None
    feet_case = False
    i = 0
    while i < len(clean_words) + 1 - words_num:
        if replace_rules['feet']!=None and is_feet(clean_words[i]):
            feet_case = True
            word = results["replace_map"][i][0]
            parts = word.split("'")
            if len(parts) != 2:
                parts = word.split("’")
            results["replace_map"][i][1] = str(round((float(parts[0]) + float(parts[1]) * 0.0833) * replace_rules['feet'], 1))
        else:
            word_chunk = ' '.join(clean_words[i:i + words_num])
            word_borders = re.split(to_find, word_chunk, flags=re.IGNORECASE)
            if len(word_borders) != 1 and word_borders[0] == '':
                if ((word_borders[1] == '') or (mutations and (word_borders[1] in possible_mutations))):
                    results["found"] = True
                    start_location = i
                    i += words_num - 1
                    if feet_case:
                        feet_ignore = replace_rules.copy()
                        feet_ignore.update({"unit": None})
                        results["replace_map"] = _text_modifier(start_location, words_num, results["replace_map"], feet_ignore) or results["replace_map"]
                    else:
                        results["replace_map"] = _text_modifier(start_location, words_num, results["replace_map"], replace_rules) or results["replace_map"]
            feet_case = False
        i += 1
    return results


def text_cleaner(string: str, settings: dict) -> str:
    erase_pattern = (
        ERASE_WITH_COMMA_SEPARATOR_PATTERN
        if settings["coma in digits"]
        else ERASE_WITH_PERIOD_SEPARATOR_PATTERN
    )
    cleaned = erase_pattern.sub(" ", string)
    return MULTIPLE_SPACES_PATTERN.sub(" ", cleaned).strip(" ")
