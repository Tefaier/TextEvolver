import pytest

from text_evolver.processing import text_analysis
from text_evolver.processing.text_analysis import (
    ReplaceRules,
    _text2int,
    convert_utf8_symbols,
    is_feet,
    redistribute_transformed_text,
    replace_iteration,
    string_empty,
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


def test_replace_iteration_preserves_punctuation_between_cleaned_words():
    replace_map = [["An", "An"], ["old", "brand"], ["road", "new path"]]

    assert replace_iteration(replace_map, ["An", "old-road."]) == ["An", "brand-new path."]


def test_replace_iteration_treats_sources_as_literal_text():
    replace_map = [["Value", "Value"], ["1.5", "2.5"]]

    assert replace_iteration(replace_map, ["Value", "1.5."]) == ["Value", "2.5."]


def test_replace_iteration_reports_unmatched_source():
    with pytest.raises(ValueError, match="'missing' was not found"):
        replace_iteration([["missing", "replacement"]], ["present"])


def test_spelled_numbers_and_feet_are_recognized():
    assert _text2int("twenty three miles") == ["23", "", "miles"]
    assert _text2int("5'11 5’11") == ["5.9163", "5.9163"]
    assert is_feet("5'11")
    assert is_feet("5’11")
    assert not is_feet(None)
    assert not is_feet("5 feet 11")
    assert not is_feet("5'11 extra")
    assert convert_utf8_symbols("Tom &amp; Jerry") == "Tom & Jerry"


def test_replace_rules_are_typed_values():
    assert ReplaceRules(replace_with="kilometers", lookup_length=1, units=1.6, can_be_word=True) == ReplaceRules(
        replace_with="kilometers",
        lookup_length=1,
        units=1.6,
        can_be_word=True,
    )


def test_replace_rules_require_positive_lookup_length():
    with pytest.raises(ValueError, match="lookup length must be positive"):
        ReplaceRules(replace_with="replacement", lookup_length=0)


def test_find_in_clean_copies_only_the_relevant_map_window(monkeypatch):
    modifier_windows: list[tuple[int, int]] = []
    original_modifier = text_analysis._text_modifier

    def record_modifier(start_location, words_num, replace_map_part, replace_rules, convert_units=True):
        modifier_windows.append((start_location, len(replace_map_part)))
        return original_modifier(
            start_location,
            words_num,
            replace_map_part,
            replace_rules,
            convert_units,
        )

    monkeypatch.setattr(text_analysis, "_text_modifier", record_modifier)
    text_analysis.find_in_clean(
        ["before", "old", "road", "after"],
        "old road",
        ReplaceRules(replace_with="new path", lookup_length=2),
    )
    text_analysis.find_in_clean(
        ["a", "b", "c", "d", "e", "f", "g", "h", "2", "miles"],
        "miles",
        ReplaceRules(
            replace_with="kilometers",
            lookup_length=1,
            units=1.6,
            can_be_word=True,
        ),
    )

    assert modifier_windows == [(0, 2), (7, 8)]


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
