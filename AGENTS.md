# VIB installed-branch agent entry point

This is the owner-approved standalone Version-Issues-Hub workflow, not a product source branch and not Provenistem.

1. Confirm the owner-authorized target repository, its exact dedicated branch `Version-Issues-Hub`, and the current branch SHA. Read vib-install.json, WORKFLOW.md, ledger/issue-versions.json, and ledger/handoff.json at that SHA. Verify package provenance and identity; if the manifest or branch is wrong, stop.
2. Read the actual GitHub issue (not a PR). Head must accept scope and acceptance criteria before assignment. Never allocate another issue while a handoff is active. Never change historical issue mappings.
3. Follow Head → Worker → Validate → Internal Auditor → Head, one active Worker. Only edit product code on the authorized product work branch, not the VIB branch. Do not merge the VIB branch into the product.
4. Use the cooperative handoff commands in WORKFLOW.md. Re-read the branch after a stale write; no force updates. Record candidate/fix SHAs, exact public-runner attempts and acceptance results. A green VIB schema test is not product acceptance.
5. Owner approval controls integration and release. Instructions are cooperative; only a separately enabled and proven GitHub gate can enforce a merge rule. Never claim the gate is active based on this file.

If the host has no shell, use its GitHub read tools to open the exact branch and the files above. Read the current handoff first. A host without authorized GitHub write capability cannot claim or advance work. Do not ask for secrets in chat.

## Owner-locked interoperability

**VIB remains independent of Provenistem. They are designed to work together as complementary parts of the same puzzle.** See [owner decision](https://github.com/vixttha/Version-Issues-Hub/issues/9). VIB owns issue-version assignments, workflow, revision-bound verification and integration decisions; Provenistem owns its ideas/features and progress records. Optional read-only references may connect them; either must operate independently. Never infer write authorization, install one from the other, merge template branches, or require the other as a dependency. Changes to interoperability require explicit owner approval.

## Default operating mode

Use Standard VIB and [STANDARD.md](STANDARD.md). Never require a GitHub App, branch protection or a separate repository to adopt VIB. Stop on invalid or stale evidence, return to Head, and require owner review of exact checkout logs and acceptance results before integration. Standard validation cannot prevent GitHub merges. Optional hard enforcement requires a separate owner decision and live proof.

## Owner-approved engineering ecosystem laws

Decision ENGINEERING-ECOSYSTEM-LAWS-001 (2026-11-24). Mission: Build a toolkit that gives compatible LLM agents a structured engineering environment, rather than building another AI model.

<!-- engineering-laws:start -->
1. **Independence:** Every tool must remain independently installable, usable, versioned and maintained.
2. **Composability:** Installing additional tools expands the agent's capabilities without creating mandatory dependencies.
3. **Portability:** No mandatory AI provider, model, agent harness or paid execution service.
4. **Persistence:** Project knowledge, decisions, progress and evidence survive the termination of an AI session.
5. **Verification:** Development claims must be supported by reproducible, revision-bound evidence.
6. **Owner authority:** The human retains control over scope, architectural decisions, integration and releases.
<!-- engineering-laws:end -->

Remain standalone. Other tools are optional; their absence must not block normal operation. Preserve project-local authority and evidence; never automatically install, merge, synchronize or write to another tool's branch. No mandatory seventh coordinator, paid service or AI provider. Existing product scope remains authoritative. Change these laws only by explicit owner approval.

## Agent capability discovery — v1

<!-- agent-capability:v1
{"schema_version":1,"id":"vib","branch":"Version-Issues-Hub","entrypoint":"AGENTS.md","scope":"Issue-version checkpoints, handoffs and owner decisions","required_ecosystem_tools":[],"authority":"owner","discovery":"read-only","versioning":"independent"}
-->

A compatible agent may enumerate the target repository's branch refs, resolve this branch to a commit, and read its entry point at that exact commit. Read-only discovery confers no execution, write, integration or release authority. If this branch or marker is absent, the tool is absent or legacy/unverified; continue independently. Check the target's own instructions and owner scope before any action. The marker describes a capability, not a required registry or package version.

Installation convention: human or agent selects a reviewed exact source SHA, checks the dedicated target branch and collisions, follows this product's own installation guide, and records source/target SHA and preserved product state. Never merge a template workspace wholesale into product source. Existing installations require explicit reviewed upgrades; preserve local records and prior receipts. Optional evidence links do not authorize writes. No mandatory coordinator or other ecosystem tool.
