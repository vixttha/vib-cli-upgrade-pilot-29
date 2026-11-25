"""Owner-controlled Standard VIB package upgrades and non-destructive retirement preparation."""
import argparse
import hashlib
import os
from tools import checkpoint, install, session

def tree_entries(api, prefix, sha):
    commit = api.request("GET", prefix + "/git/commits/" + sha)
    if commit.get("sha") != sha:
        raise ValueError("Commit identity mismatch")
    tree = install.require(install.SHA, commit["tree"]["sha"], "tree SHA")
    result = api.request("GET", prefix + "/git/trees/" + tree + "?recursive=1")
    if result.get("sha") != tree or result.get("truncated") is not False:
        raise ValueError("Tree is incomplete")
    entries = {}
    for item in result["tree"]:
        path = item["path"]
        if path in entries:
            raise ValueError("Duplicate tree path")
        entries[path] = (item["type"], item["mode"], item["sha"])
    return tree, entries

def digest_files(files):
    return {p: hashlib.sha256(s.encode("utf-8")).hexdigest() for p,s in files.items()}

def blob_id(value):
    raw = value.encode("utf-8")
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

def idle_state(api, target, expected_sha):
    install.require(install.SHA, expected_sha, "expected installed SHA")
    state = session.load(api, target, expected_sha)
    if state["manifest"].get("enforcement") != "disabled":
        raise ValueError("Disable and independently revoke enforced mode before lifecycle changes")
    if state["handoff"]["active"] is not None or any(
            row["state"] in ("in_progress", "staged") for row in state["ledger"]["issues"]):
        raise ValueError("Finish active work and reconcile staged issues before lifecycle changes")
    return state

def current(api, state):
    manifest, prefix = state["manifest"], state["prefix"]
    meta = api.request("GET", prefix)
    if (meta.get("id") != manifest["target_repository_id"]
            or meta.get("full_name") != manifest["target_repository"]
            or meta.get("default_branch") != manifest["product_branch"]
            or meta.get("archived") or meta.get("disabled")):
        raise ValueError("Target identity or default branch changed")
    if (install.read_ref(api, prefix, install.BRANCH) != state["ref"]
            or install.read_ref(api, prefix, manifest["product_branch"]) != state["product_sha"]):
        raise ValueError("Target refs advanced; reload before applying")

def upgrade(api, target, expected_sha, source_sha, *, apply=False, owner_approved=False, prepare_only=False):
    if prepare_only and not apply:
        raise ValueError("Prepare-only requires explicitly approved object creation")
    install.require(install.SHA, source_sha, "new pinned source")
    state = idle_state(api, target, expected_sha)
    manifest, prefix = state["manifest"], state["prefix"]
    old_source = manifest["source_sha"]
    if source_sha == old_source:
        raise ValueError("Already on requested source")
    comparison = api.request("GET", install.repo_path(install.SOURCE) + "/compare/" + old_source + "..." + source_sha)
    if comparison.get("status") != "ahead" or comparison["merge_base_commit"]["sha"] != old_source:
        raise ValueError("Upgrade must advance the pinned source history")
    old_files = install.source_files(api, old_source)
    new_files = install.source_files(api, source_sha)
    if not set(old_files).issubset(new_files):
        raise ValueError("Package path removal requires a separate migration")
    if apply:
        install.require_publishable_package(new_files)
    if not {"tools/session.py", "tools/install.py", "tools/lifecycle.py"}.issubset(new_files):
        raise ValueError("Target pin lacks lifecycle-compatible package")
    base_tree, entries = tree_entries(api, prefix, expected_sha)
    for p in old_files:
        if entries.get(p) != ("blob", "100644", blob_id(old_files[p])):
            raise ValueError("Installed package path differs from pinned bytes or mode")
    for p in ("vib-install.json", session.LEDGER, session.HANDOFF):
        if p not in entries or entries[p][:2] != ("blob", "100644"):
            raise ValueError("Invalid state file")
    for p in set(new_files) - set(old_files):
        if p in entries or any(q.startswith(p + "/") for q in entries):
            raise ValueError("New package path collides with adopter content")
        parts = p.split("/")
        for i in range(1,len(parts)):
            parent = "/".join(parts[:i])
            if parent in entries and entries[parent][0] != "tree":
                raise ValueError("New package parent collides with adopter content")
    new_manifest = {**manifest, "source_sha": source_sha, "package_files_sha256": digest_files(new_files)}
    changes = {p:s for p,s in new_files.items() if old_files.get(p) != s}
    changes["vib-install.json"] = install.render(new_manifest)
    plan = {"status":"planned", "operation":"upgrade", "repository":manifest["target_repository"],
            "from_source":old_source,"to_source":source_sha,"expected_sha":expected_sha,
            "changed_paths":sorted(changes),"product_sha":state["product_sha"]}
    if not apply:
        return plan
    if not owner_approved:
        raise ValueError("Explicit owner approval required")
    current(api, state)
    tree = install.require(install.SHA, api.request("POST", prefix + "/git/trees", {
        "base_tree":base_tree, "tree":[{"path":p,"type":"blob","mode":"100644","content":s}
                                   for p,s in sorted(changes.items())]})["sha"], "upgrade tree")
    commit = install.require(install.SHA, api.request("POST", prefix + "/git/commits", {
        "tree":tree,"parents":[expected_sha],"message":"Owner-approved pinned VIB package upgrade"
    })["sha"], "upgrade commit")
    # Check every leaf before publishing the ref; unchanged state and adopter files must be byte-identical.
    proposed_tree, proposed = tree_entries(api, prefix, commit)
    expected = {p:v for p,v in entries.items() if v[0] != "tree"}
    expected.update({p:("blob","100644",blob_id(s)) for p,s in changes.items()})
    if proposed_tree != tree or {p:v for p,v in proposed.items() if v[0] != "tree"} != expected:
        raise ValueError("Proposed upgrade tree changed unrelated paths")
    current(api,state)
    if prepare_only:
        return {**plan,"status":"prepared","prepared_sha":commit,"tree_sha":tree,"ref_moved":False}
    api.request("PATCH",prefix+"/git/refs/heads/"+install.BRANCH,{"sha":commit,"force":False})
    install.wait_ref(api,prefix,install.BRANCH,commit,expected_sha)
    observed = session.load(api,target,commit)
    if observed["manifest"] != new_manifest or observed["ledger"] != state["ledger"] or observed["handoff"] != state["handoff"]:
        raise ValueError("Upgrade readback mismatch; stop and inspect")
    if observed["product_sha"] != state["product_sha"]:
        raise ValueError("Product advanced concurrently; inspect before retry")
    return {**plan,"status":"upgraded","installed_sha":commit,"tree_sha":tree}

def prepare_retirement(api, target, expected_sha, *, apply=False, owner_approved=False):
    state = idle_state(api,target,expected_sha)
    prefix = state["prefix"]
    archive = "archive/vib/" + expected_sha
    if archive == state["manifest"]["product_branch"]:
        raise ValueError("Archive cannot be product branch")
    existing = install.read_ref(api,prefix,archive)
    if existing not in (None,expected_sha):
        raise ValueError("Archive ref occupied by another revision")
    plan = {"status":"planned","operation":"prepare_retirement","repository":state["manifest"]["target_repository"],
            "archive_branch":archive,"archived_sha":expected_sha,"product_sha":state["product_sha"],
            "installation_removed":False,"manual_removal_required":True}
    if not apply:
        return plan
    if not owner_approved:
        raise ValueError("Explicit owner approval required")
    current(api,state)
    if existing is None:
        api.request("POST",prefix+"/git/refs",{"ref":"refs/heads/"+archive,"sha":expected_sha})
    install.wait_ref(api,prefix,archive,expected_sha)
    current(api,state)
    return {**plan,"status":"archive_verified"}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target",required=True)
    p.add_argument("--expected-sha",required=True)
    sub=p.add_subparsers(dest="command",required=True)
    u=sub.add_parser("upgrade")
    u.add_argument("--source-sha",required=True)
    u.add_argument("--prepare-only",action="store_true",help="Create verified objects, never move a ref")
    r=sub.add_parser("prepare-retirement")
    for command in (u,r):
        command.add_argument("--apply",action="store_true")
        command.add_argument("--owner-approved",action="store_true")
    args=p.parse_args()
    token=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if args.apply and not token:
        p.error("Apply requires authorized GitHub credential")
    try:
        api=install.GitHub(token)
        opts={"apply":args.apply,"owner_approved":args.owner_approved}
        result=(upgrade(api,args.target,args.expected_sha,args.source_sha,prepare_only=args.prepare_only,**opts) if args.command=="upgrade"
                else prepare_retirement(api,args.target,args.expected_sha,**opts))
        print(install.render(result),end="")
    except (ValueError, KeyError, TypeError, OSError, install.ApiError) as exc:
        p.exit(1,f"VIB lifecycle stopped: {exc}\n")

if __name__=="__main__":
    main()
