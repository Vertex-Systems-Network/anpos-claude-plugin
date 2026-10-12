#!/usr/bin/env python3
"""Rebuild the bundled ANPOS child template from an upstream checkout.

Usage:
    python3 tools/build_template.py /path/to/ai-native-project-operating-system

Copies the upstream source into plugins/anpos/template/anpos-child/ without:
  * .git and caches,
  * every vendor-only path listed in config/licensing/vendor-source-boundary.json
    (commercial service, vendor workflows, Repository Supervisor plugin blueprint …).

Upstream's inert commercial *policy* files (config/licensing/*, COMMERCIAL-LICENSING.md …)
are kept because the upstream validator requires them in every child; the plugin never
activates the commercial_distribution role.

Writes template/SOURCE.json with the upstream commit and a content digest. Dev tool only:
it is not part of the installed plugin's runtime.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "anpos"
DEST = PLUGIN / "template" / "anpos-child"
SKIP_NAMES = {".git", "__pycache__", ".pytest_cache", "node_modules", ".DS_Store"}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1]).resolve()
    instance = json.loads((src / "config/protocol/instance.json").read_text())
    if instance.get("instance_status") != "template_source":
        raise SystemExit("source must be the canonical ANPOS template (instance_status: template_source)")
    boundary = json.loads((src / "config/licensing/vendor-source-boundary.json").read_text())
    vendor_only = [p.rstrip("/") for p in boundary["vendor_only_paths"]]

    def excluded(rel: str) -> bool:
        return any(rel == p or rel.startswith(p + "/") for p in vendor_only)

    if DEST.exists():
        shutil.rmtree(DEST)
    files = []
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src).as_posix()
        if any(part in SKIP_NAMES for part in path.relative_to(src).parts) or path.is_dir() or excluded(rel):
            continue
        target = DEST / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        files.append(rel)

    digest = hashlib.sha256()
    for rel in files:
        digest.update(rel.encode() + b"\0" + (DEST / rel).read_bytes() + b"\0")
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    version = json.loads((src / "config/protocol/version.json").read_text()).get("version")
    (PLUGIN / "template" / "SOURCE.json").write_text(json.dumps({
        "upstream_repository": "Vertex-Systems-Network/ai-native-project-operating-system",
        "upstream_commit": commit,
        "protocol_version": version,
        "file_count": len(files),
        "content_sha256": digest.hexdigest(),
        "excluded": "vendor_only_paths from config/licensing/vendor-source-boundary.json, .git, caches",
    }, indent=2) + "\n")
    print(f"template: {len(files)} files from {commit} (protocol {version}) -> {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
