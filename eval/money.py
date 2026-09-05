"""Indian-grouped rupee formatting. Money is stored in paise everywhere in
this system and formatted exactly once, here — the batch report renderer
calls it directly, and Phase 7's Jinja filter wraps this function rather
than reimplementing the grouping, so a rupee figure reads identically on
the dashboard and in the CLI report.
"""

from __future__ import annotations


def group_indian(digits: str) -> str:
    """12345678 -> 1,23,45,678 — last three digits, then pairs."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def format_paise(paise: int, *, paise_precision: bool = False) -> str:
    """₹12,45,890 by default. Rounds to whole rupees for display only —
    never for arithmetic, which stays in integer paise throughout."""
    sign = "-" if paise < 0 else ""
    magnitude = abs(paise)
    if paise_precision:
        rupees, remainder = divmod(magnitude, 100)
        return f"{sign}₹{group_indian(str(rupees))}.{remainder:02d}"
    return f"{sign}₹{group_indian(str(round(magnitude / 100)))}"


def format_rate(rate: float | None, places: int = 2) -> str:
    return "—" if rate is None else f"{rate * 100:.{places}f}%"
