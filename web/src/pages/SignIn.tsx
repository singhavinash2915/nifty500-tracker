import { useState } from 'react'
import { Mail } from 'lucide-react'
import { useAuth } from '../lib/auth'

export function SignIn() {
  const { signIn, verifyCode } = useAuth()
  const [code, setCode] = useState('')
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submitCode(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const { error } = await verifyCode(email.trim(), code)
    setBusy(false)
    // On success the auth listener swaps this screen out; nothing else to do.
    if (error) setError(error)
  }

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
        <form onSubmit={submitCode} className="mt-6">
          <div className="rounded-md border border-emerald-300 bg-emerald-50 p-4 text-sm text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200">
            <p className="font-semibold">Check your email</p>
            <p className="mt-1">
              Sent to <span className="font-mono">{email}</span>. It contains a link and a
              six-digit code.
            </p>
            <p className="mt-2">
              <strong>On a phone, use the code.</strong> Tapping the link opens it inside
              your mail app's own browser, which signs you in there rather than here.
            </p>
          </div>

          <label className="mt-4 block text-sm font-medium" htmlFor="code">
            Six-digit code
          </label>
          <input
            id="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]*"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 8))}
            placeholder="123456"
            className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-center font-mono text-lg tracking-[0.3em] outline-none dark:border-slate-700 dark:bg-slate-900"
          />
          <button
            type="submit"
            disabled={busy || code.length < 6}
            className="mt-3 w-full rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900"
          >
            {busy ? 'Signing in…' : 'Sign in with the code'}
          </button>
          {error && (
            <p className="mt-3 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
              {error}
            </p>
          )}
          <button
            type="button"
            onClick={() => { setSent(false); setError(null); setCode('') }}
            className="mt-4 text-xs text-slate-500 underline underline-offset-2"
          >
            Use a different email
          </button>
        </form>
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
            No password. You will get a link and a six-digit code — on a phone, use the
            code. For personal research. Not investment advice.
          </p>
        </form>
      )}
    </div>
  )
}
