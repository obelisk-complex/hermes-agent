Implement `topo_order(deps: dict[str, list[str]]) -> list[str]` in `solution.py`.

`deps` maps each node to the list of nodes it DEPENDS ON (its prerequisites). Return a
topological ordering: every node appears after all of its dependencies.

Rules:
- Nodes appearing only inside dependency lists (never as a key) are still real nodes and
  must appear in the output.
- Break ties by choosing the smallest available node (lexicographic), so the output is
  deterministic (Kahn's algorithm with a sorted frontier).
- If the graph contains a cycle, raise `ValueError`.

Example: `topo_order({"b": ["a"], "c": ["a", "b"], "a": []})` returns `["a", "b", "c"]`.
