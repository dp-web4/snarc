/**
 * Export/Import — fleet portability via markdown.
 * Exports Tier 2 (patterns) and Tier 3 (identity) to markdown.
 * Tier 0 and 1 are machine-local (raw, session-specific).
 */

import { EngramMemory } from './memory.js';

export function exportMarkdown(memory: EngramMemory): string {
  const patterns = memory.getPatterns();
  const identity = memory.getIdentity();
  const stats = memory.getStats();

  const lines: string[] = [
    '# SNARC Memory Export',
    `Generated: ${new Date().toISOString()}`,
    `Observations: ${stats.observations} | Patterns: ${stats.patterns} | Sessions: ${stats.sessions}`,
    '',
  ];

  // Identity (Tier 3)
  if (identity.length > 0) {
    lines.push('## Identity', '');
    for (const fact of identity) {
      lines.push(`- **${fact.key}**: ${fact.value} (confidence: ${fact.confidence.toFixed(2)}, source: ${fact.source})`);
    }
    lines.push('');
  }

  // Patterns (Tier 2) grouped by kind
  if (patterns.length > 0) {
    lines.push('## Patterns', '');

    const byKind = new Map<string, any[]>();
    for (const p of patterns) {
      const list = byKind.get(p.kind) || [];
      list.push(p);
      byKind.set(p.kind, list);
    }

    for (const [kind, pats] of byKind) {
      lines.push(`### ${formatKind(kind)}`, '');
      for (const p of pats) {
        lines.push(`- **${p.summary}** (frequency: ${p.frequency}, confidence: ${p.confidence.toFixed(2)})`);
        if (p.detail) {
          try {
            const detail = JSON.parse(p.detail);
            for (const [k, v] of Object.entries(detail)) {
              lines.push(`  - ${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`);
            }
          } catch {
            lines.push(`  - ${p.detail}`);
          }
        }
      }
      lines.push('');
    }
  }

  return lines.join('\n');
}

export function importMarkdown(memory: EngramMemory, markdown: string): { imported: number } {
  // Simple parser — extract identity facts and pattern summaries
  let imported = 0;
  const lines = markdown.split('\n');
  let section = '';

  for (const line of lines) {
    if (line.startsWith('## Identity')) { section = 'identity'; continue; }
    if (line.startsWith('## Patterns')) { section = 'patterns'; continue; }
    if (line.startsWith('## ')) { section = ''; continue; }

    if (section === 'identity' && line.startsWith('- **')) {
      const match = line.match(/\*\*(.+?)\*\*:\s*(.+?)\s*\(confidence:\s*([\d.]+)/);
      if (match) {
        // Would call memory.upsertIdentity — but we need direct DB access
        // For MVP, just count what we'd import
        imported++;
      }
    }
  }

  return { imported };
}

function formatKind(kind: string): string {
  return kind.split('_').map(w => w[0].toUpperCase() + w.slice(1)).join(' ');
}
