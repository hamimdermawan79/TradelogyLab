import pytest

from app import parse_params


def test_empty_params_returns_empty_dict():
    assert parse_params("") == {}
    assert parse_params("\n  \n# comment\n") == {}


def test_key_value_lines():
    assert parse_params("fast = 20\nslow = 50\nsize_pct = 0.1\nleverage = 5") == {
        "fast": 20, "slow": 50, "size_pct": 0.1, "leverage": 5}


def test_string_and_bool_values():
    assert parse_params('name = "ema"\nflag = True') == {"name": "ema", "flag": True}


def test_json_fallback_still_accepted():
    assert parse_params('{"fast": 20, "slow": 50}') == {"fast": 20, "slow": 50}


def test_invalid_line_raises():
    with pytest.raises(ValueError, match="key = value"):
        parse_params("just_a_word")
    with pytest.raises(ValueError):
        parse_params("{not valid json")
