"""Register issue versions and generate verification receipts."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory


LINE = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
VERSION = re.compile(LINE.pattern + r"\.[1-9][0-9]*")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA = re.compile(r"[0-9a-f]{40}")
STATES = {"assigned", "in_progress", "blocked", "deferred", "verified", "staged", "integrated", "retired"}
LEDGER = Path("ledger/issue-versions.json")
RECEIPT_SCOPE = Path("ledger/receipt-scope.json")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(raw):
    return json.loads(raw, object_pairs_hook=unique_keys)


def require(pattern, value, label):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def version_number(version):
    return tuple(int(part) for part in version[1:].split("."))


def bindings(ledger):
    if (not isinstance(ledger, dict) or set(ledger) != {"schema_version", "repository", "issues"}
            or type(ledger["schema_version"]) is not int or ledger["schema_version"] != 1
            or not isinstance(ledger["issues"], list)):
        raise ValueError("Expected a version 1 issue ledger")
    require(REPOSITORY, ledger["repository"], "repository")
    result = {}
    used_versions = set()
    for record in ledger["issues"]:
        if not isinstance(record, dict) or set(record) != {"issue", "version", "state", "acceptance"}:
            raise ValueError("Each record needs issue, version, state, and acceptance")
        issue = record["issue"]
        if type(issue) is not int or issue <= 0:
            raise ValueError(f"Invalid GitHub issue number: {issue!r}")
        version = require(VERSION, record["version"], "issue version")
        if not isinstance(record["state"], str) or record["state"] not in STATES:
            raise ValueError(f"Invalid state for issue #{issue}")
        criteria = record["acceptance"]
        if not isinstance(criteria, list) or not criteria or any(
            not isinstance(criterion, str) or not criterion.strip() for criterion in criteria
        ):
            raise ValueError(f"Issue #{issue} needs at least one acceptance criterion")
        if issue in result:
            raise ValueError(f"Duplicate issue: #{issue}")
        if version in used_versions:
            raise ValueError(f"Duplicate version: {version}")
        result[issue] = version
        used_versions.add(version)
    return result


def check_history(ledger, previous):
    current = bindings(ledger)
    if previous is None:
        return
    before = bindings(previous)
    if ledger["repository"].lower() != previous["repository"].lower():
        raise ValueError("Ledger repository changed")
    for issue, version in before.items():
        if current.get(issue) != version:
            raise ValueError(f"Historical assignment changed: issue #{issue} was {version}")


def register(ledger, issue, line, criteria):
    assigned = bindings(ledger)
    if type(issue) is not int or issue <= 0:
        raise ValueError(f"Invalid GitHub issue number: {issue!r}")
    require(LINE, line, "development line")
    if issue in assigned:
        version = assigned[issue]
        if version.rsplit(".", 1)[0] != line:
            raise ValueError(f"Issue #{issue} already belongs to {version}")
        return version, False
    prefix = line + "."
    latest = max((version_number(v)[-1] for v in assigned.values() if v.startswith(prefix)), default=0)
    version = f"{prefix}{latest + 1}"
    record = {"issue": issue, "version": version, "state": "assigned", "acceptance": criteria}
    updated = sorted([*ledger["issues"], record], key=lambda row: version_number(row["version"]))
    bindings({**ledger, "issues": updated})
    ledger["issues"] = updated
    return version, True


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def previous_at(commit, path):
    if not commit:
        return None
    require(SHA, commit, "previous revision")
    if commit == "0" * 40:
        return None
    if not git("ls-tree", "--name-only", commit, "--", path.as_posix()):
        return None
    return parse_json(git("show", f"{commit}:{path.as_posix()}"))


def check_receipt_scope(ledger, scope, previous=None):
    known_versions = set(bindings(ledger).values())
    if (not isinstance(scope, dict) or set(scope) != {"fixes", "versions"}
            or not isinstance(scope["fixes"], dict) or not isinstance(scope["versions"], list)
            or not scope["versions"]):
        raise ValueError("Receipt scope needs fix revisions and at least one selected version")
    fixes = scope["fixes"]
    for version, fix in fixes.items():
        require(VERSION, version, "issue version")
        if version not in known_versions:
            raise ValueError(f"Issue version {version} is absent from the ledger")
        require(SHA, fix, f"fix revision for {version}")
    selected = scope["versions"]
    for version in selected:
        require(VERSION, version, "selected issue version")
        if version not in fixes:
            raise ValueError(f"No fix revision for {version}")
    if len(set(selected)) != len(selected):
        raise ValueError("Receipt scope contains duplicate versions")
    if previous is not None:
        for version, fix in previous["fixes"].items():
            if fixes.get(version) != fix:
                raise ValueError(f"Historical fix revision changed: {version} was {fix}")


def verify_fix_ancestry(version, fix, candidate):
    if git("cat-file", "-t", fix) != "commit":
        raise ValueError(f"Fix revision for {version} is not a commit")
    result = subprocess.run(["git", "merge-base", "--is-ancestor", fix, candidate], check=False)
    if result.returncode != 0:
        raise ValueError(f"Fix revision for {version} is not in candidate history: {fix}")


def receipt(ledger, version, checkout, env, event, parents=(), *, fix_sha):
    bindings(ledger)
    require(VERSION, version, "issue version")
    record = next((row for row in ledger["issues"] if row["version"] == version), None)
    if record is None:
        raise ValueError(f"Issue version {version} is absent from the ledger")
    if checkout != require(SHA, env["GITHUB_SHA"], "workflow checkout"):
        raise ValueError("Actual checkout does not match GITHUB_SHA")
    repo = ledger["repository"]
    if any(name.lower() != repo.lower() for name in
           (env["GITHUB_REPOSITORY"], event["repository"]["full_name"])):
        raise ValueError("Ledger repository does not match the workflow event")
    name = env["GITHUB_EVENT_NAME"]
    require(SHA, fix_sha, "fix revision")
    base_sha = None
    pr = None
    if name == "pull_request":
        change = event["pull_request"]
        candidate_sha = require(SHA, change["head"]["sha"], "PR head revision")
        base_sha = require(SHA, change["base"]["sha"], "PR base revision")
        pr = change["number"]
        if type(pr) is not int or pr <= 0:
            raise ValueError("Invalid pull request number")
        if list(parents) != [base_sha, candidate_sha]:
            raise ValueError("Tested PR checkout does not have the expected base and head parents")
    elif name == "push":
        candidate_sha = require(SHA, event["after"], "pushed revision")
        if candidate_sha != checkout:
            raise ValueError("Pushed revision does not match the checkout")
    else:
        raise ValueError(f"Unsupported workflow event: {name}")
    digits = re.compile(r"[1-9][0-9]*")
    run = int(require(digits, env["GITHUB_RUN_ID"], "run ID"))
    attempt = int(require(digits, env["GITHUB_RUN_ATTEMPT"], "run attempt"))
    return {
        "schema_version": 1,
        "repository": repo,
        "issue": record["issue"],
        "issue_url": f"https://github.com/{repo}/issues/{record['issue']}",
        "version": version,
        "baseline_version": version.rsplit(".", 1)[0],
        "target_version": f"v{version_number(version)[0]}.{version_number(version)[1]}.{version_number(version)[2] + 1}",
        "acceptance": record["acceptance"],
        "scope": "issue-version ledger and receipt validation",
        "outcome": "pass",
        "fix_sha": fix_sha,
        "candidate_sha": candidate_sha,
        "tested_sha": checkout,
        "base_sha": base_sha,
        "pull_request": pr,
        "event": name,
        "workflow": env["GITHUB_WORKFLOW"],
        "job": env["GITHUB_JOB"],
        "run_id": run,
        "attempt": attempt,
        "run_url": f"https://github.com/{repo}/actions/runs/{run}/attempts/{attempt}",
    }


def write_json(path, data):
    with TemporaryDirectory(dir=path.parent) as temporary:
        staged = Path(temporary) / path.name
        staged.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
        staged.replace(path)


def receipts(ledger, scope, checkout, env, event, parents=(), *, versions=None, previous=None):
    check_receipt_scope(ledger, scope, previous)
    records = []
    for version in scope["versions"] if versions is None else versions:
        require(VERSION, version, "issue version")
        if version not in scope["fixes"]:
            raise ValueError(f"No fix revision for {version}")
        record = receipt(ledger, version, checkout, env, event, parents,
                         fix_sha=scope["fixes"][version])
        verify_fix_ancestry(version, record["fix_sha"], record["candidate_sha"])
        records.append(record)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="validate the ledger and any previous assignments")
    check.add_argument("--previous-ref", help="previous Git revision of this ledger")
    new = commands.add_parser("register", help="assign a version with one active writer")
    new.add_argument("issue", type=int)
    new.add_argument("line", help="starting project version, such as v0.0.0 for work toward v0.0.1")
    new.add_argument("--acceptance", action="append", default=[])
    proof = commands.add_parser("receipt", help="write a receipt in a GitHub Actions job")
    proof.add_argument("version")
    proof.add_argument("output", type=Path)
    batch = commands.add_parser("receipts", help="write the selected issue receipts in a new directory")
    batch.add_argument("output", type=Path)
    args = parser.parse_args()

    try:
        ledger = parse_json(LEDGER.read_text(encoding="utf-8"))
        if args.command == "check":
            check_history(ledger, previous_at(args.previous_ref, LEDGER))
            print(f"Validated {len(ledger['issues'])} issue-version assignment(s)")
        elif args.command == "register":
            version, added = register(ledger, args.issue, args.line, args.acceptance)
            if added:
                write_json(LEDGER, ledger)
            print(version)
        else:
            event = parse_json(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
            checkout = git("rev-parse", "HEAD")
            parents = git("show", "-s", "--format=%P", checkout).split()
            scope = parse_json(RECEIPT_SCOPE.read_text(encoding="utf-8"))
            previous_sha = (event["pull_request"]["base"]["sha"]
                            if os.environ["GITHUB_EVENT_NAME"] == "pull_request" else event.get("before"))
            results = receipts(ledger, scope, checkout, os.environ, event, parents,
                               versions=[args.version] if args.command == "receipt" else None,
                               previous=previous_at(previous_sha, RECEIPT_SCOPE))
            if args.command == "receipts":
                args.output.mkdir()
            for result in results:
                output = args.output
                if args.command == "receipts":
                    output /= f"{result['version']}-receipt.json"
                write_json(output, result)
                print(f"{result['version']} verified on {result['tested_sha']} in {result['run_url']}")
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Checkpoint error: {error}\n")


if __name__ == "__main__":
    main()
