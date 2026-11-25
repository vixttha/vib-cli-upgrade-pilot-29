"""Evaluate an owner-pinned VIB merge policy; optionally publish from a trusted GitHub App."""
import argparse
import os
import re
from pathlib import Path
from urllib.parse import quote
from tools import checkpoint, install, session

CHECK = "VIB / merge gate"
POLICY_KEYS = {"schema_version", "repository", "repository_id", "product_branch",
               "source_sha", "installed_sha", "pull_request", "head_sha", "base_sha",
               "merge_sha", "publisher_app_id", "issues"}
ISSUE_KEYS = {"issue", "version", "fix_sha", "criteria"}
TEST_KEYS = {"name", "app_id", "workflow_id", "run_head_sha", "job_name", "steps"}

def positive(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f"Invalid {name}")
    return value

def nonempty_list(value, name):
    if not isinstance(value, list) or not value:
        raise ValueError(f"Missing {name}")
    return value

def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS or type(policy["schema_version"]) is not int or policy["schema_version"] != 1:
        raise ValueError("Invalid gate policy schema")
    install.repo_path(policy["repository"])
    session.text(policy["product_branch"], "product branch")
    for key in ("repository_id", "pull_request", "publisher_app_id"):
        positive(policy[key], key)
    for key in ("source_sha", "installed_sha", "head_sha", "base_sha", "merge_sha"):
        install.require(install.SHA, policy[key], key)
    seen = set()
    for row in nonempty_list(policy["issues"], "owner-approved issue scope"):
        if not isinstance(row, dict) or set(row) != ISSUE_KEYS:
            raise ValueError("Invalid issue scope row")
        number = positive(row["issue"], "issue")
        if number in seen:
            raise ValueError("Duplicate issue in policy")
        seen.add(number)
        install.require(checkpoint.VERSION, row["version"], "issue version")
        install.require(install.SHA, row["fix_sha"], "fix revision")
        for criterion in nonempty_list(row["criteria"], "criterion mappings"):
            if not isinstance(criterion, dict) or set(criterion) != {"text", "checks"}:
                raise ValueError("Invalid criterion mapping")
            session.text(criterion["text"], "criterion")
            for test in nonempty_list(criterion["checks"], "criterion checks"):
                if not isinstance(test, dict) or set(test) != TEST_KEYS:
                    raise ValueError("Invalid trusted check mapping")
                if session.text(test["name"], "check name") == CHECK:
                    raise ValueError("Gate cannot attest itself")
                positive(test["app_id"], "test App ID")
                positive(test["workflow_id"], "workflow ID")
                install.require(install.SHA, test["run_head_sha"], "trusted run head")
                session.text(test["job_name"], "required job name")
                steps = nonempty_list(test["steps"], "required job steps")
                if any(not isinstance(step, str) or not step.strip() for step in steps) or len(set(steps)) != len(steps):
                    raise ValueError("Invalid required steps")

def exact_collection(api, path, key):
    value = api.request("GET", path)
    if not isinstance(value, dict):
        raise ValueError("Invalid GitHub collection; gate fails closed")
    rows = value.get(key)
    count = value.get("total_count")
    if type(count) is not int or count < 0 or not isinstance(rows, list) or count != len(rows):
        raise ValueError("Incomplete GitHub collection; gate fails closed")
    return rows

def assert_current_pr(api, prefix, policy):
    pr = api.request("GET", prefix + "/pulls/" + str(policy["pull_request"]))
    if (pr.get("number") != policy["pull_request"] or pr.get("state") != "open"
            or pr.get("merged") is not False or pr["base"]["ref"] != policy["product_branch"]
            or pr["base"]["repo"]["id"] != policy["repository_id"]
            or pr["head"]["repo"]["id"] != policy["repository_id"]
            or pr["head"]["sha"] != policy["head_sha"] or pr["base"]["sha"] != policy["base_sha"]
            or pr.get("merge_commit_sha") != policy["merge_sha"]):
        raise ValueError("PR identity, head, base or merge candidate is stale or unsupported")
    if install.read_ref(api, prefix, policy["product_branch"]) != policy["base_sha"]:
        raise ValueError("Product base changed")
    merge = api.request("GET", prefix + "/git/commits/" + policy["merge_sha"])
    if merge.get("sha") != policy["merge_sha"] or [p["sha"] for p in merge["parents"]] != [policy["base_sha"], policy["head_sha"]]:
        raise ValueError("Proposed merge has incorrect parents")
    return pr

def check_ancestry(api, prefix, fix, head):
    comparison = api.request("GET", prefix + "/compare/" + fix + "..." + head)
    if comparison.get("status") not in ("ahead", "identical") or comparison["merge_base_commit"]["sha"] != fix:
        raise ValueError("Issue fix is not an ancestor of the PR head")

def check_test(api, prefix, policy, evidence, test):
    url = prefix + "/commits/" + policy["merge_sha"] + "/check-runs?filter=latest&per_page=100&check_name=" + quote(test["name"], safe="")
    rows = exact_collection(api, url, "check_runs")
    matches = [r for r in rows if r.get("name") == test["name"] and (r.get("app") or {}).get("id") == test["app_id"]]
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous independently published check")
    row = matches[0]
    app = row["app"]
    if app.get("slug") in (None, "github-actions"):
        raise ValueError("Candidate-writable GitHub Actions check source is not trusted")
    if row.get("head_sha") != policy["merge_sha"] or row.get("status") != "completed" or row.get("conclusion") != "success":
        raise ValueError("Trusted check is stale, incomplete or unsuccessful")
    details = row.get("details_url")
    if details not in evidence:
        raise ValueError("Trusted check does not reference staged issue evidence")
    # The exact link was already checked against the target repo by session validation.
    m = re.fullmatch(r"https://github\.com/" + re.escape(policy["repository"]) +
                     r"/actions/runs/([1-9][0-9]*)/attempts/([1-9][0-9]*)", details, re.I)
    if not m:
        raise ValueError("Invalid exact run attempt")
    run, attempt = map(int, m.groups())
    root = prefix + "/actions/runs/" + str(run)
    latest = api.request("GET", root)
    exact = api.request("GET", root + "/attempts/" + str(attempt))
    for item in (latest, exact):
        if (item.get("id") != run or item.get("run_attempt") != attempt
                or item["repository"]["id"] != policy["repository_id"]
                or item.get("workflow_id") != test["workflow_id"]
                or item.get("head_sha") != test["run_head_sha"]
                or item.get("status") != "completed" or item.get("conclusion") != "success"):
            raise ValueError("Workflow run attempt is stale, untrusted or unsuccessful")
    jobs = exact_collection(api, root + "/attempts/" + str(attempt) + "/jobs?per_page=100", "jobs")
    matches = [j for j in jobs if j.get("name") == test["job_name"]]
    if len(matches) != 1 or matches[0].get("status") != "completed" or matches[0].get("conclusion") != "success":
        raise ValueError("Required job is missing, skipped or failed")
    job = matches[0]
    for step in test["steps"]:
        selected = [s for s in job.get("steps", []) if s.get("name") == step]
        if len(selected) != 1 or selected[0].get("status") != "completed" or selected[0].get("conclusion") != "success":
            raise ValueError("Required test step is missing, skipped or failed")
    return {"check_id": positive(row["id"], "check ID"), "app_id": test["app_id"],
            "run": run, "attempt": attempt, "job_id": positive(job["id"], "job ID"), "url": details}

def evaluate(api, target, policy):
    return evaluate_scope(api, target, policy, validate_policy, check_test)

def evaluate_scope(api, target, policy, validate, verify_test):
    validate(policy)
    if target.lower() != policy["repository"].lower():
        raise ValueError("Policy repository mismatch")
    state = session.load(api, target, policy["installed_sha"])
    manifest, ledger, handoff = state["manifest"], state["ledger"], state["handoff"]
    if (manifest["target_repository_id"] != policy["repository_id"]
            or manifest["target_repository"] != policy["repository"]
            or manifest["product_branch"] != policy["product_branch"]
            or manifest["source_sha"] != policy["source_sha"]
            or state["product_sha"] != policy["base_sha"]):
        raise ValueError("Owner policy does not match installed state")
    if handoff["active"] is not None:
        raise ValueError("Return every active handoff to Head before gate evaluation")
    prefix = state["prefix"]
    assert_current_pr(api, prefix, policy)
    latest = {r["handoff"]["issue"]: r for r in handoff["history"]}
    staged_for_pr = {n for n, r in latest.items() if r["disposition"] == "staged"
                     and r["handoff"]["pull_request"] == policy["pull_request"]}
    scope = {r["issue"] for r in policy["issues"]}
    if scope != staged_for_pr:
        raise ValueError("Owner scope must match all staged issues for this PR")
    records = {r["issue"]: r for r in ledger["issues"]}
    results = []
    for row in policy["issues"]:
        n = row["issue"]
        record = records.get(n)
        if record is None or record["state"] != "staged" or record["version"] != row["version"]:
            raise ValueError("Missing or mismatched staged issue-version mapping")
        if session.get_issue(api, state, n).get("state") != "open":
            raise ValueError("Expected an open GitHub issue")
        active = latest[n]["handoff"]
        if (active["version"] != row["version"] or active["fix_sha"] != row["fix_sha"]
                or active["candidate_sha"] != policy["head_sha"] or active["pull_request"] != policy["pull_request"]):
            raise ValueError("Issue fix or staged PR candidate does not match policy")
        if [c["text"] for c in row["criteria"]] != record["acceptance"]:
            raise ValueError("Every accepted criterion must be mapped exactly")
        check_ancestry(api, prefix, row["fix_sha"], policy["head_sha"])
        evidence = session.evidence_links(ledger["repository"], active["evidence"])
        proofs = []
        for c in row["criteria"]:
            proofs.append({"criterion": c["text"], "checks": [
                verify_test(api, prefix, policy, evidence, test) for test in c["checks"]
            ]})
        results.append({"issue": n, "version": row["version"], "fix_sha": row["fix_sha"], "criteria": proofs})
    if install.read_ref(api, prefix, install.BRANCH) != policy["installed_sha"]:
        raise ValueError("Installed state advanced during gate evaluation")
    assert_current_pr(api, prefix, policy)
    return {"status": "pass", "check": CHECK, "repository": policy["repository"],
            "pull_request": policy["pull_request"], "source_sha": policy["source_sha"],
            "installed_sha": policy["installed_sha"], "head_sha": policy["head_sha"],
            "base_sha": policy["base_sha"], "tested_sha": policy["merge_sha"],
            "issues": results, "published": False}

def external_id(policy):
    return f"vib:{policy['repository_id']}:{policy['pull_request']}:{policy['installed_sha']}:{policy['merge_sha']}"

def assert_gate_identity(check, policy, *, expected_id=None):
    app = check.get("app") or {}
    if (check.get("name") != CHECK or check.get("head_sha") != policy["merge_sha"]
            or check.get("external_id") != external_id(policy)
            or app.get("id") != policy["publisher_app_id"]
            or app.get("slug") in (None, "github-actions")):
        raise ValueError("Published check has wrong identity; do not rely on it")
    check_id = positive(check["id"], "published check ID")
    if expected_id is not None and check_id != expected_id:
        raise ValueError("Wrong check run ID")
    return check_id

def matching_gate_checks(api, prefix, policy):
    path = prefix + "/commits/" + policy["merge_sha"] + "/check-runs?filter=all&per_page=100&check_name=" + quote(CHECK, safe="")
    return [r for r in exact_collection(api, path, "check_runs")
            if r.get("name") == CHECK and (r.get("app") or {}).get("id") == policy["publisher_app_id"]]

def publish(api, publisher, target, policy):
    verdict = evaluate(api, target, policy)
    prefix = install.repo_path(target)
    # A prior verdict must never be shadowed by a duplicate green check.
    if matching_gate_checks(api, prefix, policy):
        raise ValueError("Existing trusted gate check; revoke and use a new candidate")
    pending = publisher.request("POST", prefix + "/check-runs", {
        "name": CHECK, "head_sha": policy["merge_sha"], "status": "in_progress",
        "external_id": external_id(policy),
        "output": {"title": "VIB gate evaluating", "summary": "No successful verdict yet."},
    })
    check_id = assert_gate_identity(pending, policy)
    path = prefix + "/check-runs/" + str(check_id)
    observed = api.request("GET", path)
    assert_gate_identity(observed, policy, expected_id=check_id)
    if observed.get("status") != "in_progress" or observed.get("conclusion") is not None:
        raise ValueError("Expected a non-success pending gate check")
    rows = matching_gate_checks(api, prefix, policy)
    if len(rows) != 1 or rows[0].get("id") != check_id:
        raise ValueError("Ambiguous trusted gate check; do not publish success")
    # Re-evaluate after creating the pending check. The trusted executor must
    # serialize publication, revocation, and policy/state writes through merge.
    verdict = evaluate(api, target, policy)
    # Once success publication is attempted, even a transport failure may mean
    # GitHub accepted it. Never retry that success write. Confirm exact evidence
    # again and invalidate the same check if confirmation fails.
    try:
        updated = publisher.request("PATCH", path, {
            "status": "completed", "conclusion": "success",
            "output": {"title": "VIB gate passed", "summary":
                f"Owner-pinned state {policy['installed_sha']}; tested merge {policy['merge_sha']}; "
                f"{len(verdict['issues'])} staged issue(s)."},
        })
        for check in (updated, api.request("GET", path)):
            assert_gate_identity(check, policy, expected_id=check_id)
            if check.get("status") != "completed" or check.get("conclusion") != "success":
                raise ValueError("Gate success was not confirmed")
        confirmed = evaluate(api, target, policy)
        if confirmed != verdict:
            raise ValueError("Bound evidence changed during publication")
        rows = matching_gate_checks(api, prefix, policy)
        if len(rows) != 1 or rows[0].get("id") != check_id:
            raise ValueError("Ambiguous trusted gate check after publication")
        assert_gate_identity(rows[0], policy, expected_id=check_id)
        if rows[0].get("status") != "completed" or rows[0].get("conclusion") != "success":
            raise ValueError("Published gate changed during confirmation")
    except Exception as exc:
        # Revocation independently verifies the original check identity.
        # An unsuccessful or ambiguous revocation requires owner intervention.
        try:
            revoke(api, publisher, target, policy, check_id)
        except Exception as revoke_error:
            raise ValueError(
                f"Gate publication uncertain; FREEZE MERGES; revocation of check {check_id} "
                "not confirmed. Inspect original policy and check; do not retry publication."
            ) from revoke_error
        raise ValueError(f"Gate publication invalidated; check {check_id} revoked: {exc}") from exc
    return {**verdict, "published": True, "check_id": check_id}


def revoke(api, publisher, target, policy, check_id):
    """Revoke an exact previously published verdict, including an old state pin."""
    validate_policy(policy)
    if target.lower() != policy["repository"].lower():
        raise ValueError("Policy repository mismatch")
    prefix = install.repo_path(target)
    meta = api.request("GET", prefix)
    if meta.get("id") != policy["repository_id"] or meta.get("full_name") != policy["repository"]:
        raise ValueError("Revocation repository identity mismatch")
    path = prefix + "/check-runs/" + str(positive(check_id, "check ID"))
    assert_gate_identity(api.request("GET", path), policy, expected_id=check_id)
    updated = publisher.request("PATCH", path, {
        "status": "completed", "conclusion": "failure",
        "output": {"title": "VIB gate revoked", "summary": "Owner invalidated this sealed verdict. New candidate required."},
    })
    for check in (updated, api.request("GET", path)):
        assert_gate_identity(check, policy, expected_id=check_id)
        if check.get("status") != "completed" or check.get("conclusion") != "failure":
            raise ValueError("Gate revocation was not confirmed")
    rows = matching_gate_checks(api, prefix, policy)
    if len(rows) != 1 or rows[0].get("id") != check_id or rows[0].get("conclusion") != "failure":
        raise ValueError("Revocation ambiguous; freeze merges and inspect all checks")
    return {"status": "revoked", "check_id": check_id, "tested_sha": policy["merge_sha"]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target", required=True)
    p.add_argument("--policy", type=Path, required=True, help="Owner-approved policy outside PR control")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--publish", action="store_true")
    mode.add_argument("--revoke-check", type=int, help="Revoke exact prior check using its original owner policy")
    args = p.parse_args()
    api = install.GitHub(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    try:
        policy = checkpoint.parse_json(args.policy.read_text(encoding="utf-8"))
        if args.publish or args.revoke_check is not None:
            token = os.environ.get("VIB_CHECK_TOKEN")
            if not token:
                raise ValueError("Trusted GitHub App installation credential required")
            publisher = install.GitHub(token)
            verdict = (publish(api, publisher, args.target, policy) if args.publish else
                       revoke(api, publisher, args.target, policy, args.revoke_check))
        else:
            verdict = evaluate(api, args.target, policy)
        print(install.render(verdict), end="")
    except (ValueError, KeyError, TypeError, OSError, install.ApiError) as exc:
        p.exit(1, f"VIB gate refused: {exc}\n")

if __name__ == "__main__":
    main()
