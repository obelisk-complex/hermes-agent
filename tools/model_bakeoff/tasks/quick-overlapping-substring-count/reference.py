def count_overlapping(s, sub):
    if not sub:
        return 0
    count = start = 0
    while True:
        i = s.find(sub, start)
        if i == -1:
            return count
        count += 1
        start = i + 1
