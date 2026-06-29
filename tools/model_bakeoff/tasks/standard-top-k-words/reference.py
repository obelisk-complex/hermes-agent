from collections import Counter


def top_k(text, k):
    counts = Counter(text.lower().split())
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [word for word, _ in ranked[:k]]
