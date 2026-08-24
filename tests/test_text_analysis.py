from text_evolver.processing.text_analysis import convert_utf8_symbols, is_feet, text2int, text_cleaner


def test_text_cleaner_preserves_decimal_separator_and_removes_punctuation():
    assert text_cleaner("Distance: 12.5 miles!", {"coma in digits": False}) == "Distance 12.5 miles"
    assert text_cleaner("Distance: 12,5 miles!", {"coma in digits": True}) == "Distance 12,5 miles"


def test_spelled_numbers_and_feet_are_recognized():
    assert text2int("twenty three miles") == ["23", "", "miles"]
    assert is_feet("5'11")
    assert convert_utf8_symbols("Tom &amp; Jerry") == "Tom & Jerry"

