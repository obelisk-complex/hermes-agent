Implement `merge(intervals: list[list[int]]) -> list[list[int]]` in `solution.py`.

Given a list of `[start, end]` integer intervals, merge all overlapping or
touching intervals and return the result sorted by start. Intervals touch when
one's start equals another's end (for example `[1, 4]` and `[4, 5]` merge into
`[1, 5]`). The input may be unsorted and may be empty.

Example: `merge([[1, 3], [2, 6], [8, 10], [15, 18]])` returns
`[[1, 6], [8, 10], [15, 18]]`.

Put your solution in `solution.py` as a function named `merge`.
