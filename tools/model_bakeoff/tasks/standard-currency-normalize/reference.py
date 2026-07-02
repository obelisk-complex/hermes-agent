import re


def normalize_number(s):
    if not isinstance(s, str):
        raise ValueError("not a string")
    t = s.strip()
    if not t:
        raise ValueError("empty")
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg = True
        t = t[1:-1].strip()
    for sym in ("$", "£", "€"):
        if t.startswith(sym):
            t = t[1:].strip()
            break
    if t.startswith("-"):
        neg = not neg
        t = t[1:].strip()
    pct = False
    if t.endswith("%"):
        pct = True
        t = t[:-1].strip()
    t = t.replace(",", "")
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        raise ValueError(f"unparseable: {s!r}")
    val = float(t)
    if pct:
        val /= 100.0
    return -val if neg else val
