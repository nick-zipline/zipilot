"""FSM states and transition table for zipilot."""

from __future__ import annotations

from enum import Enum, auto


class State(Enum):
    IDLE = auto()
    SPEC_CREATION = auto()
    EXECUTING = auto()
    VERIFYING = auto()
    BLOCKED = auto()
    RECOVERING = auto()
    NEEDS_INPUT = auto()
    CONTEXT_HANDOFF = auto()
    COMPLETED = auto()


class Event(Enum):
    SPEC_LOADED = auto()
    APPROVED = auto()
    STEP_DONE = auto()
    ALL_PASSED = auto()
    SOME_FAILED = auto()
    ERROR = auto()
    TOOL = auto()
    RECOVERED = auto()
    MAX_RETRIES = auto()
    INPUT_RECEIVED = auto()
    CONTEXT_HIGH = auto()
    HANDOFF_COMPLETE = auto()


# (current_state, event) -> next_state
TRANSITIONS: dict[tuple[State, Event], State] = {
    (State.IDLE, Event.SPEC_LOADED): State.SPEC_CREATION,
    (State.SPEC_CREATION, Event.APPROVED): State.EXECUTING,
    (State.EXECUTING, Event.STEP_DONE): State.VERIFYING,
    (State.EXECUTING, Event.ERROR): State.BLOCKED,
    (State.EXECUTING, Event.CONTEXT_HIGH): State.CONTEXT_HANDOFF,
    (State.VERIFYING, Event.ALL_PASSED): State.COMPLETED,
    (State.VERIFYING, Event.SOME_FAILED): State.BLOCKED,
    (State.BLOCKED, Event.TOOL): State.RECOVERING,
    (State.BLOCKED, Event.MAX_RETRIES): State.NEEDS_INPUT,
    (State.RECOVERING, Event.RECOVERED): State.EXECUTING,
    (State.RECOVERING, Event.ERROR): State.BLOCKED,
    (State.NEEDS_INPUT, Event.INPUT_RECEIVED): State.EXECUTING,
    (State.CONTEXT_HANDOFF, Event.HANDOFF_COMPLETE): State.EXECUTING,
}


def transition(current: State, event: Event) -> State:
    """Return the next state for a given (state, event) pair.

    Raises ValueError if the transition is not allowed.
    """
    key = (current, event)
    if key not in TRANSITIONS:
        raise ValueError(
            f"Invalid transition: {current.name} + {event.name}"
        )
    return TRANSITIONS[key]
