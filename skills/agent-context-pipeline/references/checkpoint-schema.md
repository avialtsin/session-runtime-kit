# Checkpoint schema

## Contents

- Purpose
- Required fields
- Freshness semantics
- Quality rules

## Purpose

A checkpoint freezes the meaning of a meaningful product/architecture state.
It is not a replacement for Git and not a manually maintained copy of the whole
architecture.

## Required fields

- **Identity**: timestamp, baseline, HEAD, branch.
- **Product intent**: the requirement that motivated the work at that time.
- **Current outcome**: what the system actually does now.
- **Architecture delta**: changed boundaries, ownership, contracts, or flows.
- **Decisions and invariants**: principles later work must preserve.
- **Rejected or failed approaches**: what was tried or considered and why it was
  not retained.
- **Verification evidence**: exact commands and outcomes.
- **Open questions and risks**: remaining uncertainty.
- **Git evidence**: commits, changed files, diffstat.
- **Content-addressed evidence**: SHA-256 of every anchored changed file.

## Freshness semantics

Every file anchor is evaluated independently:

- `FRESH`: current bytes equal the checkpoint hash.
- `STALE`: current bytes differ.
- `missing`: the anchored file no longer exists.

A stale checkpoint remains valid historical evidence. It is not current
structural truth.

Narrative architecture freshness uses the code snapshot declared near the top of
`ARCHITECTURE.md`. Documentation-only commits do not stale it; code or
configuration changes do.

## Quality rules

- Record actual outcome separately from original intent.
- Explain business reason, not only implementation mechanics.
- State invalidation conditions when known.
- Prefer stable IDs, contracts, and named boundaries over line numbers.
- Record failed attempts before their rationale disappears.
- Never claim a test passed unless it was run in the recorded state.
- Create a successor checkpoint after material evolution; never rewrite history.
