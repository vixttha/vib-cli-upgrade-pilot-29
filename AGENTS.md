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
