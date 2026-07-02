import re


def evaluate(expr):
    toks = re.findall(r"\d+|[()+\-*/]", expr or "")
    if "".join(toks) != re.sub(r"\s+", "", expr or ""):
        raise ValueError("bad token")
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def eat():
        nonlocal pos
        t = toks[pos]
        pos += 1
        return t

    def tdiv(a, b):
        if b == 0:
            raise ValueError("div0")
        q = abs(a) // abs(b)
        return q if (a < 0) == (b < 0) else -q

    def atom():
        t = peek()
        if t == "(":
            eat()
            v = expr_()
            if peek() != ")":
                raise ValueError("unbalanced")
            eat()
            return v
        if t == "-":
            eat()
            return -atom()
        if t is None or not t.isdigit():
            raise ValueError("atom")
        return int(eat())

    def term():
        v = atom()
        while peek() in ("*", "/"):
            op = eat()
            r = atom()
            v = v * r if op == "*" else tdiv(v, r)
        return v

    def expr_():
        v = term()
        while peek() in ("+", "-"):
            op = eat()
            r = term()
            v = v + r if op == "+" else v - r
        return v

    if not toks:
        raise ValueError("empty")
    v = expr_()
    if pos != len(toks):
        raise ValueError("trailing")
    return v
