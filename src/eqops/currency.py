"""Currency-pair validation for FX trade bookings."""
from __future__ import annotations

import re

# Active ISO 4217 codes most often seen on an FX desk (not the full list).
ISO_4217 = {
    "USD", "EUR", "JPY", "GBP", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK", "HKD",
    "SGD", "CNH", "CNY", "KRW", "INR", "MXN", "BRL", "ZAR", "TRY", "PLN", "CZK", "HUF",
    "ILS", "THB", "TWD", "IDR", "MYR", "PHP", "SAR", "AED", "KES", "NGN", "EGP", "CLP",
    "COP", "PEN", "RON", "ISK",
}
# Codes that are valid ISO but should never appear on a spot ticket.
NON_TRADABLE = {"XXX", "XTS", "XAU", "XAG"}

_PAIR = re.compile(r"^([A-Za-z]{3})[/\-]?([A-Za-z]{3})$")


def validate_pair(pair) -> tuple[str | None, list[str]]:
    """Return (normalised 'BASE/QUOTE' or None, list of issue codes).

    Issue codes: MISSING, BAD_FORMAT, NOT_UPPERCASE, UNKNOWN_CCY:<code>,
    NON_TRADABLE:<code>, SAME_CCY. NOT_UPPERCASE is a warning: the pair is
    still normalised so it can be repaired rather than rejected.
    """
    if pair is None or (isinstance(pair, float)) or str(pair).strip() == "":
        return None, ["MISSING"]
    s = str(pair).strip()
    m = _PAIR.match(s)
    if not m:
        return None, ["BAD_FORMAT"]
    issues = []
    base, quote = m.group(1), m.group(2)
    if base != base.upper() or quote != quote.upper():
        issues.append("NOT_UPPERCASE")
    base, quote = base.upper(), quote.upper()
    for c in (base, quote):
        if c in NON_TRADABLE:
            issues.append(f"NON_TRADABLE:{c}")
        elif c not in ISO_4217:
            issues.append(f"UNKNOWN_CCY:{c}")
    if base == quote:
        issues.append("SAME_CCY")
    ok = not [i for i in issues if i != "NOT_UPPERCASE"]
    return (f"{base}/{quote}" if ok else None), issues
