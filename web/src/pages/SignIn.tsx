import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Lock } from 'lucide-react'
import { useAuth } from '../lib/auth'

/**
 * The gate in front of the holdings, and nothing else.
 *
 * One field. There is a single account and its address is baked into the
 * build, so asking for an email would be a second thing to type that proves
 * nothing — the only question is whether you know the password.
 */
export function SignIn() {
  const { signIn } = useAuth()
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!password) return
    setBusy(true)
    setError(null)
    const { error } = await signIn(password)
    setBusy(false)
    if (error) setError(error)
    // On success the auth listener swaps this screen for the portfolio; there
    // is nothing to navigate to and nothing to reset.
  }

  return (
    <div className="mx-auto max-w-sm">
      <Link
        to="/"
        className="mb-6 inline-block text-sm text-slate-500 hover:text-slate-900 dark:hover:text-slate-100"
      >
        &larr; Screener
      </Link>

      <div className="rounded-md border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-4 flex items-center gap-2">
          <Lock className="h-4 w-4 text-slate-400" />
          <h1 className="text-lg font-semibold tracking-tight">Positions are private</h1>
        </div>

        <p className="mb-5 text-sm text-slate-600 dark:text-slate-400">
          The screener is open to anyone with the link. What you hold, what you paid
          and where your stops sit are not.
        </p>

        <form onSubmit={submit} className="grid gap-3">
          {/* Present but hidden: without it a password manager has nothing to
              file the entry under, and offers to save it against the wrong site. */}
          <input
            type="email"
            name="email"
            autoComplete="username"
            value={import.meta.env.VITE_OWNER_EMAIL ?? ''}
            readOnly
            hidden
          />
          <label className="grid gap-1">
            <span className="font-mono text-[11px] uppercase tracking-wider text-slate-500">
              Password
            </span>
            <input
              type="password"
              value={password}
              autoFocus
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950"
            />
          </label>

          <button
            type="submit"
            disabled={busy || !password}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
          >
            {busy ? 'Checking…' : 'Unlock'}
          </button>
        </form>

        {error && (
          <p className="mt-3 text-sm text-red-700 dark:text-red-400">{error}</p>
        )}

        <p className="mt-5 border-t border-slate-200 pt-4 text-xs text-slate-500 dark:border-slate-800">
          Signing in is a one-off per device. The session renews itself, so this
          screen should not come back unless you sign out or clear the browser.
        </p>
      </div>
    </div>
  )
}
