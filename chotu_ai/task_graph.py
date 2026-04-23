"""Task Graph — explicit dependency graph management."""
import dataclasses
from typing import Optional


@dataclasses.dataclass
class TaskGraph:
    nodes: dict
    edges: dict
    order: list
    is_valid: bool
    validation_errors: list


def build(todo_list: list) -> TaskGraph:
    """Build a TaskGraph from a flat step list."""
    nodes = {}
    edges = {}

    for step in todo_list:
        step_id = step.get("id", "")
        nodes[step_id] = step
        edges[step_id] = step.get("depends_on", [])

    is_valid, validation_errors = _validate_graph(nodes, edges)

    if is_valid:
        order = _topological_sort(nodes, edges)
    else:
        order = [s.get("id", "") for s in todo_list]

    return TaskGraph(
        nodes=nodes,
        edges=edges,
        order=order,
        is_valid=is_valid,
        validation_errors=validation_errors,
    )


def get_ready_steps(graph: TaskGraph, completed: list,
                  failed_skipped: list = None) -> list:
    """Return step IDs whose dependencies are all satisfied."""
    failed_skipped = failed_skipped or []
    ready = []

    for step_id in graph.order:
        node = graph.nodes.get(step_id)
        if not node:
            continue
        status = node.get("status", "pending")

        if status not in ("pending", "generating"):
            continue

        deps = graph.edges.get(step_id, [])
        deps_met = all(
            d in completed or d in failed_skipped
            for d in deps
        )
        if deps_met:
            ready.append(step_id)

    return ready


def get_execution_order(graph: TaskGraph) -> list:
    """Return topological execution order."""
    return list(graph.order)


def is_blocked(graph: TaskGraph, step_id: str, completed: list) -> tuple:
    """Check if a step is blocked. Returns (is_blocked, blocking_deps)."""
    deps = graph.edges.get(step_id, [])
    blocking = [d for d in deps if d not in completed]
    return (len(blocking) > 0, blocking)


def validate(graph: TaskGraph) -> tuple:
    """Validate the graph structure."""
    return _validate_graph(graph.nodes, graph.edges)


def _validate_graph(nodes: dict, edges: dict) -> tuple:
    """Check for cycles and missing dependencies."""
    errors = []

    all_ids = set(nodes.keys())
    for step_id, deps in edges.items():
        for dep in deps:
            if dep not in all_ids:
                errors.append(f"Step '{step_id}' depends on '{dep}' which does not exist")

    if not errors:
        has_cycle, cycle_path = _detect_cycle(nodes, edges)
        if has_cycle:
            errors.append(f"Cycle detected: {' → '.join(cycle_path)}")

    return (len(errors) == 0, errors)


def _detect_cycle(nodes: dict, edges: dict) -> tuple:
    """DFS cycle detection. Returns (has_cycle, cycle_path)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in nodes}
    path = []

    def dfs(node):
        color[node] = GRAY
        path.append(node)
        for dep in edges.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GRAY:
                cycle_start = path.index(dep)
                return True, path[cycle_start:] + [dep]
            if color[dep] == WHITE:
                result = dfs(dep)
                if result[0]:
                    return result
        path.pop()
        color[node] = BLACK
        return False, []

    for node in nodes:
        if color[node] == WHITE:
            result = dfs(node)
            if result[0]:
                return result
    return False, []


def _topological_sort(nodes: dict, edges: dict) -> list:
    """Kahn's algorithm for topological sort."""
    in_degree = {nid: 0 for nid in nodes}
    reverse_edges = {nid: [] for nid in nodes}

    for step_id, deps in edges.items():
        for dep in deps:
            if dep in nodes:
                in_degree[step_id] = in_degree.get(step_id, 0) + 1
                reverse_edges.setdefault(dep, []).append(step_id)

    queue = [nid for nid in nodes if in_degree.get(nid, 0) == 0]
    order = []

    while queue:
        queue.sort()
        node = queue.pop(0)
        order.append(node)

        for neighbor in reverse_edges.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return order