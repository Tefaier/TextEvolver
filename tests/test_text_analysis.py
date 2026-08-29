import pytest

from text_evolver.processing.text_analysis import (
    redistribute_transformed_text,
    convert_utf8_symbols,
    is_feet,
    string_empty,
    text2int,
    text_cleaner,
)


def test_text_cleaner_preserves_decimal_separator_and_removes_punctuation():
    assert text_cleaner("Distance: 12.5 miles!", {"coma in digits": False}) == "Distance 12.5 miles"
    assert text_cleaner("Distance: 12,5 miles!", {"coma in digits": True}) == "Distance 12,5 miles"


def test_spelled_numbers_and_feet_are_recognized():
    assert text2int("twenty three miles") == ["23", "", "miles"]
    assert is_feet("5'11")
    assert convert_utf8_symbols("Tom &amp; Jerry") == "Tom & Jerry"


def test_string_empty_accepts_only_empty_or_whitespace_text():
    assert string_empty("")
    assert string_empty("  \n\t")
    assert string_empty("\u2003")
    assert not string_empty(".")
    assert not string_empty("  text  ")


def test_redistribute_transformed_text_preserves_native_part_ownership():
    assert redistribute_transformed_text(["old", " road"], "brand new path") == ["brand", " new path"]
    assert redistribute_transformed_text(["unchanged", " text"], "unchanged text") == ["unchanged", " text"]


def test_redistribute_transformed_text_requires_a_native_part():
    with pytest.raises(ValueError, match="at least one native part"):
        redistribute_transformed_text([], "text")
