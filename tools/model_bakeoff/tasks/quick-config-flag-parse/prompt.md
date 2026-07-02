Implement `parse_flags(argv: list[str], defaults: dict) -> dict` in `solution.py`.

Parse a `--key value` argument list, layered over `defaults`:
- Start from a COPY of `defaults` (never mutate the input).
- `--name value` sets `name` to `value` (a string).
- `--flag` with no following value, or followed by another `--token`, is a boolean `True`.
- Later occurrences override earlier ones.
- A bare token that is not preceded by a `--key` expecting a value is ignored.

Example: `parse_flags(["--host", "prod", "--verbose"], {"host": "local", "verbose": False})`
returns `{"host": "prod", "verbose": True}`.
