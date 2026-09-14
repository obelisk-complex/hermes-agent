# Refactor Gotcha Checklist

After any refactor, verify:
1. All existing tests pass (run test suite)
2. Previously-working manual flows still work
3. No renamed imports left dangling
4. No deleted exports referenced elsewhere
5. Error messages still meaningful
6. Logging not broken
7. Public API surface unchanged
