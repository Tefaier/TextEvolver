import copy
import html
import re
import unicodedata

in_num_words = ["of", '', 'to', 'or', 'so']
erase_symbols_def = [',', '?', ':', ';', '!', '[', ']', '(', ')', '-', '_', '"', '>', '<', '*']
possible_mutations = ['s', "'", "'s", 'es', 'ов', 'ы', 'а', '’s']
digit_len_before = 7


def string_with_meaning(text: str):
    return re.search(r"[^\W_]", text) is not None


def convert_utf8_symbols(text: str):
    return unicodedata.normalize("NFKC", html.unescape(text))


def is_float(string: str):
    if string is None:
        return False
    try:
        float(string)
        return True
    except ValueError:
        return False


def is_feet(string: str):
    if string is None:
        return False
    try:
        parts1 = string.split("'")
        parts2 = string.split("’")
        return (len(parts1)==2 and parts1[0].isdigit() and parts1[1].isdigit()) or (len(parts2)==2 and parts2[0].isdigit() and parts2[1].isdigit())
    except ValueError:
        return False


def replace_iteration(replace_map: list, words: list):
    current_word = len(words) - 1
    right_part = ''
    left_part = words[current_word]
    index = len(replace_map) - 1
    while index >= 0:
        borders = re.split(replace_map[index][0], left_part, flags=re.IGNORECASE)
        if len(borders) == 1:  # not found
            words[current_word] = left_part + right_part
            current_word -= 1
            if current_word < 0:
                raise Exception
            right_part = ''
            left_part = words[current_word]
        else:  # found
            right_part = replace_map[index][1] + borders[-1] + right_part
            left_part = left_part[:len(left_part) - len(replace_map[index][0]) - len(borders[-1])]
            index -= 1
    return words


def text2int(textnum, numwords={}, interimwords={}):
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


def text_modifier(start_location: int, words_num: int, replace_map: list, replace_rules: dict):
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
        digit_version = text2int(" ".join([x[1] for x in replace_map_copy[digit_check_start:start_location]]))
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
                        results["replace_map"] = text_modifier(start_location, words_num, results["replace_map"], feet_ignore) or results["replace_map"]
                    else:
                        results["replace_map"] = text_modifier(start_location, words_num, results["replace_map"], replace_rules) or results["replace_map"]
            feet_case = False
        i += 1
    return results


def text_cleaner(string: str, settings: dict):
    erase_symbols = erase_symbols_def.copy()
    separator = '.'
    if settings["coma in digits"]:
        erase_symbols.remove(',')
        erase_symbols.append('.')
        separator = ','
    new_string = string
    for to_erase in erase_symbols:
        new_string = new_string.replace(to_erase, ' ')
    for i in range(len(new_string) - 1, -1, -1):
        if new_string[i] == separator:
            if i == 0 or i == len(new_string) - 1:
                new_string = new_string[0:i] + new_string[i + 1:]
            elif not (is_float(new_string[i + 1]) and is_float(new_string[i - 1])):
                new_string = new_string[0:i] + ' ' + new_string[i + 1:]
    while "  " in new_string:
        new_string = new_string.replace('  ', ' ')
    if (len(new_string) > 0 and new_string[0] == ' '):
        new_string = new_string[1:]
    if (len(new_string) > 0 and new_string[-1] == ' '):
        new_string = new_string[:-1]
    return new_string
