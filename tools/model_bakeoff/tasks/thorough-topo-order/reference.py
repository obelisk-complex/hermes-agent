import heapq


def topo_order(deps):
    nodes = set(deps)
    for d in deps.values():
        nodes.update(d)
    indeg = {n: 0 for n in nodes}
    adj = {n: [] for n in nodes}
    for node, prereqs in deps.items():
        for p in prereqs:
            adj[p].append(node)
            indeg[node] += 1
    frontier = [n for n in nodes if indeg[n] == 0]
    heapq.heapify(frontier)
    out = []
    while frontier:
        n = heapq.heappop(frontier)
        out.append(n)
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                heapq.heappush(frontier, m)
    if len(out) != len(nodes):
        raise ValueError("cycle detected")
    return out
