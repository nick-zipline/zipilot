"""Tests for session-scoped persistence helpers."""

from __future__ import annotations

from zipilot.persistence import (
    PersistedState,
    _slugify,
    cleanup_sessions,
    complete_session,
    create_session,
    list_sessions,
    load_session,
    load_state,
    save_state,
)


def _ps(session_id: str, *, completed: bool = False, state: str = "EXECUTING") -> PersistedState:
    return PersistedState(
        session_id=session_id,
        state=state,
        spec_path="/tmp/spec.yaml",
        step_index=1,
        retry_count=0,
        session_history=[],
        continuation_context="ctx",
        completed=completed,
    )


def test_create_session_creates_dir_and_active(tmp_path):
    session_id = create_session("Fix flaky test", tmp_path)

    assert (tmp_path / session_id).is_dir()
    assert (tmp_path / ".active").read_text(encoding="utf-8") == session_id
    assert session_id.endswith("_fix-flaky-test")


def test_save_and_load_state_round_trip(tmp_path):
    session_id = create_session("Add auth", tmp_path)
    original = _ps(session_id)

    save_state(original, tmp_path)
    loaded = load_state(tmp_path)

    assert loaded is not None
    assert loaded.session_id == original.session_id
    assert loaded.state == original.state
    assert loaded.spec_path == original.spec_path
    assert loaded.step_index == original.step_index
    assert loaded.continuation_context == original.continuation_context
    assert loaded.completed is False


def test_complete_session_marks_completed_and_clears_active(tmp_path):
    session_id = create_session("Complete me", tmp_path)
    save_state(_ps(session_id), tmp_path)

    complete_session(tmp_path)

    active = load_state(tmp_path)
    completed = load_session(session_id, tmp_path)

    assert active is None
    assert completed is not None
    assert completed.completed is True
    assert (tmp_path / ".active").read_text(encoding="utf-8") == ""


def test_cleanup_sessions_prunes_oldest_completed(tmp_path):
    session_ids = [f"20260215_12010{i}_session-{i}" for i in range(5)]
    for session_id in session_ids:
        save_state(_ps(session_id, completed=True, state="COMPLETED"), tmp_path)

    cleanup_sessions(tmp_path, max_sessions=2)

    remaining = sorted([p.name for p in tmp_path.iterdir() if p.is_dir()])
    assert remaining == session_ids[-2:]


def test_list_sessions_returns_sorted_results(tmp_path):
    save_state(_ps("20260215_120102_c"), tmp_path)
    save_state(_ps("20260215_120101_b"), tmp_path)
    save_state(_ps("20260215_120100_a"), tmp_path)

    sessions = list_sessions(tmp_path)

    assert [sid for sid, _ in sessions] == [
        "20260215_120100_a",
        "20260215_120101_b",
        "20260215_120102_c",
    ]


def test_slugify_edge_cases():
    assert _slugify("  Hello, World!  ") == "hello-world"
    assert _slugify("___") == "session"
    assert _slugify("A" * 40) == "a" * 30
