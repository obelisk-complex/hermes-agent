def merge(intervals):
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda iv: iv[0])
    out = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out
