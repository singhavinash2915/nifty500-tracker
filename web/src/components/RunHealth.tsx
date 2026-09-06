import { AlertTriangle, CircleAlert } from 'lucide-react'
import type { RunHealth as Health } from '../lib/load'

/**
 * A banner when the last build cannot be trusted.
 *
 * Deliberately loud, and deliberately different from the "showing the exported
 * snapshot" notice beside it. That one means the data is *old*; this one means
 * the data may be *wrong*, and the two want opposite reactions — one is fine to
 * work around, the other is not.
 *
 * Silence when everything passed. A banner that is always present is furniture,
 * and furniture does not get read.
 */
export function RunHealth({ health }: { health: Health | null }) {
  if (!health) return null

  const failed = health.status === 'failed' || health.status === 'partial'
  const stale = health.age_days !== null && health.age_days > STALE_DAYS
  const neverFinished = health.status === 'running' && (health.age_days ?? 0) >= 1
  if (!failed && !stale && !neverFinished) return null

  const tone = failed || neverFinished ? 'bad' : 'warn'
  return (
    <div
      className={`mb-6 flex gap-3 rounded-md border p-4 text-sm ${
        tone === 'bad'
          ? 'border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200'
          : 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200'
      }`}
    >
      {tone === 'bad' ? (
        <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
      ) : (
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      )}
      <div>
        <p className="font-semibold">{headline(health, { failed, stale, neverFinished })}</p>

        {health.notes && <p className="mt-1">{health.notes}</p>}

        {health.errors.length > 0 && (
          <ul className="mt-2 grid gap-0.5">
            {health.errors.slice(0, 5).map((e, i) => (
              <li key={i} className="font-mono text-xs">
                {e.symbol ? `${e.symbol}: ` : ''}
                {e.message}
              </li>
            ))}
          </ul>
        )}

        <p className="mt-2 text-xs opacity-80">
          {failed || neverFinished
            ? 'These are checks that must pass on any correct run, so this is not staleness — something is wrong with what is on screen.'
            : `Last verified ${health.age_days} days ago. The nightly job only runs while the machine is awake.`}
        </p>
      </div>
    </div>
  )
}

/** Beyond this the pipeline has stopped and nobody noticed. */
const STALE_DAYS = 4

function headline(
  health: Health,
  { failed, stale, neverFinished }: { failed: boolean; stale: boolean; neverFinished: boolean },
) {
  if (neverFinished) return 'The last build started and never finished'
  if (failed) return 'The last build failed its own checks — do not act on this screen'
  if (stale) return `Nothing has been verified for ${health.age_days} days`
  return 'Build health'
}
