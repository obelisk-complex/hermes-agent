Implement `merge_halfopen(intervals: list[list[int]]) -> list[list[int]]` in `solution.py`.

Treat each interval as HALF-OPEN `[start, end)`: it covers `start <= x < end`.
Merge ONLY on true overlap. TOUCHING does NOT merge: `[1,4)` and `[4,5)` stay separate,
giving `[[1,4],[4,5]]`.

Return the merged intervals sorted by start. The input may be unsorted or empty.
