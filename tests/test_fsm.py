"""Tests for FSM state transitions and engine."""

import pytest

from zipilot.states import Event, State, transition, TRANSITIONS


class TestTransitions:
    """Test the transition table."""

    def test_idle_to_spec_creation(self):
        assert transition(State.IDLE, Event.SPEC_LOADED) == State.SPEC_CREATION

    def test_spec_creation_to_executing(self):
        assert transition(State.SPEC_CREATION, Event.APPROVED) == State.EXECUTING

    def test_executing_to_verifying(self):
        assert transition(State.EXECUTING, Event.STEP_DONE) == State.VERIFYING

    def test_executing_to_blocked(self):
        assert transition(State.EXECUTING, Event.ERROR) == State.BLOCKED

    def test_executing_to_context_handoff(self):
        assert transition(State.EXECUTING, Event.CONTEXT_HIGH) == State.CONTEXT_HANDOFF

    def test_verifying_to_completed(self):
        assert transition(State.VERIFYING, Event.ALL_PASSED) == State.COMPLETED

    def test_verifying_to_blocked(self):
        assert transition(State.VERIFYING, Event.SOME_FAILED) == State.BLOCKED

    def test_blocked_to_recovering(self):
        assert transition(State.BLOCKED, Event.TOOL) == State.RECOVERING

    def test_blocked_to_needs_input(self):
        assert transition(State.BLOCKED, Event.MAX_RETRIES) == State.NEEDS_INPUT

    def test_recovering_to_executing(self):
        assert transition(State.RECOVERING, Event.RECOVERED) == State.EXECUTING

    def test_recovering_to_blocked(self):
        assert transition(State.RECOVERING, Event.ERROR) == State.BLOCKED

    def test_needs_input_to_executing(self):
        assert transition(State.NEEDS_INPUT, Event.INPUT_RECEIVED) == State.EXECUTING

    def test_context_handoff_to_executing(self):
        assert transition(State.CONTEXT_HANDOFF, Event.HANDOFF_COMPLETE) == State.EXECUTING

    def test_invalid_transition_raises(self):
        with pytest.raises(ValueError, match="Invalid transition"):
            transition(State.IDLE, Event.APPROVED)

    def test_completed_has_no_outgoing(self):
        """COMPLETED is terminal — no events should transition out of it."""
        for event in Event:
            assert (State.COMPLETED, event) not in TRANSITIONS


class TestTransitionCoverage:
    """Ensure every defined transition is reachable."""

    def test_all_transitions_valid(self):
        for (state, event), target in TRANSITIONS.items():
            assert isinstance(state, State)
            assert isinstance(event, Event)
            assert isinstance(target, State)

    def test_all_non_terminal_states_have_outgoing(self):
        non_terminal = {s for s in State if s != State.COMPLETED}
        states_with_outgoing = {s for s, _ in TRANSITIONS}
        assert non_terminal <= states_with_outgoing


class TestFSMEngine:
    """Integration tests for FSMEngine with mocked Codex sessions."""

    def _make_spec(self):
        from zipilot.spec import load_spec_str
        return load_spec_str("""\
version: 1
goal: "test"
steps:
  - id: s1
    description: "do thing"
exit_conditions:
  - type: command
    command: "true"
""")

    def _make_engine(self, spec=None, auto_approve=True, sessions_dir="/tmp"):
        from zipilot.config import Config
        from zipilot.fsm import FSMEngine
        from zipilot.tools.registry import ToolRegistry

        spec = spec or self._make_spec()
        config = Config(sessions_dir=sessions_dir)
        registry = ToolRegistry()
        return FSMEngine(
            spec=spec,
            config=config,
            registry=registry,
            auto_approve=auto_approve,
        )

    def test_engine_initial_state(self, tmp_path):
        engine = self._make_engine(sessions_dir=str(tmp_path))
        assert engine.ctx.state == State.IDLE

    def test_engine_transitions_to_spec_creation(self, tmp_path):
        from zipilot.states import Event
        engine = self._make_engine(sessions_dir=str(tmp_path))
        engine._emit(Event.SPEC_LOADED)
        assert engine.ctx.state == State.SPEC_CREATION

    def test_engine_approve_transitions_to_executing(self, tmp_path):
        from zipilot.states import Event
        engine = self._make_engine(sessions_dir=str(tmp_path))
        engine._emit(Event.SPEC_LOADED)
        engine._emit(Event.APPROVED)
        assert engine.ctx.state == State.EXECUTING
