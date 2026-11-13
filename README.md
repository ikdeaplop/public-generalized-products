# public-generalized-products

A personal collection of **general-purpose tools and agent workflow skills**, built and maintained by one author since 2019.

Everything here is MIT-licensed, has no runtime dependency outside the Python standard library (plus macOS system frameworks where noted), and is designed to run standalone — clone, read, use, fork.

> This repository is an independent personal project. It is not affiliated with, endorsed by, or sponsored by Anthropic, OpenAI, GitHub, JetBrains, or any other company. All trademarks are the property of their respective owners.

---

## What is here

### `tools/` — executable utilities

| Tool | What it does |
|---|---|
| `douyin-video-text-extract/douyin_video_text.py` | Extract on-screen text and card content from short-video frames using **macOS Vision OCR** — no model download, no cloud call, single self-contained file. Includes card-layout reconstruction (title vs. description detection, column clustering, line-merge repair) and a same-character homophone correction map. `--selftest` runs 24 assertions. |
| `build-actionable-list.py` | Turn a list of extracted "platform → benefit" cards into an **actionable, threshold-tiered checklist** (tier L0–L4 by verification burden: no gate / account / ID / payment method / professional credentials). Includes platform-name normalization, link tables, and an "alternative path" map. |
| `script-version-keeper.py` | Version manager for a personal script library: freeze snapshots, reconcile registrations, detect drift between a script and its recorded version. `--selftest` runs 24 assertions. |
| `script-start-gate.py` | Pre-flight gate: refuse to run a script whose registered version does not match the file on disk. |
| `gateway-tool-priority-chain.py` | Configure a priority chain of model/tool endpoints with automatic fallback. |

### `skills/` — portable agent skills

Each directory is a self-contained skill with a `SKILL.md`. They are written to be **agent-agnostic** — copy a directory into your agent's skills folder and it works.

| Category | Skills |
|---|---|
| **Verification guards** | `verify-real-outcome` (catches silent no-ops), `verify-end-state` (catches false-success signals), `silent-failure-rootcause` (root-cause tracing for "no error but no effect") |
| **Unblocking playbooks** | `rejected-task-fallback` (decision tree for blocked tasks), `unblock-interception` (interception method), `codex-task-dispatch` (delegate to a headless CLI worker) |
| **macOS troubleshooting** | `macos-disk-space-triage`, `macos-network-drop-triage`, `macos-input-hijack-hunt`, `macos-settings-panes-repair`, `macos-vision-subject-crop`, `macos-python-app-launch-fix` |
| **Skill & context management** | `skill-curator` (triage skills by real usage telemetry), `skill-verifier` (admission exam for skills), `token-saver` (semantics-preserving text compression) |
| **Content extraction** | `douyin-video-transcribe` (full workflow around the OCR extractor) |
| **Self-improvement loops** | `kb-evolve-meta`, `self-evolution-loop` |

### `scripts/` — repository hygiene

| Script | Purpose |
|---|---|
| `check_sensitive.sh` | **Pre-publish redaction gate.** Scans the tree for real keys, hardcoded secrets, cookies, private keys, absolute home paths, private IPs, tailnet hostnames, real emails, account identifiers, and motivation/framing vocabulary that could make the repository read as something it isn't. Exits non-zero on any hit. Runs in CI. |
| `gate_selftest.sh` | **Proves the gate works.** Plants one sample per rule class and asserts that each rule reports *that specific file* — plus a clean-tree pass and a whitelist check. A gate that only ever sees a clean tree is untested. Runs in CI. |
| `stage_public.py` | Copies artifacts from a local machine into this repo with **rule-based, idempotent redaction**. Copies rather than moves; the source is never modified. |
| `publish_github.sh` | One-command publish: runs the gate and its self-test, creates the repo if needed, pushes, verifies. Never force-pushes. |

### `docs/` — methodology

`docs/Selection-and-Redaction-Principles.md` documents what gets published and what
never does, how redaction is applied and verified, and the two classes of problem a
string-matching gate cannot catch on its own.

---

## Quick start

```bash
# Extract text from a video's frames (macOS 12+, Python 3.9+)
python3 tools/douyin-video-text-extract/douyin_video_text.py --help

# Verify a tool still works
python3 tools/douyin-video-text-extract/douyin_video_text.py --selftest
python3 tools/script-version-keeper.py --selftest

# Before publishing a change: confirm nothing sensitive slipped in
bash scripts/check_sensitive.sh

# ...and confirm the gate itself still works (13 planted samples, per rule)
bash scripts/gate_selftest.sh

# Publish (runs both of the above first, then creates + pushes)
REPO_SLUG=your-name/your-repo bash scripts/publish_github.sh
```

## Requirements

- macOS 12 or later (the OCR tool uses the system Vision framework via `swiftc`)
- Python 3.9 or later
- No third-party Python packages required

## License

MIT — see [LICENSE](LICENSE). Trademarks and third-party names remain the property of their respective owners.
