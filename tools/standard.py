"""Read-only advisory GitHub evidence validator; not a merge-enforcement gate."""
import argparse
import os
from pathlib import Path
from tools import checkpoint, gate, install, session

KEYS = gate.POLICY_KEYS - {"publisher_app_id"}
TEST_KEYS = {"run", "attempt", "workflow_id", "job_name", "steps"}

def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) != KEYS or type(policy["schema_version"]) is not int or policy["schema_version"] != 1:
        raise ValueError("Invalid standard policy")
    install.repo_path(policy["repository"])
    session.text(policy["product_branch"], "product branch")
    for key in ("repository_id", "pull_request"):
        gate.positive(policy[key], key)
    for key in ("source_sha", "installed_sha", "head_sha", "base_sha", "merge_sha"):
        install.require(install.SHA, policy[key], key)
    seen = set()
    for row in gate.nonempty_list(policy["issues"], "owner-approved scope"):
        if not isinstance(row, dict) or set(row) != gate.ISSUE_KEYS:
            raise ValueError("Invalid issue scope")
        n = gate.positive(row["issue"], "issue")
        if n in seen:
            raise ValueError("Duplicate issue")
        seen.add(n)
        install.require(checkpoint.VERSION, row["version"], "issue version")
        install.require(install.SHA, row["fix_sha"], "fix SHA")
        for c in gate.nonempty_list(row["criteria"], "criteria"):
            if not isinstance(c, dict) or set(c) != {"text", "checks"}:
                raise ValueError("Invalid criterion")
            session.text(c["text"], "criterion")
            for t in gate.nonempty_list(c["checks"], "checks"):
                if not isinstance(t, dict) or set(t) != TEST_KEYS:
                    raise ValueError("Invalid standard check")
                for k in ("run", "attempt", "workflow_id"):
                    gate.positive(t[k], k)
                session.text(t["job_name"], "job name")
                steps = gate.nonempty_list(t["steps"], "steps")
                if any(not isinstance(s, str) or not s.strip() for s in steps) or len(steps) != len(set(steps)):
                    raise ValueError("Invalid steps")

def check_test(api, prefix, policy, evidence, test):
    run, attempt = test["run"], test["attempt"]
    url = f"https://github.com/{policy['repository']}/actions/runs/{run}/attempts/{attempt}"
    if url not in evidence:
        raise ValueError("Run is not in staged evidence")
    root = prefix + "/actions/runs/" + str(run)
    for item in (api.request("GET", root), api.request("GET", root + "/attempts/" + str(attempt))):
        if (item.get("id") != run or item.get("run_attempt") != attempt
                or item["repository"]["id"] != policy["repository_id"]
                or item.get("workflow_id") != test["workflow_id"]
                or item.get("event") != "pull_request"
                or item.get("head_sha") not in (policy["head_sha"], policy["merge_sha"])
                or item.get("status") != "completed" or item.get("conclusion") != "success"):
            raise ValueError("Run is stale, unsuccessful or unrelated")
        # GitHub may omit the PR association. If present it must match.
        prs = item.get("pull_requests") or []
        if prs and not any(p.get("number") == policy["pull_request"]
                           and p["head"]["sha"] == policy["head_sha"]
                           and p["base"]["sha"] == policy["base_sha"] for p in prs):
            raise ValueError("Run PR association does not match")
    jobs = gate.exact_collection(api, root + "/attempts/" + str(attempt) + "/jobs?per_page=100", "jobs")
    matches = [j for j in jobs if j.get("name") == test["job_name"]]
    if len(matches) != 1 or matches[0].get("status") != "completed" or matches[0].get("conclusion") != "success":
        raise ValueError("Required job missing or unsuccessful")
    job = matches[0]
    for step in test["steps"]:
        matches = [s for s in job.get("steps", []) if s.get("name") == step]
        if len(matches) != 1 or matches[0].get("status") != "completed" or matches[0].get("conclusion") != "success":
            raise ValueError("Required step missing or unsuccessful")
    return {"run": run, "attempt": attempt, "job_id": gate.positive(job["id"], "job ID"),
            "url": url, "checkout_review_required": True}

def evaluate(api, target, policy):
    validate_policy(policy)
    meta = api.request("GET", install.repo_path(target))
    if meta.get("private") is not False or meta.get("id") != policy["repository_id"]:
        raise ValueError("Standard public-runner profile requires a public target")
    result = gate.evaluate_scope(api, target, policy, validate_policy, check_test)
    # Metadata cannot establish what arbitrary product workflows executed.
    return {**result, "status": "review_required", "check": "VIB / standard advisory",
            "mode": "standard", "published": False, "merge_enforced": False,
            "owner_review_required": True, "checkout_review_required": True}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target", required=True)
    p.add_argument("--policy", required=True, type=Path)
    args = p.parse_args()
    try:
        policy = checkpoint.parse_json(args.policy.read_text(encoding="utf-8"))
        api = install.GitHub(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
        print(install.render(evaluate(api, args.target, policy)), end="")
    except (ValueError, KeyError, TypeError, OSError, install.ApiError) as exc:
        p.exit(1, f"VIB standard validation refused: {exc}\n")

if __name__ == "__main__":
    main()
