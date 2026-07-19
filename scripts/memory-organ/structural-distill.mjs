// Memory-organ distill: gitnexus structural facts -> engram, so facet-aware
// recall (findRelated) can surface them woven with episodic memory.
// Source: gitnexus eval-server (POST /tool/cypher — its own kuzu/ladybug backend).
// Sink: the shared engram DB for this workspace.
//
// Portable: imports resolve relative to this file (snarc/scripts/memory-organ/).
// Run from anywhere; pass the workspace root as argv[2] (default: cwd). The
// gitnexus eval-server must be running: `gitnexus eval-server --port 4848`.
//
// Finding 2026-07-18 (docs/MEMORY_ORGAN_UNIFIED_RECALL_FINDING.md): store-level
// unification is necessary but not sufficient — the reader must be facet-aware.
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
const HERE = dirname(fileURLToPath(import.meta.url));
const { EngramMemory } = await import(resolve(HERE, '../../dist/src/memory.js'));
const { getDbPath } = await import(resolve(HERE, '../../dist/src/db.js'));

const ROOT = process.argv[2] || process.env.ENGRAM_ROOT_DIR || process.cwd();
const PORT = process.env.GITNEXUS_EVAL_PORT || 4848;
const TOPN = 15, SALIENCE = 0.5;

async function cypher(repo, query) {
  const r = await fetch(`http://127.0.0.1:${PORT}/tool/cypher`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo, query }),
  });
  const t = await r.text();
  const j = JSON.parse(t.split('\n---')[0]); // strip the trailing "Next:" hint
  const md = j.markdown || '';
  const lines = md.split('\n').filter(l => l.trim().startsWith('|'));
  if (lines.length < 3) return [];
  return lines.slice(2).map(l => l.split('|').slice(1, -1).map(c => c.trim()));
}

const health = await (await fetch(`http://127.0.0.1:${PORT}/health`)).json();
const repos = health.repos || [];
const mem = new EngramMemory(getDbPath(ROOT));
mem.initSession('structural-distill', ROOT);

let written = 0; const perRepo = {};
for (const repo of repos) {
  const rows = await cypher(repo,
    `MATCH (f:Function)<-[r:CodeRelation]-() RETURN f.name AS name, f.filePath AS file, count(r) AS indeg ORDER BY indeg DESC LIMIT ${TOPN}`);
  let n = 0;
  for (const [name, file, indeg] of rows) {
    if (!name || name === 'name') continue;
    const text = `${name} — code symbol (function) in ${repo}/${file}; ${indeg} callers. Deep view: gitnexus context "${name}".`;
    if (mem.captureContext('structural', text, ROOT, SALIENCE)) { written++; n++; }
  }
  perRepo[repo] = n;
}
mem.close();
console.log('structural rows written:', written);
console.log('per repo:', JSON.stringify(perRepo));
