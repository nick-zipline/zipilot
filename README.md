# zipilot

`zipilot` is an autonomous control plane for running spec-driven Codex CLI workflows with a finite-state machine.

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Run a spec:

```bash
zipilot run specs/example.yaml --approve
```

## CLI

```bash
zipilot run <spec.yaml> [--approve] [--config config.yaml]
zipilot resume [--config config.yaml]
zipilot status [--config config.yaml]
zipilot validate <spec.yaml>
zipilot tools
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
python3 -m pytest tests/ -v
```
