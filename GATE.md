# Owner-enabled VIB merge gate — candidate

Standard VIB is the default. This optional feature is not a prerequisite to install, validate, or use VIB. See [STANDARD.md](STANDARD.md).

## Trust boundary

The standalone branch contains the gate implementation; installing it does **not** modify any product branch or enable enforcement. A repository owner can opt in using a dedicated trusted GitHub App check source. Do not trust a green status from the candidate's own mutable workflow. The privileged verifier must run reviewed, pinned VIB code and an owner-pinned policy outside candidate control. It reads GitHub metadata only and never checks out or executes the PR with the publisher credential.

The policy is an explicit per-PR owner-approved snapshot, not a file provided by the candidate. It pins repository ID/name, product branch, package SHA, installed state SHA, PR/head/base/proposed merge SHA, publisher App ID, and the complete staged issue scope. Each accepted criterion is mapped to independently trusted checks on the exact proposed merge commit. Every check must originate from the owner-selected App (not the GitHub Actions App), refer to the recorded run/attempt URL, and match the approved workflow ID, executor revision, job and required successful steps. A trusted executor can run on standard public GitHub-hosted runners and publish a separate test check that attests the exact product checkout. The gate does not infer product acceptance from VIB unit tests.

## Policy shape

Each field is required; replace placeholders with the real approved values, never trust candidate input. Example structure:

```json
{"schema_version":1,"repository":"OWNER/REPO","repository_id":42,"product_branch":"main","source_sha":"FULL_SHA","installed_sha":"FULL_SHA","pull_request":12,"head_sha":"FULL_SHA","base_sha":"FULL_SHA","merge_sha":"FULL_SHA","publisher_app_id":123,"issues":[{"issue":7,"version":"v0.0.0.1","fix_sha":"FULL_SHA","criteria":[{"text":"Exact accepted criterion","checks":[{"name":"Trusted product acceptance","app_id":456,"workflow_id":789,"run_head_sha":"FULL_SHA","job_name":"test","steps":["Run acceptance"]}]}]}]}
```

```sh
python -m tools.gate --target OWNER/REPO --policy /trusted/owner-policy.json
# Only a protected trusted execution host with an owner-selected App token:
python -m tools.gate --target OWNER/REPO --policy /trusted/owner-policy.json --publish
```

The first command is read-only. The second creates a pending check, verifies its App identity and readback, then re-evaluates before publishing success on the proposed merge SHA. A failed preflight creates no check; later failure can leave a pending check that blocks merging. Existing trusted checks are refused rather than shadowed. GH_TOKEN is the read credential; VIB_CHECK_TOKEN is the separately scoped, short-lived App installation token with Checks write permission. Never expose either in candidate code or chat. Publish only from a trusted protected workflow/host. An owner must protect the policy, installed state and verifier from candidate writes; pin each policy snapshot. Do not assume an agent's assertion of owner approval is a security control.

## Live pilot required before enforcement

1. Use a disposable public repository or target branch and a trusted App installed only where needed. Prepare a protected executor and independent test attestation. Do not put its private key in an ordinary candidate-accessible workflow or execute untrusted product code with credentials.
2. Publish the exact check once to the proposed merge SHA. In repository rulesets, target the disposable product branch, require PRs, require the exact check `VIB / merge gate` **from the selected App**, strict up-to-date checks, and no unintended bypass. Protect the dedicated VIB branch/policy. Keep product-side bootstrap minimal and opt-in; do not merge the VIB branch into product.
3. Verify ruleset readback, check source, and actual GitHub merge eligibility. Positive: a compliant PR can merge. Negative: missing mapping, incomplete scope, missing/forged source, stale candidate/base/attempt, skipped job/step, and a candidate trying to publish its own verdict must be blocked. Exercise state revocation: if a sealed scope/evidence decision changes after success, invalidate the old verdict or require a new product candidate before allowing merge.
4. Reconcile staged issues after the actual owner-approved merge. Only then consider adoption of hard enforcement outside the pilot. Standard VIB adoption does not depend on this pilot. Fork PRs, merge queues, automatic trusted publisher deployment, revocation automation, and cross-repository test runners are not yet supported by this candidate. Same-repository PRs and same-repository Actions evidence only.

Public-runner unit tests use simulated APIs; they do not prove GitHub branch protection. GitHub Apps and ruleset administration are owner-controlled and separate from installing the branch.

References: [rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets), [required status check troubleshooting](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks), [check runs](https://docs.github.com/en/rest/checks/runs), [secure pull_request_target](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target).

## Sealed verdict and revocation

A successful verdict remains attached to a Git SHA even when external state changes. The trusted owner-controlled executor MUST serialize all gate publication, revocation, policy/ledger edits and merge decisions; GitHub does not make these separate API operations atomic. Never mutate a sealed policy or its evidence while a green verdict is mergeable. First freeze merging, revoke the exact old check using the original policy and check ID, verify GitHub actually blocks the PR, then make the change and require a new candidate SHA. Keep merge frozen if revocation fails or readback is ambiguous. Do not automatically retry an uncertain publication or create duplicate checks on the same candidate. A trusted App token must be scoped to the pilot repository and not available to product code.

```sh
python -m tools.gate --target OWNER/REPO --policy /trusted/original-policy.json --revoke-check CHECK_ID
```

The revocation command verifies repository identity and the original external ID, updates that exact trusted check to failure, and reads it back. It intentionally works after the installed state changes, as emergency remediation. This does not prove live GitHub re-evaluation; include a post-green revocation negative control in the disposable pilot. No live merge enforcement may be advertised until that control passes.

### Post-publication evidence confirmation (#34)

After attempting the success PATCH, the publisher re-evaluates the complete policy and compares the exact proof identities (including check ID, run/attempt and job ID) to the pre-publication result, then confirms the unique published check. On changed evidence, failed readback or uncertain success response it attempts identity-checked revocation of the same check; it never retries the success PATCH. An unconfirmed revocation requires an immediate merge freeze and owner investigation. These are bounded observations, not atomic enforcement: GitHub may expose green before revocation or evidence may change after the last read. The trusted executor must serialize reruns, test-attestation updates, policy/state writes, publication, revocation and merge decisions; the disposable live negative control remains mandatory. Standard remains independent and enforcement disabled by default.
