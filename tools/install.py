"""Create a standalone VIB branch via GitHub's Git data API; no product ref writes."""
import argparse
import ast
import base64
import hashlib
import json
import os
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

if __package__:
    from . import checkpoint
else:  # Preserve direct script invocation.
    import checkpoint

SOURCE = "vixttha/Version-Issues-Hub"
BRANCH = "Version-Issues-Hub"
SHA = re.compile(r"[0-9a-f]{40}")
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+")
BASELINE = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
PACKAGE = {
    "install/branch/README.md": "README.md",
    "install/branch/WORKFLOW.md": "WORKFLOW.md",
    "tools/checkpoint.py": "tools/checkpoint.py",
    "tools/install.py": "tools/install.py",
    "tools/session.py": "tools/session.py",
    "tools/gate.py": "tools/gate.py",
    "tools/standard.py": "tools/standard.py",
    "tools/lifecycle.py": "tools/lifecycle.py",
    "install/branch/LIFECYCLE.md": "LIFECYCLE.md",
    "install/branch/STANDARD.md": "STANDARD.md",
    "install/branch/GATE.md": "GATE.md",
    "install/branch/AGENTS.md": "AGENTS.md",
    "LICENSE": "VIB-LICENSE.md",
    "VIB-NOTICE.md": "VIB-NOTICE.md",
}

HISTORICAL_LEGAL_PATHS = {
    "docs/licensing/reference/PolyForm-Strict-1.0.0.md": "VIB-LICENSE.md",
    "docs/licensing/VIB-NOTICE-REVIEW.md": "VIB-NOTICE.md",
}

class ApiError(RuntimeError):
    def __init__(self, status, method=None, operation=None):
        context = f" ({method} {operation})" if method and operation else ""
        super().__init__(f"GitHub API returned HTTP {status}{context}")
        self.status = status

class ApiTransportError(OSError):
    """Transport failure with only safe operation context, never request details."""
    def __init__(self, method, operation, timed_out):
        self.timed_out = timed_out
        detail = "timed out" if timed_out else "failed"
        caution = "; inspect before retrying any write" if method != "GET" else ""
        super().__init__(f"GitHub transport {detail} ({method} {operation}){caution}")


def api_operation(path):
    """Classify endpoint without logging repository names, filenames, query strings or bodies."""
    parts = path.split("?", 1)[0].split("/")
    tail = parts[4:] if len(parts) >= 4 and parts[1] == "repos" else []
    if not tail:
        return "repository"
    if tail[0] == "git" and len(tail) >= 2 and tail[1] in ("trees", "commits", "refs", "ref", "blobs"):
        return "git/" + tail[1]
    if tail[0] in ("compare", "contents", "issues", "pulls", "actions", "check-runs", "commits"):
        return tail[0]
    return "other"

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class GitHub:
    def __init__(self, token):
        self.token = token
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, body=None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request("https://api.github.com" + path, data=data, headers=headers, method=method)
        delays = (0, 0.5, 1.5) if method == "GET" else (0,)
        for attempt, delay in enumerate(delays):
            if delay:
                time.sleep(delay)
            try:
                with self.opener.open(req, timeout=30) as response:
                    try:
                        return checkpoint.parse_json(response.read().decode("utf-8"))
                    except ValueError:
                        # A malformed write response is uncertain; never replay the write.
                        caution = "; inspect before retrying any write" if method != "GET" else ""
                        raise ValueError(f"Invalid GitHub JSON response ({method} {api_operation(path)}){caution}") from None
            except HTTPError as exc:
                code = exc.code
                exc.close()
                if code not in (500, 502, 503, 504) or attempt == len(delays) - 1:
                    raise ApiError(code, method, api_operation(path)) from None
            except (TimeoutError, URLError) as exc:
                timed_out = isinstance(exc, TimeoutError) or (
                    isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError))
                # A read may safely repeat; an uncertain write never does.
                if not timed_out or attempt == len(delays) - 1:
                    raise ApiTransportError(method, api_operation(path), timed_out) from None


def require(pattern, value, name):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"Invalid {name}")
    return value

def repo_path(name):
    require(REPO, name, "repository owner/name")
    if name.split("/")[1] in (".", ".."):
        raise ValueError("Invalid repository name")
    return "/repos/" + quote(name, safe="/")

def read_ref(api, prefix, branch):
    try:
        data = api.request("GET", prefix + "/git/ref/heads/" + quote(branch, safe="/"))
    except ApiError as exc:
        if exc.status == 404:
            return None
        raise
    if data.get("ref") != "refs/heads/" + branch or data["object"]["type"] != "commit":
        raise ValueError("Unexpected GitHub ref response")
    return require(SHA, data["object"]["sha"], "ref revision")

def wait_ref(api, prefix, branch, expected, previous=None):
    """After a successful write, tolerate only absent/known-old readback, never repeat the write."""
    require(SHA, expected, "expected ref revision")
    if previous is not None:
        require(SHA, previous, "previous ref revision")
    for delay in (0, 0.25, 0.5, 1, 2, 4):
        if delay:
            time.sleep(delay)
        observed = read_ref(api, prefix, branch)
        if observed == expected:
            return expected
        if observed != previous:
            raise ValueError("Unexpected ref revision after write; inspect before retrying")
    raise ValueError("Ref readback not confirmed; inspect before retrying")

def read_text(api, prefix, path, ref):
    require(SHA, ref, "file revision")
    item = api.request("GET", prefix + "/contents/" + quote(path, safe="/") + "?ref=" + ref)
    if item.get("type") != "file" or item.get("encoding") != "base64" or item.get("path") != path:
        raise ValueError("Package source is not a regular file")
    raw = base64.b64decode("".join(item["content"].split()), validate=True)
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if blob != item.get("sha"):
        raise ValueError("Package blob integrity mismatch")
    return raw.decode("utf-8")

def pinned_package(source_text):
    """Read a historical literal package declaration without executing source."""
    try:
        tree = ast.parse(source_text)
        declarations = [node.value for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "PACKAGE" for t in node.targets)]
        if len(declarations) != 1:
            raise ValueError("Missing or ambiguous pinned package declaration")
        node = declarations[0]
        if not isinstance(node, ast.Dict) or any(k is None for k in node.keys):
            raise ValueError("Pinned package must be a literal mapping")
        package = ast.literal_eval(node)
        if len(package) != len(node.keys):
            raise ValueError("Duplicate pinned package path")
    except (SyntaxError, TypeError, ValueError) as exc:
        raise ValueError("Invalid pinned package declaration") from exc
    required = {"install/branch/README.md", "install/branch/WORKFLOW.md", "tools/checkpoint.py"}
    if (not required.issubset(package) or
            any(not isinstance(k, str) or not isinstance(v, str) or {**PACKAGE, **HISTORICAL_LEGAL_PATHS}.get(k) != v
                for k, v in package.items()) or len(set(package.values())) != len(package)):
        raise ValueError("Pinned package has unsupported or missing paths")
    return package

def source_files(api, source_sha):
    files = {}
    prefix = repo_path(SOURCE)
    commit = api.request("GET", prefix + "/git/commits/" + source_sha)
    if commit.get("sha") != source_sha:
        raise ValueError("Source revision did not match")
    source_text = read_text(api, prefix, "tools/install.py", source_sha)
    for source, dest in pinned_package(source_text).items():
        files[dest] = source_text if source == "tools/install.py" else read_text(api, prefix, source, source_sha)
    return files

def render(data):
    return json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n"

def require_publishable_package(files):
    """Reject incomplete or review-only legal packages; not a license enforcement service."""
    names = {"VIB-LICENSE.md", "VIB-NOTICE.md"}
    if not names.intersection(files):
        return  # Historical pins remain readable and compatible.
    if not names.issubset(files):
        raise ValueError("Incomplete legal package is not authorized for installation or upgrade")
    raw = files["VIB-LICENSE.md"].encode("utf-8")
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    notice = files["VIB-NOTICE.md"]
    notice_raw = notice.encode("utf-8")
    notice_blob = hashlib.sha1(b"blob " + str(len(notice_raw)).encode() + b"\0" + notice_raw).hexdigest()
    if (blob != "90abd0e6c64a135f42451762337a397bf27f4862"
            or notice_blob != "70dd8710828a38118965be3ca8be32457d106789"
            or "VIB_LICENSE_TERMS_APPROVED" not in notice.splitlines()
            or "REVIEW_ONLY_NOT_A_GRANT" in notice):
        raise ValueError("Legal review package is not authorized for installation or upgrade")


def install(api, target, source_sha, baseline, apply=False):
    require(SHA, source_sha, "full source commit")
    require(BASELINE, baseline, "baseline")
    prefix = repo_path(target)
    meta = api.request("GET", prefix)
    if meta.get("full_name", "").lower() != target.lower():
        raise ValueError("Target repository identity mismatch")
    if meta.get("archived") or meta.get("disabled"):
        raise ValueError("Target repository is not writable")
    if type(meta.get("id")) is not int or meta["id"] <= 0:
        raise ValueError("Target repository ID is missing")
    default = meta["default_branch"]
    if default == BRANCH:
        raise ValueError("Install branch cannot be the product default branch")
    if read_ref(api, prefix, BRANCH) is not None:
        raise ValueError("Install branch already exists; no overwrite or upgrade performed")
    product = read_ref(api, prefix, default)
    if product is None:
        raise ValueError("Initialize the target repository before installing VIB")
    files = source_files(api, source_sha)
    major, minor, patch = map(int, baseline[1:].split("."))
    manifest = {
        "schema_version": 1, "package": "Version-Issues-Hub",
        "source_repository": SOURCE, "source_sha": source_sha,
        "target_repository": meta["full_name"], "target_repository_id": meta["id"],
        "product_branch": default, "product_baseline_sha": product,
        "install_branch": BRANCH, "baseline_version": baseline,
        "target_version": f"v{major}.{minor}.{patch + 1}",
        "enforcement": "disabled",
        "package_files_sha256": {p: hashlib.sha256(s.encode()).hexdigest() for p, s in files.items()},
    }
    files["vib-install.json"] = render(manifest)
    files["ledger/issue-versions.json"] = render({
        "schema_version": 1, "repository": meta["full_name"], "issues": [],
    })
    files["ledger/handoff.json"] = render({"schema_version": 1, "active": None, "history": []})
    plan = {"status": "planned", "manifest": manifest, "paths": sorted(files)}
    if not apply:
        return plan
    require_publishable_package(files)
    # Recheck identity and refs before creating unreachable objects. Never update a ref.
    latest = api.request("GET", prefix)
    if latest.get("id") != meta["id"] or latest.get("default_branch") != default:
        raise ValueError("Target identity or default branch changed")
    if read_ref(api, prefix, BRANCH) is not None or read_ref(api, prefix, default) != product:
        raise ValueError("Target refs changed; rerun the dry run")
    entries = [{"path": p, "mode": "100644", "type": "blob", "content": s}
               for p, s in sorted(files.items())]
    tree = require(SHA, api.request("POST", prefix + "/git/trees", {"tree": entries})["sha"], "tree")
    commit = require(SHA, api.request("POST", prefix + "/git/commits", {
        "message": "Install pinned Version-Issues-Hub package",
        "tree": tree, "parents": [product],
    })["sha"], "installed commit")
    # Create-only: GitHub rejects an existing branch even in a race.
    api.request("POST", prefix + "/git/refs", {"ref": "refs/heads/" + BRANCH, "sha": commit})
    wait_ref(api, prefix, BRANCH, commit)
    installed = api.request("GET", prefix + "/git/commits/" + commit)
    if installed.get("sha") != commit or installed["tree"]["sha"] != tree:
        raise ValueError("Installed tree read-back mismatch")
    if read_ref(api, prefix, default) != product:
        raise ValueError("Product ref changed concurrently; inspect before retrying")
    return {**plan, "status": "installed", "installed_sha": commit, "installed_tree_sha": tree}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target", required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--baseline", required=True)
    p.add_argument("--apply", action="store_true", help="Create only the dedicated branch")
    args = p.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if args.apply and not token:
        p.error("Apply requires a GitHub credential in GH_TOKEN or GITHUB_TOKEN")
    try:
        print(render(install(GitHub(token), args.target, args.source_sha, args.baseline, args.apply)), end="")
    except (ValueError, ApiError, KeyError, OSError) as exc:
        p.exit(1, f"Install stopped: {exc}\n")

if __name__ == "__main__":
    main()
