# Pull request

**What this changes** — one or two sentences.

**Why** — the problem being solved, not the diff.

**How it was verified** — paste the actual command and its real output
(`bash scripts/ci_local.sh` and the relevant `--selftest` run). A summary
line without the output is not verification.

## Checklist

- [ ] `bash scripts/ci_local.sh` passes (all 7 checks)
- [ ] `bash scripts/gate_selftest.sh` passes (the redaction gate still catches planted samples)
- [ ] No new absolute home paths, private addresses, tokens, cookies or account identifiers
- [ ] New behaviour has a `--selftest` case, or an explicit note why not
