_DIGITS = "0123456789abcdef"


def to_base(n, b):
    if not 2 <= b <= 16:
        raise ValueError(f"base out of range: {b}")
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, b)
        out.append(_DIGITS[r])
    return "".join(reversed(out))
