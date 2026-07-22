/**
 * Resolve the project root from any subdirectory.
 *
 * Uses ENGRAM_PROJECT_ROOT env var if set (most reliable).
 * Otherwise walks up from `startDir` looking for a standing-law marker
 * (CLAUDE.md or AGENTS.md — the latter is what non-Claude harnesses like Kimi
 * use), then .git. Stops walking at the first .git to avoid crossing into
 * parent repos.
 *
 * This prevents subdirectory cwd values (from Bash tool cd) from
 * splitting observations across multiple engram databases.
 */

import { existsSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

export function resolveProjectRoot(startDir: string): string {
  // Explicit env var takes priority
  if (process.env.ENGRAM_PROJECT_ROOT) {
    return process.env.ENGRAM_PROJECT_ROOT;
  }

  let dir = resolve(startDir);
  const root = '/';

  // First pass: look for a standing-law marker (strongest workspace indicator).
  // CLAUDE.md for Claude Code; AGENTS.md for Kimi and other non-Claude harnesses
  // that use it as their standing-law file, so their observations shard the same
  // way rather than falling through to the .git fallback.
  let search = dir;
  while (search !== root) {
    if (existsSync(join(search, 'CLAUDE.md')) || existsSync(join(search, 'AGENTS.md'))) {
      return search;
    }
    const parent = dirname(search);
    if (parent === search) break;
    search = parent;
  }

  // Second pass: look for .git (repo root)
  // For workspaces containing multiple repos, this returns the first
  // .git found walking up — which is the immediate repo, not the workspace.
  // That's acceptable as a fallback.
  search = dir;
  while (search !== root) {
    if (existsSync(join(search, '.git'))) {
      return search;
    }
    const parent = dirname(search);
    if (parent === search) break;
    search = parent;
  }

  // No marker found — use the original directory
  return resolve(startDir);
}
