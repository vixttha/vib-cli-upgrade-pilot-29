"""Bootstrap VIB from an installed branch; serialize cooperative handoffs on GitHub."""
import argparse
import copy
import hashlib
import json
import os
import re
from tools import checkpoint, install

PHASES = ("Worker", "Validate", "Internal Auditor", "Head")
ACTIVE_KEYS = {"issue", "version", "phase", "next_action", "fix_sha",
               "candidate_sha", "pull_request", "evidence"}
HANDOFF = "ledger/handoff.json"
LEDGER = "ledger/issue-versions.json"

def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing {label}")
    return value

def issue_number(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid GitHub issue or PR number")
    return value

def evidence_links(repo, links):
    pattern = re.compile(r"https://github\.com/" + re.escape(repo) +
                         r"/actions/runs/[1-9][0-9]*/attempts/[1-9][0-9]*", re.I)
    if not isinstance(links, list) or any(not isinstance(x, str) or not pattern.fullmatch(x) for x in links):
        raise ValueError("Use exact repository GitHub Actions run-attempt URLs")
    if len(set(links)) != len(links):
        raise ValueError("Duplicate evidence link")
    return links

def validate_active(active, ledger):
    if not isinstance(active, dict) or set(active) != ACTIVE_KEYS:
        raise ValueError("Invalid active handoff schema")
    issue_number(active["issue"])
    if checkpoint.bindings(ledger).get(active["issue"]) != active["version"]:
        raise ValueError("Active issue mapping does not match ledger")
    if active["phase"] not in PHASES:
        raise ValueError("Invalid handoff phase")
    text(active["next_action"], "next action")
    fields = [active["fix_sha"], active["candidate_sha"], active["pull_request"]]
    if any(v is not None for v in fields):
        install.require(install.SHA, fields[0], "fix revision")
        install.require(install.SHA, fields[1], "candidate revision")
        issue_number(fields[2])
    if active["phase"] in ("Validate", "Internal Auditor") and any(v is None for v in fields):
        raise ValueError("Verification handoff requires a fix, candidate and PR")
    evidence_links(ledger["repository"], active["evidence"])
    if active["phase"] == "Internal Auditor" and not active["evidence"]:
        raise ValueError("Auditor handoff requires evidence references")
    if active["phase"] == "Worker" and (any(v is not None for v in fields) or active["evidence"]):
        raise ValueError("Worker handoff must not carry stale verification")
    if active["evidence"] and any(v is None for v in fields):
        raise ValueError("Evidence requires a candidate")

def validate_handoff(handoff, ledger):
    if not isinstance(handoff, dict) or set(handoff) != {"schema_version", "active", "history"}:
        raise ValueError("Invalid handoff schema")
    if type(handoff["schema_version"]) is not int or handoff["schema_version"] != 1 or not isinstance(handoff["history"], list):
        raise ValueError("Invalid handoff version or history")
    checkpoint.bindings(ledger)
    in_progress = {row["issue"] for row in ledger["issues"] if row["state"] == "in_progress"}
    if handoff["active"] is not None:
        validate_active(handoff["active"], ledger)
        if in_progress != {handoff["active"]["issue"]}:
            raise ValueError("Active issue must be the sole in-progress row; owner reconciliation required")
    elif in_progress:
        raise ValueError("Orphan in-progress issue without active handoff; owner reconciliation required")
    for record in handoff["history"]:
        if not isinstance(record, dict) or set(record) != {"handoff", "disposition", "decision", "merged_sha"}:
            raise ValueError("Invalid archived handoff")
        validate_active(record["handoff"], ledger)
        if record["disposition"] not in ("blocked", "deferred", "staged", "integrated", "rework") or (record["disposition"] != "rework" and record["handoff"]["phase"] != "Head"):
            raise ValueError("Invalid archived disposition")
        text(record["decision"], "owner decision")
        if record["disposition"] in ("staged", "integrated"):
            archived = record["handoff"]
            if any(archived[key] is None for key in ("fix_sha", "candidate_sha", "pull_request")) or not archived["evidence"]:
                raise ValueError("Archived staged/integrated handoff requires a fix, candidate, PR and evidence")
        if record["disposition"] == "integrated":
            install.require(install.SHA, record["merged_sha"], "merged revision")
        elif record["merged_sha"] is not None:
            raise ValueError("Non-integrated handoff has merged revision")

def load(api, target, expected_sha=None):
    prefix = install.repo_path(target)
    meta = api.request("GET", prefix)
    if meta.get("full_name", "").lower() != target.lower() or type(meta.get("id")) is not int:
        raise ValueError("Target repository identity mismatch")
    ref = install.read_ref(api, prefix, install.BRANCH)
    if ref is None:
        raise ValueError("VIB is not installed")
    if expected_sha is not None and ref != install.require(install.SHA, expected_sha, "expected installed SHA"):
        raise ValueError("Installed branch has advanced; reload before acting")
    def read(path):
        return install.read_text(api, prefix, path, ref)
    manifest = checkpoint.parse_json(read("vib-install.json"))
    if (manifest.get("schema_version") != 1 or manifest.get("package") != install.BRANCH
            or manifest.get("source_repository") != install.SOURCE
            or manifest.get("target_repository") != meta["full_name"]
            or manifest.get("target_repository_id") != meta["id"]
            or manifest.get("install_branch") != install.BRANCH
            or manifest.get("product_branch") != meta["default_branch"]
            or meta["default_branch"] == install.BRANCH):
        raise ValueError("Installed manifest does not match target identity or product branch")
    baseline = install.require(install.BASELINE, manifest["baseline_version"], "baseline")
    major, minor, patch = checkpoint.version_number(baseline)
    if manifest["target_version"] != f"v{major}.{minor}.{patch + 1}":
        raise ValueError("Manifest target version does not match baseline")
    install.require(install.SHA, manifest["product_baseline_sha"], "starting product revision")
    source = install.require(install.SHA, manifest["source_sha"], "source revision")
    expected_files = install.source_files(api, source)
    digests = {p: hashlib.sha256(s.encode()).hexdigest() for p, s in expected_files.items()}
    if manifest["package_files_sha256"] != digests or any(read(p) != s for p, s in expected_files.items()):
        raise ValueError("Installed package does not match pinned source")
    ledger = checkpoint.parse_json(read(LEDGER))
    checkpoint.bindings(ledger)
    if ledger["repository"] != meta["full_name"]:
        raise ValueError("Ledger belongs to another repository")
    handoff = checkpoint.parse_json(read(HANDOFF))
    validate_handoff(handoff, ledger)
    product = install.read_ref(api, prefix, meta["default_branch"])
    if product is None:
        raise ValueError("Product branch is missing")
    return {"prefix": prefix, "ref": ref, "manifest": manifest,
            "ledger": ledger, "handoff": handoff, "product_sha": product}

def get_issue(api, state, number):
    value = api.request("GET", state["prefix"] + "/issues/" + str(issue_number(number)))
    if value.get("number") != number or "pull_request" in value:
        raise ValueError("Expected an actual GitHub issue, not a PR")
    return value

def get_pr(api, state, active):
    pr = api.request("GET", state["prefix"] + "/pulls/" + str(issue_number(active["pull_request"])))
    if (pr.get("number") != active["pull_request"]
            or pr["base"]["ref"] != state["manifest"]["product_branch"]
            or pr["base"]["repo"]["id"] != state["manifest"]["target_repository_id"]
            or pr["head"]["sha"] != active["candidate_sha"]):
        raise ValueError("PR candidate or target changed; return to Worker and reverify")
    return pr

def require_open_pr(api, state, active):
    pr = get_pr(api, state, active)
    if pr.get("merged") is True:
        raise ValueError("PR is merged; return to Head for owner reconciliation")
    if pr.get("state") != "open" or pr.get("merged") is not False:
        raise ValueError("PR is not open and unmerged; return to Head or rework")
    return pr

def inspect(api, state, requested=None):
    active = state["handoff"]["active"]
    issue = requested if requested is not None else (active["issue"] if active else None)
    info = None
    if issue is not None:
        item = get_issue(api, state, issue)
        info = {"number": issue, "title": item.get("title"), "state": item.get("state"),
                "version": checkpoint.bindings(state["ledger"]).get(issue)}
    status = "head_scope_required" if active is None else "resume"
    if active and issue != active["issue"]:
        status = "blocked_other_active_issue"
    if active and status == "resume":
        if active["pull_request"] is not None:
            try:
                pr = get_pr(api, state, active)
            except ValueError:
                status = "stale_candidate"
            else:
                if pr.get("merged") is True and pr.get("state") == "closed":
                    status = "merged_reconciliation_required"
                elif pr.get("state") == "closed" and pr.get("merged") is False:
                    status = "closed_pr_rework_required"
                elif pr.get("state") != "open" or pr.get("merged") is not False:
                    status = "pr_state_unverified"
        if status == "resume" and info["state"] != "open":
            status = "issue_reconciliation_required"
    return {"status": status, "installed_sha": state["ref"], "product_sha": state["product_sha"],
            "repository": state["ledger"]["repository"], "baseline": state["manifest"]["baseline_version"],
            "target": state["manifest"]["target_version"], "issue": info, "active": active,
            "owner_merge_required": True, "evidence_verified": False}

def claim(api, state, number, criteria, note, *, resume_prior_line=False):
    validate_handoff(state["handoff"], state["ledger"])
    item = get_issue(api, state, number)
    if item.get("state") != "open":
        raise ValueError("Only an open GitHub issue can be claimed")
    ledger = copy.deepcopy(state["ledger"])
    handoff = copy.deepcopy(state["handoff"])
    active = handoff["active"]
    if active is not None:
        if active["issue"] != number:
            raise ValueError("Another issue owns the active handoff")
        record = next(x for x in ledger["issues"] if x["issue"] == number)
        if criteria and criteria != record["acceptance"]:
            raise ValueError("Existing acceptance criteria differ")
        return ledger, handoff
    baseline = state["manifest"]["baseline_version"]
    row = next((x for x in ledger["issues"] if x["issue"] == number), None)
    if row is not None and row["state"] in ("integrated", "retired"):
        raise ValueError("Historical issue cannot be reclaimed")
    if row is not None and row["version"].rsplit(".", 1)[0] != baseline:
        prior_line = row["version"].rsplit(".", 1)[0]
        if (checkpoint.version_number(prior_line) >= checkpoint.version_number(baseline)
                or row["state"] not in ("assigned", "blocked", "deferred")):
            raise ValueError("Issue is not eligible for prior-line recovery; owner reconciliation required")
        if not resume_prior_line:
            raise ValueError("Prior-line issue requires explicit owner-approved --resume-prior-line")
        version = row["version"]
    else:
        if resume_prior_line:
            raise ValueError("Prior-line recovery requires an existing eligible historical issue")
        version, _ = checkpoint.register(ledger, number, baseline, criteria)
        row = next(x for x in ledger["issues"] if x["issue"] == number)
    if criteria and criteria != row["acceptance"]:
        raise ValueError("Existing acceptance criteria differ")
    row["state"] = "in_progress"
    handoff["active"] = {"issue": number, "version": version, "phase": "Worker",
                         "next_action": text(note, "next action"), "fix_sha": None,
                         "candidate_sha": None, "pull_request": None, "evidence": []}
    return ledger, handoff

def advance(api, state, note, *, fix=None, candidate=None, pr=None, evidence=None,
            rework=False, return_to_head=False):
    validate_handoff(state["handoff"], state["ledger"])
    ledger = copy.deepcopy(state["ledger"])
    handoff = copy.deepcopy(state["handoff"])
    active = handoff["active"]
    if not active:
        raise ValueError("No active handoff")
    active["next_action"] = text(note, "next action")
    phase = active["phase"]
    if rework and return_to_head:
        raise ValueError("Choose one transition")
    if rework:
        if phase == "Worker":
            raise ValueError("Already in Worker phase")
        if get_issue(api, state, active["issue"]).get("state") != "open":
            raise ValueError("Issue is not open; return to Head for owner reconciliation")
        if active["pull_request"] is not None:
            # Rework is allowed for stale or closed-unmerged PRs, never for a
            # merged PR (including a changed head) or an unverified PR state.
            existing = api.request("GET", state["prefix"] + "/pulls/" +
                                   str(issue_number(active["pull_request"])))
            if (existing.get("number") != active["pull_request"]
                    or existing.get("merged") is not False
                    or existing.get("state") not in ("open", "closed")):
                raise ValueError("PR is merged or state is unverified; return to Head for owner reconciliation")
        previous = copy.deepcopy(state["handoff"]["active"])
        handoff["history"].append({"handoff": previous, "disposition": "rework",
                                   "decision": note, "merged_sha": None})
        active.update(phase="Worker", fix_sha=None, candidate_sha=None, pull_request=None, evidence=[])
    elif return_to_head:
        if phase == "Head":
            raise ValueError("Already at Head")
        active["phase"] = "Head"
    elif get_issue(api, state, active["issue"]).get("state") != "open":
        raise ValueError("Issue is not open; return to Head for owner reconciliation")
    elif phase == "Worker":
        active.update(phase="Validate", fix_sha=fix, candidate_sha=candidate, pull_request=pr, evidence=[])
        validate_active(active, ledger)
        require_open_pr(api, state, active)
    elif phase == "Validate":
        require_open_pr(api, state, active)
        active.update(phase="Internal Auditor", evidence=evidence_links(ledger["repository"], evidence or []))
    elif phase == "Internal Auditor":
        require_open_pr(api, state, active)
        active["phase"] = "Head"
    else:
        raise ValueError("Head must decide or return to Worker")
    validate_handoff(handoff, ledger)
    return ledger, handoff

def finish(api, state, disposition, decision):
    validate_handoff(state["handoff"], state["ledger"])
    ledger = copy.deepcopy(state["ledger"])
    handoff = copy.deepcopy(state["handoff"])
    active = handoff["active"]
    if active is None or active["phase"] != "Head":
        raise ValueError("Return to Head before deciding")
    if disposition not in ("blocked", "deferred", "staged", "integrated"):
        raise ValueError("Invalid disposition")
    merged = None
    if disposition in ("staged", "integrated"):
        if not active["evidence"]:
            raise ValueError("Integration requires recorded verification evidence")
        pr = get_pr(api, state, active)
        if disposition == "staged":
            if pr.get("state") != "open" or pr.get("merged") is not False:
                raise ValueError("Staging requires an open unmerged PR")
        else:
            if pr.get("state") != "closed" or pr.get("merged") is not True:
                raise ValueError("GitHub does not report a closed, merged PR")
            merged = install.require(install.SHA, pr["merge_commit_sha"], "merged revision")
    handoff["history"].append({"handoff": active, "disposition": disposition,
                               "decision": text(decision, "owner decision"), "merged_sha": merged})
    next(x for x in ledger["issues"] if x["issue"] == active["issue"])["state"] = disposition
    handoff["active"] = None
    return ledger, handoff

def reconcile(api, state, number, decision):
    validate_handoff(state["handoff"], state["ledger"])
    ledger = copy.deepcopy(state["ledger"])
    handoff = copy.deepcopy(state["handoff"])
    if handoff["active"] is not None:
        raise ValueError("Finish the active handoff before integration reconciliation")
    row = next((x for x in ledger["issues"] if x["issue"] == issue_number(number)), None)
    if row is None or row["state"] != "staged":
        raise ValueError("Integration reconciliation requires a staged issue")
    prior = next((x for x in reversed(handoff["history"]) if x["handoff"]["issue"] == number), None)
    if prior is None or prior["disposition"] != "staged":
        raise ValueError("Missing latest staged handoff")
    active = copy.deepcopy(prior["handoff"])
    pr = get_pr(api, state, active)
    if pr.get("state") != "closed" or pr.get("merged") is not True:
        raise ValueError("GitHub does not report a closed, merged PR")
    handoff["history"].append({"handoff": active, "disposition": "integrated",
        "decision": text(decision, "owner decision"), "merged_sha": install.require(
            install.SHA, pr["merge_commit_sha"], "merged revision")})
    row["state"] = "integrated"
    return ledger, handoff

def save(api, state, ledger, handoff, apply=False):
    validate_handoff(state["handoff"], state["ledger"])
    checkpoint.check_history(ledger, state["ledger"])
    validate_handoff(handoff, ledger)
    if handoff["history"][:len(state["handoff"]["history"])] != state["handoff"]["history"]:
        raise ValueError("Historical handoff changed")
    if ledger == state["ledger"] and handoff == state["handoff"]:
        return {"status": "unchanged", "installed_sha": state["ref"]}
    if not apply:
        return {"status": "planned", "ledger": ledger, "handoff": handoff}
    prefix, old = state["prefix"], state["ref"]
    if install.read_ref(api, prefix, install.BRANCH) != old:
        raise ValueError("Installed branch advanced; reload")
    commit = api.request("GET", prefix + "/git/commits/" + old)
    if commit.get("sha") != old:
        raise ValueError("Wrong installed commit")
    tree = api.request("POST", prefix + "/git/trees", {
        "base_tree": install.require(install.SHA, commit["tree"]["sha"], "installed tree"),
        "tree": [{"path": p, "type": "blob", "mode": "100644", "content": install.render(v)}
                 for p, v in ((LEDGER, ledger), (HANDOFF, handoff))],
    })["sha"]
    new = api.request("POST", prefix + "/git/commits", {"message": "VIB cooperative handoff",
        "tree": install.require(install.SHA, tree, "handoff tree"), "parents": [old]})["sha"]
    install.require(install.SHA, new, "handoff commit")
    # Fast-forward-only CAS: a divergent writer cannot overwrite a competing handoff.
    api.request("PATCH", prefix + "/git/refs/heads/" + install.BRANCH, {"sha": new, "force": False})
    install.wait_ref(api, prefix, install.BRANCH, new, old)
    return {"status": "saved", "installed_sha": new}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target", required=True)
    p.add_argument("--expected-sha")
    sub = p.add_subparsers(dest="command", required=True)
    status = sub.add_parser("inspect")
    status.add_argument("--issue", type=int)
    c = sub.add_parser("claim")
    c.add_argument("issue", type=int)
    c.add_argument("--acceptance", action="append", default=[])
    c.add_argument("--note", required=True)
    c.add_argument("--owner-approved", action="store_true")
    c.add_argument("--resume-prior-line", action="store_true", help="Resume an eligible unresolved historical checkpoint without renumbering")
    a = sub.add_parser("advance")
    a.add_argument("--note", required=True)
    a.add_argument("--fix")
    a.add_argument("--candidate")
    a.add_argument("--pr", type=int)
    a.add_argument("--evidence", action="append", default=[])
    a.add_argument("--rework", action="store_true")
    a.add_argument("--return-to-head", action="store_true")
    f = sub.add_parser("finish")
    f.add_argument("disposition", choices=["blocked", "deferred", "staged", "integrated"])
    f.add_argument("--decision", required=True)
    f.add_argument("--owner-approved", action="store_true")
    i = sub.add_parser("reconcile")
    i.add_argument("issue", type=int)
    i.add_argument("--decision", required=True)
    i.add_argument("--owner-approved", action="store_true")
    for command in (c, a, f, i):
        command.add_argument("--apply", action="store_true")
    args = p.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    api = install.GitHub(token)
    try:
        state = load(api, args.target, args.expected_sha)
        if args.command == "inspect":
            result = inspect(api, state, args.issue)
        else:
            if args.apply and (not token or not args.expected_sha):
                raise ValueError("Apply requires a credential and exact expected installed SHA")
            if args.command in ("claim", "finish", "reconcile") and not args.owner_approved:
                raise ValueError("Explicit owner approval is required")
            if args.command == "claim":
                ledger, handoff = claim(api, state, args.issue, args.acceptance, args.note,
                    resume_prior_line=args.resume_prior_line)
            elif args.command == "advance":
                ledger, handoff = advance(api, state, args.note, fix=args.fix, candidate=args.candidate,
                    pr=args.pr, evidence=args.evidence, rework=args.rework, return_to_head=args.return_to_head)
            elif args.command == "finish":
                ledger, handoff = finish(api, state, args.disposition, args.decision)
            else:
                ledger, handoff = reconcile(api, state, args.issue, args.decision)
            result = save(api, state, ledger, handoff, args.apply)
        print(install.render(result), end="")
    except (ValueError, KeyError, OSError, install.ApiError) as exc:
        p.exit(1, f"VIB session stopped: {exc}\n")

if __name__ == "__main__":
    main()
