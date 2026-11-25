"""Create a standalone VIB branch via GitHub's Git data API; no product ref writes."""
import argparse
import ast
import base64
import hashlib
import json
import os
import re
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

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
    "install/branch/STANDARD.md": "STANDARD.md",
    "install/branch/GATE.md": "GATE.md",
    "install/branch/AGENTS.md": "AGENTS.md",
}

class ApiError(RuntimeError):
    def __init__(self, status):
        super().__init__(f"GitHub API returned HTTP {status}")
        self.status = status

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
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request("https://api.github.com" + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            raise ApiError(exc.code) from None

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
            any(not isinstance(k, str) or not isinstance(v, str) or PACKAGE.get(k) != v
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
    return json.dumps(data, indent=2, sort_keys=True) + "\n"

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
    if read_ref(api, prefix, BRANCH) != commit:
        raise ValueError("Install ref read-back mismatch; inspect before retrying")
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
