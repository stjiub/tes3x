#!/usr/bin/env python3
"""Look up Morrowind mods on Nexus Mods through its public GraphQL API. No key is needed.

    tes3x nexus 45384
    tes3x nexus "Optimization Patch"
"""

import argparse
import json
import sys
import urllib.request

API = "https://api.nexusmods.com/v2/graphql"
GAME_ID = 100
GAME = "morrowind"
FIELDS = "modId name summary author version"


def page_url(mod_id):
    return f"https://www.nexusmods.com/{GAME}/mods/{mod_id}"


def query(text, timeout=15):
    request = urllib.request.Request(
        API, data=json.dumps({"query": text}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "TES3X"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        reply = json.load(response)
    if reply.get("errors"):
        raise ValueError("; ".join(error.get("message", "?") for error in reply["errors"]))
    return reply["data"]


def info(node):
    return {"id": node["modId"], "name": node["name"], "summary": node.get("summary") or "",
            "author": node.get("author") or "", "version": node.get("version") or "",
            "url": page_url(node["modId"])}


def mod_info(mod_id):
    """A mod's name, summary, author, version and page, or None when Nexus has no such mod."""
    data = query(f"{{ legacyMods(ids: [{{gameId: {GAME_ID}, modId: {int(mod_id)}}}]) "
                 f"{{ nodes {{ {FIELDS} }} }} }}")
    nodes = data["legacyMods"]["nodes"]
    return info(nodes[0]) if nodes else None


def search(name, count=10):
    """Mods whose name contains name."""
    data = query(f"{{ mods(filter: {{gameDomainName: [{{value: {json.dumps(GAME)}}}], "
                 f"name: [{{value: {json.dumps(name)}, op: WILDCARD}}]}}, count: {int(count)}) "
                 f"{{ nodes {{ {FIELDS} }} }} }}")
    return [info(node) for node in data["mods"]["nodes"]]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mod", help="a Nexus mod id or part of a mod name")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")
    found = [mod_info(args.mod)] if args.mod.isdigit() else search(args.mod)
    for mod in filter(None, found):
        print(f"{mod['name']} {mod['version']} by {mod['author']}\n  {mod['url']}\n"
              f"  {mod['summary']}")
    return 0 if any(found) else 1


if __name__ == "__main__":
    sys.exit(main())
