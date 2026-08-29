import pytest

from text_evolver.processing.text_analysis import (
    convert_utf8_symbols,
    is_feet,
    redistribute_transformed_text,
    string_empty,
    text2int,
    text_cleaner,
)


def test_text_cleaner_preserves_decimal_separator_and_removes_punctuation():
    assert text_cleaner("Distance: 12.5 miles!", {"coma in digits": False}) == "Distance 12.5 miles"
    assert text_cleaner("Distance: 12,5 miles!", {"coma in digits": True}) == "Distance 12,5 miles"


@pytest.mark.parametrize(
    ("comma_in_digits", "source", "expected"),
    [
        (False, "Values .5 5. 1.5 and 1,5.", "Values 5 5 1.5 and 1 5"),
        (True, "Values ,5 5, 1,5 and 1.5.", "Values 5 5 1,5 and 1 5"),
        (False, "Names first.last and a,b", "Names first last and a b"),
        (True, "Names first,last and a.b", "Names first last and a b"),
    ],
)
def test_text_cleaner_keeps_only_configured_separators_between_digits(
    comma_in_digits: bool,
    source: str,
    expected: str,
):
    assert text_cleaner(source, {"coma in digits": comma_in_digits}) == expected


def test_text_cleaner_erases_arbitrary_non_alphanumeric_symbols():
    source = "Latin/Кириллица—漢字_42 @#$%^&*+=|{}[]()<>~`\\\nnext"

    assert text_cleaner(source, {"coma in digits": False}) == "Latin Кириллица 漢字 42 next"


def test_text_cleaner_preserves_apostrophes_used_by_other_matchers():
    source = "Height 5'11 and cat’s paws"

    assert text_cleaner(source, {"coma in digits": False}) == source


def test_spelled_numbers_and_feet_are_recognized():
    assert text2int("twenty three miles") == ["23", "", "miles"]
    assert is_feet("5'11")
    assert is_feet("5’11")
    assert not is_feet(None)
    assert not is_feet("5 feet 11")
    assert not is_feet("5'11 extra")
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
