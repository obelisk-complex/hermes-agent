def encode(s):
    if not s:
        return ""
    out = []
    prev = s[0]
    count = 1
    for ch in s[1:]:
        if ch == prev:
            count += 1
        else:
            out.append(f"{prev}{count}")
            prev = ch
            count = 1
    out.append(f"{prev}{count}")
    return "".join(out)
