import math


def isqrt(n):
    if n < 0:
        raise ValueError("isqrt is undefined for negative n")
    return math.isqrt(n)
