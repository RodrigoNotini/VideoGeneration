"""Deterministic Phase 0 graph skeleton."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents import (
    article_extractor,
    image_generator,
    relevance_ranker,
    rss_collector,
    script_validator,
    script_writer,
    tts_generator,
    video_renderer,
)
from agents.reporter import Reporter, run as reporter_agent_run
from core.state import PipelineState, assert_state_contract, copy_state

try:
    from langgraph.graph import END, StateGraph

    HAS_LANGGRAPH = True
except ModuleNotFoundError:
    END = "__END__"
    HAS_LANGGRAPH = False


NodeFn = Callable[[PipelineState], PipelineState]


NODE_FLOW: tuple[tuple[str, NodeFn], ...] = (
    ("rss_collector", rss_collector.run),
    ("relevance_ranker", relevance_ranker.run),
    ("article_extractor", article_extractor.run),
    ("script_writer", script_writer.run),
    ("script_validator", script_validator.run),
    ("image_generator", image_generator.run),
    ("tts_generator", tts_generator.run),
    ("video_renderer", video_renderer.run),
    ("reporter", reporter_agent_run),
)


class _FallbackCompiledGraph:
    """Minimal deterministic graph fallback when langgraph is unavailable."""

    def __init__(self, nodes: list[tuple[str, NodeFn]]) -> None:
        self._nodes = nodes

    def invoke(self, state: PipelineState) -> PipelineState:
        next_state = copy_state(state)
        for _, node in self._nodes:
            next_state = node(next_state)
        return next_state


def _wrap_node(stage_name: str, node_fn: NodeFn, reporter: Reporter) -> NodeFn:
    def _wrapped(state: PipelineState) -> PipelineState:
        reporter.stage_started(stage_name)
        updated_state = node_fn(copy_state(state))
        assert_state_contract(updated_state)
        reporter.stage_finished(stage_name, note="phase0_stub")
        return reporter.sync_state_metrics(updated_state)

    return _wrapped


def build_graph(reporter: Reporter) -> Any:
    wrapped_nodes = [(_name, _wrap_node(_name, _fn, reporter)) for _name, _fn in NODE_FLOW]

    if not HAS_LANGGRAPH:
        return _FallbackCompiledGraph(wrapped_nodes)

    graph = StateGraph(PipelineState)
    for node_name, node_fn in wrapped_nodes:
        graph.add_node(node_name, node_fn)

    graph.set_entry_point(wrapped_nodes[0][0])
    for index in range(len(wrapped_nodes) - 1):
        graph.add_edge(wrapped_nodes[index][0], wrapped_nodes[index + 1][0])
    graph.add_edge(wrapped_nodes[-1][0], END)

    return graph.compile()


def run_pipeline(initial_state: PipelineState, reporter: Reporter) -> PipelineState:
    assert_state_contract(initial_state)
    compiled = build_graph(reporter)
    final_state = compiled.invoke(copy_state(initial_state))
    assert_state_contract(final_state)
    return final_state
