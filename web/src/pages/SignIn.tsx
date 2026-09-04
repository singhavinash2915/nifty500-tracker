import { useState } from 'react'
import { Mail } from 'lucide-react'
import { useAuth } from '../lib/auth'

export function SignIn() {
  const { signIn } = useAuth()
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const { error } = await signIn(email.trim())
    setBusy(false)
    if (error) setError(error)
    else setSent(true)
  }

  return (
    <div className="mx-auto mt-16 max-w-md">
      <h1 className="text-2xl font-bold tracking-tight">Nifty 500 Conviction Tracker</h1>
      <p className="mt-2 text-slate-600 dark:text-slate-400">
        Sign in to continue. Every table requires a signed-in user, so there is nothing
        to see until you do.
      </p>

      {sent ? (
        <div className="mt-6 rounded-md border border-emerald-300 bg-emerald-50 p-4 text-sm text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200">
          <p className="font-semibold">Check your email</p>
          <p className="mt-1">
            A sign-in link is on its way to <span className="font-mono">{email}</span>.
            Opening it on this device signs you in here.
          </p>
        </div>
      ) : (
        <form onSubmit={submit} className="mt-6">
          <label className="block text-sm font-medium" htmlFor="email">
            Email
          </label>
          <div className="mt-1 flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900">
            <Mail className="h-4 w-4 text-slate-400" />
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              className="w-full bg-transparent text-sm outline-none placeholder:text-slate-400"
            />
          </div>
          <button
            type="submit"
            disabled={busy || !email.trim()}
            className="mt-3 w-full rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900"
          >
            {busy ? 'Sending…' : 'Email me a sign-in link'}
          </button>
          {error && (
            <p className="mt-3 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
              {error}
            </p>
          )}
          <p className="mt-4 text-xs text-slate-500">
            No password — the link is the credential, and it expires. For personal
            research. Not investment advice.
          </p>
        </form>
      )}
    </div>
  )
}
