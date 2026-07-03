def digit_root(n):
    while n >= 10:
        n = sum(int(c) for c in str(n))
    return n
