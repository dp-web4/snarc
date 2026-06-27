#!/usr/bin/env python3
"""Idempotently register the snarc PreCompact hook in this machine's ~/.claude/settings.json.

The PreCompact handler (pre-compact.js) captures the CONVERSATION (what was said) before context
compaction — the "mind", complementing PostToolUse's "hands". It shipped compiled but was never
registered on any machine. This wires it, per-machine, safely:
  - resolves the snarc path from THIS script's location (no hardcoded per-machine paths)
  - no-op if already registered (safe to run every deploy)
  - backs up settings.json + validates the JSON before writing (restores nothing destructive)

Run after `git pull && npm install && npm run build`:
    python3 scripts/register_precompact.py
"""
import json, os, sys, shutil, time

SETTINGS = os.path.expanduser("~/.claude/settings.json")
SNARC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # scripts/ -> snarc/
HANDLER_JS = os.path.join(SNARC_ROOT, "dist", "hooks", "handlers", "pre-compact.js")
COMMAND = f"node {HANDLER_JS}"


def main() -> int:
    if not os.path.exists(SETTINGS):
        print(f"[register_precompact] {SETTINGS} not found — snarc not wired on this machine; skipping")
        return 0
    if not os.path.exists(HANDLER_JS):
        print(f"[register_precompact] {HANDLER_JS} missing — run `npm install && npm run build` first")
        return 1

    with open(SETTINGS) as f:
        data = json.load(f)

    hooks = data.setdefault("hooks", {})
    precompact = hooks.setdefault("PreCompact", [])

    for grp in precompact:
        for h in grp.get("hooks", []):
            if "pre-compact.js" in h.get("command", ""):
                print("[register_precompact] PreCompact already registered — no-op")
                return 0

    # back up, then add, then validate before writing
    shutil.copy(SETTINGS, f"{SETTINGS}.bak.{int(time.time())}")
    precompact.append({"hooks": [{"type": "command", "command": COMMAND, "timeout": 15}]})
    out = json.dumps(data, indent=2)
    json.loads(out)  # sanity: must round-trip
    with open(SETTINGS, "w") as f:
        f.write(out + "\n")
    print(f"[register_precompact] registered PreCompact -> {COMMAND}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
