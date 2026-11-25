# Standard VIB — safe lifecycle

## Upgrade and retirement contract

Use the owner-approved source pin and exact current installed branch SHA. Both operations are read-only plans by default. No product branch or protection settings are changed. Only apply after explicit owner approval and a reviewed plan. Do not run while a worker is active or a staged issue awaits reconciliation; do not use with enabled hard enforcement.

```sh
python -m tools.lifecycle --target OWNER/REPO --expected-sha STATE_SHA upgrade --source-sha NEW_SOURCE_SHA
python -m tools.lifecycle --target OWNER/REPO --expected-sha STATE_SHA prepare-retirement
```

Add --apply --owner-approved to the selected subcommand only after review. Run from the new reviewed package checkout for upgrades. Upgrade checks that the new source descends from the old source, refuses removed package paths and collisions with adopter files, preserves all existing state and unrelated leaf objects byte-for-byte, and checks the proposed tree before a non-force ref update. Re-read after any ambiguous failure. The expected SHA is a cooperative stale-write guard; serialize owner lifecycle work with other writers and do not force-update refs. The new installation pin is recorded in the manifest, while older pins and states remain in Git history.

Retirement preparation creates a durable archive/vib/STATE_SHA ref at the exact validated installation state and verifies it. It **never deletes** the dedicated branch. If the owner later chooses to remove the installation, freeze all writers, recheck the archive and current SHA, retain evidence and external artifact backups, and delete only the exact dedicated branch via a separately authorized operation. Never delete a product or other template branch. If anything advanced, repeat archival preparation at the new SHA. Archiving Git history does not preserve expiring Actions artifacts automatically.

Phase 4 evidence: unit tests exercise real disposable Git repositories through a simulated GitHub API on public runners. This proves tree/commit/ref preservation under the tested conditions, not live GitHub retirement or an independent agent trial. No live adopter upgrade or deletion is implied.

### Alternative transport after a ref-update failure

If the GitHub REST ref mutation is unavailable, a separately owner-approved `upgrade --source-sha NEW --apply --owner-approved --prepare-only` creates and checks only the new tree and child commit. It does not move a ref. Inspect the returned exact expected old SHA, prepared SHA and tree; recheck repo identity, active work, product and installed refs. An authorized GitHub connector can advance only the dedicated ref with the exact old SHA as its lease and force disabled. Independently verify the prepared commit parent, full tree and unchanged adopter-state blobs after readback. Never automatically retry a failed mutation or fall back to a different transport without checking the actual state. This is a supported alternative path, not a claim that GitHub's REST PATCH outage has been fixed.
