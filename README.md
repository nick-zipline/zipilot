# zipilot

`zipilot` is an autonomous control plane for running spec-driven Codex CLI workflows.

## Quickstart

```bash
uv sync
```

Run a spec:

```bash
uv run zipilot run specs/example.yaml --approve
```

If `uv` is not installed, fallback:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
zipilot run specs/example.yaml --approve
```

## CLI

```bash
uv run zipilot run <spec.yaml> [--approve] [--config config.yaml]
uv run zipilot resume [--config config.yaml]
uv run zipilot status [--config config.yaml]
uv run zipilot validate <spec.yaml>
uv run zipilot tools
uv run zipilot spec [output.yaml] [--with-codex]
```

## Repo Structure

```text
zipilot/
├── config.yaml              # default runtime config
├── specs/                   # example/input workflow specs
├── tests/                   # unit/integration tests
├── zipilot/
│   ├── cli.py               # CLI entrypoint and commands
│   ├── config.py            # config model + loader
│   ├── fsm.py               # FSM engine and state handlers
│   ├── persistence.py       # session-scoped state persistence
│   ├── session.py           # codex exec wrapper
│   ├── spec.py              # spec parsing/validation
│   ├── states.py            # FSM states, events, transitions
│   └── tools/               # recovery/verification tools
└── sessions/                # runtime session state (gitignored)
```

## FSM States and Transitions

`zipilot` runs as a finite state machine defined in `zipilot/states.py` and driven by handlers in `zipilot/fsm.py`.

### States

- `IDLE`: Engine initialized; no work started yet.
- `SPEC_CREATION`: Spec is displayed and awaiting approval.
- `EXECUTING`: Running workflow steps through Codex.
- `VERIFYING`: Running all exit conditions after steps are done.
- `BLOCKED`: A step or verification failed; deciding how to recover.
- `RECOVERING`: Running a recovery tool selected from the tool registry.
- `NEEDS_INPUT`: Waiting for user guidance after retries/tool recovery are exhausted.
- `CONTEXT_HANDOFF`: Context window is high; summarizing and resetting session context.
- `COMPLETED`: Terminal success/exit state.

### Transition Table

| From | Event | To | Triggered when |
|---|---|---|---|
| `IDLE` | `SPEC_LOADED` | `SPEC_CREATION` | `run()` starts and loads the parsed spec. |
| `SPEC_CREATION` | `APPROVED` | `EXECUTING` | User (or `--approve`) accepts the spec. |
| `EXECUTING` | `STEP_DONE` | `VERIFYING` | All workflow steps have completed. |
| `EXECUTING` | `ERROR` | `BLOCKED` | Current step run fails. |
| `EXECUTING` | `CONTEXT_HIGH` | `CONTEXT_HANDOFF` | Context tracker signals handoff threshold reached. |
| `VERIFYING` | `ALL_PASSED` | `COMPLETED` | Every exit condition passes. |
| `VERIFYING` | `SOME_FAILED` | `BLOCKED` | One or more exit conditions fail. |
| `BLOCKED` | `TOOL` | `RECOVERING` | A matching recovery tool is found. |
| `BLOCKED` | `MAX_RETRIES` | `NEEDS_INPUT` | Retry limit exceeded or no tool available. |
| `RECOVERING` | `RECOVERED` | `EXECUTING` | Recovery tool succeeds. |
| `RECOVERING` | `ERROR` | `BLOCKED` | Recovery tool fails (or no tool at execution time). |
| `NEEDS_INPUT` | `INPUT_RECEIVED` | `EXECUTING` | User provides guidance and continues. |
| `CONTEXT_HANDOFF` | `HANDOFF_COMPLETE` | `EXECUTING` | Handoff context reset completes. |

### Terminal Behavior

- `COMPLETED` is terminal in the transition table (no outgoing transitions).
- `run()` returns when state is `COMPLETED` or `NEEDS_INPUT`.
- In `NEEDS_INPUT`, entering `abort` sets state directly to `COMPLETED` (without an FSM event).

## Session Persistence

State is stored per session under `sessions/` (gitignored):

```text
sessions/
├── .active
└── <session_id>/state.json
```

- `session_id` format: `YYYYMMDD_HHMMSS_<goal-slug>`
- `.active` points to the active session (empty/no file means no active session)
- completed sessions are retained
- on completion, active session is marked `completed: true` and `.active` is cleared
- oldest completed sessions are pruned beyond `max_sessions`

## Config

Default config file: `config.yaml`

Key fields:
- `working_directory`
- `model`
- `max_retries`
- `sessions_dir` (default: `sessions`)
- `max_sessions` (default: `10`)

## Tests

```bash
uv run pytest tests/ -v
```
