# Selection & Redaction Principles

How this repository decides **what to publish** and **how to de-identify it**.
This is a technical document about publishing hygiene — it intentionally
describes *method*, not *purpose*.

---

## 1. What gets published

Only artifacts that satisfy all three criteria:

| # | Criterion | Why |
|---|---|---|
| 1 | **General-purpose** — the capability is useful to someone outside the author's personal workflow | A repo of one-off helpers is not a repo; it is a dump |
| 2 | **Self-contained** — runs on the Python standard library (plus explicitly-noted OS frameworks), no pip install, no cloud account, no API key | Reviewers must be able to clone and run it in one command |
| 3 | **No credential handling** — does not read, store, decrypt, refresh, or transmit login state, cookies, tokens, or session material | Credential tooling is legally and contractually distinct from ordinary utilities |

Everything else stays private. The author keeps a much larger private library;
this repository is the subset that passes the three gates above.

---

## 2. What is explicitly excluded

The following categories are excluded **by policy, not by accident**:

- **Credential and session tooling** — anything that reads or refreshes login
  state, decrypts stored cookies, or switches between accounts.
- **Automated account registration** — scripts that create accounts on
  third-party platforms on the user's behalf. Technically feasible on many
  sites; prohibited by their terms of service, and wrong regardless.
- **Anything that requires a phone number, real-name verification, or payment
  method to be exercised** — such flows must involve the human directly.
- **Content-specific scraping of a single creator's feed.** The extraction
  *tooling* is generic (see `tools/`); the *harvested output* is personal data
  and never enters the repository.

---

## 3. Redaction rules

Applied by `scripts/stage_public.py` at copy time, and re-verified by
`scripts/check_sensitive.sh` before every push (and again in CI).

| Class | Detected as | Replaced with |
|---|---|---|
| Absolute local paths | `/Users/<name>/…` | `${HOME}/…` |
| Interpreter / tool paths | `${PYTHON}`, `${WORKSPACE}`, `${WB_BIN}` | `${…}` placeholders |
| Project codenames | vault name, account login | `your-vault`, `your-username` |
| Private network addresses | `192.168.x.y` | `192.168.x.x` |
| Tailnet hostnames | `*.ts.net` | `your-tailnet-host.ts.net` |
| Real credentials | `sk-…`, `ghp_…`, `AIza…`, `xai-…`, `AKIA…` | *(removed — not translatable)* |
| Assigned secrets | `password/secret/api_key/token = "…"` | *(removed)* |
| Session material | `cookie/session/jwt/authorization = "…"` | *(removed)* |
| Personal e-mail addresses | any address not on the allowlist | *(removed)* |
| Filenames that imply credentials | `*.keys`, `*token*`, `*machine-identity*` | *(excluded from copy)* |

### Two rules that are easy to get wrong

**Backup files escape suffix-based rules.** A file named
`SKILL.md.bak-before-20261112` does not end in `.md`, so a rule that only
rewrites `.md`/`.py`/`.sh` will skip it — and the backup contains exactly the
old text you were trying to remove. Exclude by pattern, not by extension.

**The gate must be tested in both directions.** "The clean tree passes" proves
nothing; only "a planted sample of every class is caught" proves the gate can
detect. Both directions are exercised whenever a rule changes.

---

## 4. What the gate cannot check

`check_sensitive.sh` matches *strings*. It cannot tell whether a sentence
reveals intent. Two classes therefore need human review, and the author
reviews both before any push:

1. **Framing** — a README that states *why* the project exists can make a
   legitimate tool look like something else. Describe capability, not purpose.
2. **Vocabulary** — a technical term can be innocuous in one domain and
   pointed in another. The gate has a configurable word list for this; words
   that carry a specific connotation in a given ecosystem are kept out of
   public-facing prose.

The gate is a floor, not a ceiling.

---

## 5. Pre-push sequence

```
stage_public.py          # copy + redact (idempotent, originals untouched)
check_sensitive.sh       # verify — must pass
selftest on each tool    # de-redaction must not break behavior
git commit
publish_github.sh        # runs the gate again, then pushes
```

CI re-runs steps 2–3 on every push, so a rule added later is enforced
retroactively against the whole tree.

---

## 6. License and independence

MIT. No third-party Python packages. The only external dependencies are
system components (macOS Vision framework, `swiftc`, CPython) licensed
separately by their respective owners.

This project is independent and is **not** affiliated with, endorsed by, or
sponsored by any company whose products appear in it. See `NOTICE`.
