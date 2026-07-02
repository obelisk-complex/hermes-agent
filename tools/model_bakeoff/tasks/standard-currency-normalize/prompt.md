Implement `normalize_number(s: str) -> float` in `solution.py`.

Parse a human-written money/number string into a float:
- Strip surrounding whitespace and a leading currency symbol (`$`, `£`, `€`).
- Thousands separators are commas; the decimal separator is a dot: `"1,234.50" -> 1234.5`.
- A trailing `%` divides the value by 100: `"12.5%" -> 0.125`.
- Parentheses mean negative (accounting style): `"(1,000)" -> -1000.0`.
- A leading `-` is also negative.

On anything unparseable, raise `ValueError`. Do NOT use `eval`.
