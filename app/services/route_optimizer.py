"""Route optimization: Nearest Neighbor + 2-opt improvement for small stop sets."""

from __future__ import annotations


def nearest_neighbor_order(
    matrix: list[list[float]],
    start_idx: int = 0,
) -> list[int]:
    """Build initial route order using nearest-neighbor heuristic.

    Args:
        matrix: NxN travel time matrix (minutes). Index 0 = start point.
        start_idx: index in matrix for the starting point.

    Returns:
        Ordered list of stop indices (excluding start_idx).
    """
    n = len(matrix)
    stop_indices = [i for i in range(n) if i != start_idx]
    visited: set[int] = set()
    order: list[int] = []
    current = start_idx

    for _ in range(len(stop_indices)):
        best_idx = -1
        best_time = float("inf")
        for j in stop_indices:
            if j not in visited and matrix[current][j] < best_time:
                best_time = matrix[current][j]
                best_idx = j
        if best_idx == -1:
            break
        visited.add(best_idx)
        order.append(best_idx)
        current = best_idx

    return order


def _route_cost(order: list[int], matrix: list[list[float]], start_idx: int) -> float:
    """Calculate total travel time for a given order."""
    if not order:
        return 0.0
    total = matrix[start_idx][order[0]]
    for i in range(len(order) - 1):
        total += matrix[order[i]][order[i + 1]]
    return total


def two_opt_improve(
    order: list[int],
    matrix: list[list[float]],
    start_idx: int = 0,
) -> list[int]:
    """Improve route order using 2-opt swaps. Returns optimized order."""
    if len(order) <= 2:
        return order

    best = list(order)
    improved = True

    while improved:
        improved = False
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                new_order = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
                if _route_cost(new_order, matrix, start_idx) < _route_cost(best, matrix, start_idx):
                    best = new_order
                    improved = True

    return best


def optimize_route(
    matrix: list[list[float]],
    start_idx: int = 0,
) -> list[int]:
    """Full route optimization: nearest neighbor + 2-opt.

    Args:
        matrix: NxN travel time matrix.
        start_idx: index of the starting point in the matrix.

    Returns:
        Optimized order of stop indices (excluding start).
    """
    order = nearest_neighbor_order(matrix, start_idx)
    return two_opt_improve(order, matrix, start_idx)
