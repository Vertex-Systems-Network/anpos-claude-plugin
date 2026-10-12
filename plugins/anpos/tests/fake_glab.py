#!/usr/bin/env python3
"""Minimal fake of `glab` for offline tests (state in FAKE_GLAB_STATE)."""
from __future__ import annotations

import json
import os
import sys
from urllib.parse import unquote

STATE = os.environ["FAKE_GLAB_STATE"]


def load() -> dict:
    try:
        with open(STATE) as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}


def main() -> int:
    a = sys.argv[1:]
    s = load()
    s.setdefault("project", {"permissions": {"project_access": {"access_level": 40}}, "default_branch": "main",
                             "merge_method": "merge", "only_allow_merge_if_pipeline_succeeds": False})
    s.setdefault("protected", {})
    s.setdefault("premium", False)
    s.setdefault("issues", {})
    s.setdefault("members", {})
    if a[:2] == ["auth", "status"]:
        return 0
    if a[0] != "api":
        return 1
    method = a[a.index("-X") + 1] if "-X" in a else "GET"
    path = [x for x in a[1:] if x.startswith("projects/")][0].split("?")[0]
    fields = {}
    for i, x in enumerate(a):
        if x == "-f":
            k, v = a[i + 1].split("=", 1)
            fields[k] = v
    parts = path.split("/")
    out = None
    code = 0
    if len(parts) == 2 and method == "GET":
        out = s["project"]
    elif len(parts) == 2 and method == "PUT":
        for k, v in fields.items():
            s["project"][k] = {"true": True, "false": False}.get(v, v)
        out = s["project"]
    elif parts[2] == "protected_branches" and method == "POST":
        s["protected"][fields["name"]] = {"name": fields["name"], "allow_force_push": fields.get("allow_force_push") == "true",
                                          "push_access_levels": [{"access_level": int(fields["push_access_level"])}]}
        out = s["protected"][fields["name"]]
    elif parts[2] == "protected_branches" and method == "DELETE":
        s["protected"].pop(unquote(parts[3]), None)
    elif parts[2] == "protected_branches":
        name = unquote(parts[3])
        if name in s["protected"]:
            out = s["protected"][name]
        else:
            code = 1
    elif parts[2] == "approval_rules":
        if s["premium"]:
            out = {"id": 1}
        else:
            code = 1
    elif parts[2] == "issues" and method == "POST":
        iid = str(len(s["issues"]) + 1)
        s["issues"][iid] = {"title": fields.get("title"), "notes": []}
        out = {"iid": int(iid), "web_url": f"https://gitlab.com/acme/app/-/issues/{iid}"}
    elif parts[2] == "issues" and parts[-1] == "notes":
        out = s["issues"].get(parts[3], {}).get("notes", [])
    elif parts[2] == "members":
        out = {"access_level": s["members"].get(parts[-1], 10)}
    elif parts[2] == "pipelines":
        out = s.get("pipelines", [])
    else:
        code = 1
    with open(STATE, "w") as handle:
        json.dump(s, handle, indent=1)
    if code:
        print("404 Not Found", file=sys.stderr)
        return 1
    if out is not None:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
