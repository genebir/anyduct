/**
 * Cron helpers — Phase ADG (2026-06-04).
 *
 * Extracted from the schedules list (ADF) so every surface that shows
 * a raw cron expression can offer the same human-readable tooltip,
 * using the same ``cronstrue`` library as the builder's CronInput.
 */

import cronstrue from "cronstrue";

/** Human-readable description of a cron expression, e.g.
 *  ``"0 2 * * *"`` → ``"At 02:00 AM"``. Returns ``undefined`` on parse
 *  failure so it can be spread straight onto a ``title`` attribute
 *  without painting a broken string. */
export function cronHuman(cron: string | null | undefined): string | undefined {
  if (!cron) return undefined;
  try {
    return cronstrue.toString(cron, { verbose: true });
  } catch {
    return undefined;
  }
}
