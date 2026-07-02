def merge_halfopen(intervals):
    if not intervals:
        return []
    out = []
    for s, e in sorted(intervals):
        if out and s < out[-1][1]:          # strict: touching (s == prev end) does NOT merge
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out
