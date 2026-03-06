# STATE.yaml — Multi-Agent Coordination Pattern

When orchestrating multiple AI coding agents working on the same codebase, the biggest failure mode is silent divergence: two agents both think they're working on the same task, or one overwrites the other's work.

The STATE.yaml pattern solves this with a single shared file that acts as a bulletin board.

---

## The Pattern

Create `STATE.yaml` at the repo root before spawning any agents:

```yaml
# STATE.yaml — Shared agent coordination state
# Last updated: 2026-03-06T01:00:00Z

phase: "Phase 7 — Networking"
status: in_progress   # pending | in_progress | blocked | complete

agents:
  agent_a:
    task: "Implement TCP socket layer"
    files_owned:
      - src/net/tcp.rs
      - src/net/socket.rs
    status: working   # idle | working | review | done | blocked
    last_update: "2026-03-06T00:45:00Z"
    notes: "Basic connect() working, working on send/recv"

  agent_b:
    task: "Implement UDP layer"
    files_owned:
      - src/net/udp.rs
    status: idle
    last_update: "2026-03-06T00:30:00Z"
    notes: ""

completed_tasks:
  - "TCP handshake implementation (agent_a, 2026-03-06T00:30:00Z)"

blockers: []

next_checkpoint: "All networking tests green"
```

---

## Rules

1. **Each agent owns a file list.** If you don't own it, you don't touch it without a handoff.
2. **Update STATE.yaml when you start and finish work.** Not periodically — at transitions.
3. **Read STATE.yaml before starting.** If another agent is `working` on an adjacent file, coordinate.
4. **Blockers go in STATE.yaml.** Don't just stop — write why, so the orchestrator sees it.
5. **Completed tasks move to `completed_tasks`.** It builds a running record without growing forever.

---

## Orchestrator Usage

```bash
# Initialize for a new phase
python3 scripts/state-orchestrator.py init \
  --phase "Phase 7 — Networking" \
  --agents agent_a agent_b agent_c

# Check current state
python3 scripts/state-orchestrator.py status

# Watch for completion (blocks until all agents done or timeout)
python3 scripts/state-orchestrator.py watch --timeout 3600
```

---

## Why This Works

- Zero inter-agent communication required. Agents don't call each other — they read a shared file.
- Survives agent restarts. State is persistent in the repo.
- Orchestrator can see the full picture at any time without querying agents.
- Git history tracks the evolution of STATE.yaml across a sprint.

---

## Limitations

- Requires agents to be well-behaved about updating the file. Enforce it in agent prompts.
- File-level ownership can be too coarse for deeply shared modules. Break into sub-components.
- Not a substitute for good CI — tests catch the merge conflicts STATE.yaml prevents.
