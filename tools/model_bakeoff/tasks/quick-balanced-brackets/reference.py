def is_balanced(s):
    pairs = {")": "(", "]": "[", "}": "{"}
    opens = set(pairs.values())
    stack = []
    for ch in s:
        if ch in opens:
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack
