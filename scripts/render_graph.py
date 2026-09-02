"""Regenerate the LangGraph structure diagram used in docs/design.md.

Prints Mermaid syntax derived directly from the real compiled StateGraph
(nodes, edges, conditional routing) — never hand-maintained, so it can't
drift out of sync with graph.py the way a hand-drawn diagram could. No
live credentials needed: the provider and BigQuery tool are only
referenced to build the graph, never actually called, since only the
graph's structure (not its behavior) is being rendered.

Usage:
    uv run python scripts/render_graph.py
"""

from unittest.mock import MagicMock

from retail_agent.graph import build_graph


def main() -> None:
    graph = build_graph(MagicMock(), MagicMock(), MagicMock(), "render-graph-placeholder", system_instruction="")
    print(graph.get_graph().draw_mermaid())


if __name__ == "__main__":
    main()
