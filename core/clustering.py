"""Embedding-based clustering via union-find over cosine similarity.

Shared helper extracted so the consolidator can group observations into
topically-coherent chunks the same way the crystallizer groups promotion
candidates. Deterministic for fixed embeddings.
"""

from __future__ import annotations


def cluster_by_embeddings(
    embeddings,  # sequence of equal-length float vectors, or numpy (N, dim)
    threshold: float,
    *,
    adaptive: bool = True,
) -> list[list[int]]:
    """Group row indices ``[0..N)`` by cosine similarity using union-find.

    Returns a list of index groups. Deterministic for fixed embeddings.

    When ``adaptive`` is True the effective threshold is lifted to
    ``max(threshold, P75 of off-diagonal sims)``, capped at 0.85, so a tight
    similarity distribution does not collapse everything into one cluster.
    ``threshold`` stays a hard minimum so a sparse batch never over-clusters.
    """
    import numpy as np

    n = len(embeddings)
    if n == 0:
        return []
    if n == 1:
        return [[0]]

    arr = np.asarray(embeddings, dtype=float)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    normed = arr / np.maximum(norms, 1e-9)
    sims = normed @ normed.T

    if adaptive:
        iu = np.triu_indices(n, k=1)
        off_diag = sims[iu]
        p75 = float(np.percentile(off_diag, 75)) if off_diag.size else threshold
        effective = min(0.85, max(threshold, p75))
    else:
        effective = threshold

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if sims[i, j] >= effective:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def balance_chunks(groups: list[list[int]], chunk_size: int) -> list[list[int]]:
    """Split oversized groups and coalesce small ones to ``chunk_size``.

    Groups larger than ``chunk_size`` are sliced into consecutive sub-chunks.
    Small groups are greedily merged so the consolidator does not issue a
    flood of 1-observation LLM calls. Deterministic given group ordering.
    """
    big: list[list[int]] = []
    small: list[list[int]] = []
    for g in groups:
        if len(g) > chunk_size:
            for k in range(0, len(g), chunk_size):
                big.append(g[k : k + chunk_size])
        elif g:
            small.append(g)

    merged: list[list[int]] = []
    cur: list[int] = []
    for g in sorted(small, key=len, reverse=True):
        if cur and len(cur) + len(g) > chunk_size:
            merged.append(cur)
            cur = []
        cur.extend(g)
    if cur:
        merged.append(cur)

    return big + merged
