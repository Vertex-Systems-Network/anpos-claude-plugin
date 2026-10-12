#!/usr/bin/env python3
"""Minimal fake of the `gh` CLI for offline tests of `anpos pm apply/pull`.

State lives in the JSON file named by FAKE_GH_STATE. Only the commands the plugin uses are implemented.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

STATE = os.environ["FAKE_GH_STATE"]


def load() -> dict:
    try:
        with open(STATE) as handle:
            data = json.load(handle)
    except FileNotFoundError:
        data = {}
    for key, default in (("issues", {}), ("milestones", {}), ("next_issue", 1), ("next_ms", 1), ("calls", []), ("tick", 0)):
        data.setdefault(key, default)
    return data


def save(s: dict) -> None:
    with open(STATE, "w") as handle:
        json.dump(s, handle, indent=1)


def opt(args: list[str], name: str, default=None):
    return args[args.index(name) + 1] if name in args else default


def fields(args: list[str]) -> dict:
    out = {}
    for i, a in enumerate(args):
        if a == "-f":
            k, v = args[i + 1].split("=", 1)
            out[k] = v
    return out


def stamp(s: dict) -> str:
    s["tick"] += 1
    return f"2026-10-12T00:00:{s['tick']:02d}Z"


def main() -> int:
    a = sys.argv[1:]
    s = load()
    s["calls"].append(a)
    slug = opt(a, "-R")
    if a[:2] == ["auth", "status"]:
        save(s)
        return 0
    if a[0] == "label" or a[:2] == ["project", "item-add"]:
        save(s)
        return 0
    if a[0] == "api":
        method = opt(a, "-X", "GET")
        path = [x for x in a[1:] if x.startswith("repos/")][0]
        clean = path.split("?")[0]
        parts = clean.split("/")
        repo = s.setdefault("repo", {"private": False, "permissions": {"admin": True}, "security_and_analysis": {},
                                     "default_branch": "main", "allow_merge_commit": True, "allow_rebase_merge": True,
                                     "allow_squash_merge": True})
        body = sys.stdin.read() if "--input" in a else None
        if len(parts) == 3 and method == "GET":
            print(json.dumps(repo))
        elif len(parts) == 3 and method == "PATCH":
            for i, x in enumerate(a):
                if x == "-F":
                    k, v = a[i + 1].split("=", 1)
                    repo[k] = {"true": True, "false": False}.get(v, v)
        elif "check-runs" in clean:
            print(json.dumps({"check_runs": s.get("check_runs", [])}))
        elif clean.endswith("/rulesets") and method == "GET":
            print(json.dumps([{"id": int(k), "name": v["name"]} for k, v in s.setdefault("rulesets", {}).items()]))
        elif clean.endswith("/rulesets") and method == "POST":
            rid = str(100 + len(s.setdefault("rulesets", {})))
            s["rulesets"][rid] = json.loads(body)
            print(json.dumps({"id": int(rid)}))
        elif "/rulesets/" in clean and method == "PUT":
            s["rulesets"][parts[-1]] = json.loads(body)
            print(json.dumps({"id": int(parts[-1])}))
        elif "/rulesets/" in clean:
            print(json.dumps(s["rulesets"][parts[-1]]))
        elif clean.endswith("/comments"):
            print(json.dumps(s.get("comments", {}).get(parts[-2], [])))
        elif "/collaborators/" in clean:
            print(json.dumps({"permission": s.get("perms", {}).get(parts[-2], "read")}))
        elif method == "GET" and clean.endswith("/milestones"):
            print(json.dumps([{"title": m["title"], "number": int(n)} for n, m in s["milestones"].items()]))
        elif method == "POST" and clean.endswith("/milestones"):
            n = s["next_ms"]
            s["next_ms"] += 1
            s["milestones"][str(n)] = fields(a)
            print(json.dumps({"number": n, "html_url": f"https://github.com/x/milestone/{n}"}))
        elif "/milestones/" in clean:
            n = path.rsplit("/", 1)[-1]
            s["milestones"][n].update(fields(a))
        save(s)
        return 0
    if a[0] == "issue":
        sub = a[1]
        if sub == "list":
            term = opt(a, "--search").split('"')[1]
            hits = [{"number": int(n), "url": i["url"]} for n, i in s["issues"].items() if term in i["body"]]
            print(json.dumps(hits))
        elif sub == "create":
            n = s["next_issue"]
            s["next_issue"] += 1
            url = f"https://github.com/{slug}/issues/{n}"
            s["issues"][str(n)] = {"title": opt(a, "--title"), "body": sys.stdin.read(), "state": "OPEN",
                                   "milestone": opt(a, "--milestone"), "url": url, "updatedAt": stamp(s)}
            print(url)
        elif sub == "edit":
            issue = s["issues"][a[2]]
            issue.update({"title": opt(a, "--title"), "body": sys.stdin.read(), "updatedAt": stamp(s)})
        elif sub == "view":
            issue = s["issues"][a[2]]
            print(json.dumps({"state": issue["state"], "updatedAt": issue["updatedAt"]}))
        elif sub == "close":
            s["issues"][a[2]].update({"state": "CLOSED", "updatedAt": stamp(s)})
        elif sub == "reopen":
            s["issues"][a[2]].update({"state": "OPEN", "updatedAt": stamp(s)})
        save(s)
        return 0
    print(f"fake gh: unsupported {a}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
