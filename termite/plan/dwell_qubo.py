"""Dwell planning as a QUBO.

Choose how many time units ``k`` each band gets. Binary variable x[b,k] = 1
means band b gets k units. The objective rewards coverage with diminishing
returns (priority * log(1 + k)), and penalties enforce exactly one choice per
band and a total of ``units`` across bands.

Solved with dwave-samplers' simulated annealer if installed
(``pip install 'anteater[termite]'``), otherwise with the small numpy annealer
below. Both give the same kind of answer; the D-Wave one is faster on big plans.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from termite.plan.allocator import Band

Var = tuple[int, int]  # (band index, units)


def build_qubo(
    bands: Sequence[Band],
    units: int,
    max_units: int | None = None,
    one_hot: float | None = None,
    budget: float | None = None,
) -> dict[tuple[Var, Var], float]:
    max_units = max_units or units
    top = max(b.priority for b in bands) * math.log1p(max_units)
    a = one_hot if one_hot is not None else 4.0 * top  # one choice per band
    lam = budget if budget is not None else 2.0 * top / max(1, units)  # total units
    q: dict[tuple[Var, Var], float] = {}

    def add(u: Var, v: Var, w: float) -> None:
        key = (u, v) if u <= v else (v, u)
        q[key] = q.get(key, 0.0) + w

    variables = [(i, k) for i in range(len(bands)) for k in range(max_units + 1)]
    for i, k in variables:
        # reward, one-hot (x - 1)^2 -> -x linear, and budget linear terms
        add((i, k), (i, k), -bands[i].priority * math.log1p(k) - a + lam * (k * k - 2 * units * k))
    for idx, u in enumerate(variables):
        for v in variables[idx + 1 :]:
            if u[0] == v[0]:
                add(u, v, 2 * a)
            add(u, v, 2 * lam * u[1] * v[1])
    return q


def energy(q: dict[tuple[Var, Var], float], sample: dict[Var, int]) -> float:
    return sum(w * sample.get(u, 0) * sample.get(v, 0) for (u, v), w in q.items())


def anneal(q: dict[tuple[Var, Var], float], sweeps: int = 600, seed: int = 0) -> dict[Var, int]:
    """Plain simulated annealing on a QUBO. Good enough for a few hundred variables."""
    rng = np.random.default_rng(seed)
    variables = sorted({v for pair in q for v in pair})
    index = {v: i for i, v in enumerate(variables)}
    n = len(variables)
    mat = np.zeros((n, n))
    for (u, v), w in q.items():
        i, j = index[u], index[v]
        if i == j:
            mat[i, i] += w
        else:
            mat[i, j] += w / 2
            mat[j, i] += w / 2
    x = rng.integers(0, 2, n).astype(float)
    scale = float(np.max(np.abs(mat))) or 1.0
    best, best_e = x.copy(), float(x @ mat @ x)
    for t in range(sweeps):
        temp = scale * (1.0 - t / sweeps) + 1e-3
        for i in rng.permutation(n):
            # energy change of flipping x[i]
            delta = (1 - 2 * x[i]) * (mat[i, i] + 2 * (mat[i] @ x - mat[i, i] * x[i]))
            if delta < 0 or rng.random() < math.exp(-delta / temp):
                x[i] = 1 - x[i]
        e = float(x @ mat @ x)
        if e < best_e:
            best, best_e = x.copy(), e
    return {v: int(best[i]) for v, i in index.items()}


def solve(bands: Sequence[Band], units: int, seed: int = 0) -> dict[str, int]:
    """Return {band name: units}. Always one entry per band; fixes up invalid samples."""
    q = build_qubo(bands, units)
    try:
        from dwave.samplers import SimulatedAnnealingSampler

        result = SimulatedAnnealingSampler().sample_qubo(q, num_reads=50, seed=seed)
        sample = {k: int(v) for k, v in result.first.sample.items()}
    except ImportError:
        sample = anneal(q, seed=seed)

    out: dict[str, int] = {}
    for i, b in enumerate(bands):
        picked = [k for (bi, k), on in sample.items() if bi == i and on]
        out[b.name] = max(picked) if picked else 0
    # repair: the penalties make this rare, but never return more than the budget
    while sum(out.values()) > units:
        worst = min((b for b in bands if out[b.name] > 0), key=lambda b: b.priority)
        out[worst.name] -= 1
    return out
