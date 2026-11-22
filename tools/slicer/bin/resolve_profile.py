#!/usr/bin/env python3
"""Flatten an OrcaSlicer BBL system profile (inherits chain) into standalone JSON.
usage: resolve_profile.py <machine|process|filament> "<name>" <out.json>
Source: raw JSON from OrcaSlicer tag v2.5.2 (cached in profiles/src)."""
import json, os, sys, subprocess, urllib.parse
TAG = "v2.5.2"
BASE = f"https://raw.githubusercontent.com/SoftFever/OrcaSlicer/{TAG}/resources/profiles/BBL"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "profiles", "src")
CA = os.environ.get("CURL_CA_BUNDLE")

def load(kind, name):
    path = os.path.join(ROOT, kind, name + ".json")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        url = f"{BASE}/{kind}/{urllib.parse.quote(name)}.json"
        r = subprocess.run(["curl", "-fsSL", *(["--cacert", CA] if CA else []), "-o", path, url])
        if r.returncode:
            if os.path.exists(path): os.remove(path)
            sys.exit(f"cannot fetch {kind}/{name}")
    with open(path) as f:
        return json.load(f)

def resolve(kind, name):
    cur = load(kind, name)
    parent = cur.get("inherits")
    base = resolve(kind, parent) if parent else {}
    base.update({k: v for k, v in cur.items() if k != "inherits"})
    return base

if __name__ == "__main__":
    kind, name, out = sys.argv[1:4]
    d = resolve(kind, name)
    d["name"] = name
    d["from"] = "system"
    d.pop("instantiation", None)
    d["instantiation"] = "true"
    with open(out, "w") as f:
        json.dump(d, f, indent=2)
    print(out)
