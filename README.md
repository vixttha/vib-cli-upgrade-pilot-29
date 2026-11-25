# Version-Issues-Hub

Standalone VIB branch. Do not merge into the product. Start with [AGENTS.md](AGENTS.md) and [WORKFLOW.md](WORKFLOW.md). The installed package pin and target identity are in [vib-install.json](vib-install.json). The [ledger](ledger/issue-versions.json) and [handoff](ledger/handoff.json) belong to the adopter.

Run `python -m tools.session --target OWNER/REPO inspect` in an authorized cloud host to recover the current state. Merge enforcement is disabled unless separately configured and proven by the owner.

Optional owner-enabled gate: [GATE.md](GATE.md). The candidate is not live enforcement until the disposable ruleset pilot passes.

**Default: Standard VIB.** No GitHub App, rulesets, branch protection, database or external service required. Use [STANDARD.md](STANDARD.md) to review product evidence. Optional hard enforcement is separate and does not block ordinary adoption.
