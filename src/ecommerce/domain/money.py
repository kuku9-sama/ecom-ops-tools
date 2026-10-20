from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# 不同币种的最小单位位数不同：IDR/VND 没有小数位。
MINOR_UNITS = {
    "CNY": 2,
    "USD": 2,
    "SGD": 2,
    "MYR": 2,
    "THB": 2,
    "PHP": 2,
    "IDR": 0,
    "VND": 0,
}

# 长符号放前面，避免 "US$" 先被 "$" 吃掉，留下 "US"。
_SYMBOLS = ("US$", "S$", "RM", "Rp", "฿", "₫", "¥", "$", "€", "£")


class MoneyError(ValueError):
    pass


def parse_amount_to_minor(text: str, currency: str) -> int:
    """把 '¥1,234.50' 变成 123450。"""
    if currency not in MINOR_UNITS:
        raise MoneyError(f"不支持的币种: {currency!r}")

    cleaned = text.strip().replace(" ", " ")
    for symbol in sorted(_SYMBOLS, key=len, reverse=True):
        cleaned = cleaned.replace(symbol, "")
    cleaned = cleaned.replace(",", "").replace(" ", "")
    if cleaned in ("", "-", "."):
        raise MoneyError(f"金额为空: {text!r}")

    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise MoneyError(f"金额格式错误: {text!r}") from exc

    scale = Decimal(10) ** MINOR_UNITS[currency]
    return int((amount * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def convert_minor(amount_minor: int, rate: Decimal) -> int:
    """按汇率换算最小单位，四舍五入到整数。"""
    if rate <= 0:
        raise MoneyError(f"汇率必须大于 0: {rate}")
    return int((Decimal(amount_minor) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_minor(amount_minor: int, currency: str) -> str:
    """把 129900 变回 '1299.00'，用于发给平台的金额字段。"""
    digits = MINOR_UNITS[currency]
    scale = 10 ** digits
    sign = "-" if amount_minor < 0 else ""
    whole, frac = divmod(abs(amount_minor), scale)
    if digits == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{frac:0{digits}d}"
