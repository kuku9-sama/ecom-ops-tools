from decimal import Decimal

from ecommerce.domain.money import convert_minor, parse_amount_to_minor


def test_parse_amount_with_symbol_and_comma():
    assert parse_amount_to_minor("¥1,234.50", "CNY") == 123450


def test_parse_half_up_not_float():
    # float 下 1.005 * 100 可能是 100.499999...，用 Decimal 才会得到 101。
    assert parse_amount_to_minor("1.005", "USD") == 101


def test_zero_decimal_currency():
    assert parse_amount_to_minor("123,450", "IDR") == 123450


def test_convert_rounds_half_up():
    assert convert_minor(1550, Decimal("7.25")) == 11238
    assert convert_minor(123450, Decimal("0.14")) == 17283
