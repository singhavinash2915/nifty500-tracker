import { useState } from 'react'
import { Plus } from 'lucide-react'
import { supabase } from '../lib/supabase'
import type { ScreenerRow } from '../types'

/**
 * Record a holding you already own.
 *
 * A stop is required, and that is deliberate rather than pedantic. A position
 * with no predetermined exit is how a six-month thesis quietly becomes a
 * two-year one, and every alert about risk — breached stop, distance to stop,
 * money still at risk — is uncomputable without it. If you genuinely have no
 * stop in mind, the honest number is the level at which you would admit the
 * idea was wrong.
 */
export function AddHolding({
  rows,
  onAdded,
}: {
  rows: ScreenerRow[]
  onAdded: () => void
}) {
  const [open, setOpen] = useState(false)
  const [symbol, setSymbol] = useState('')
  const [qty, setQty] = useState('')
  const [price, setPrice] = useState('')
  const [stop, setStop] = useState('')
  const [target, setTarget] = useState('')
  const [thesis, setThesis] = useState('')
  const [entryDate, setEntryDate] = useState(new Date().toISOString().slice(0, 10))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const match = rows.find((r) => r.symbol === symbol.toUpperCase())
  const numeric = (v: string) => (v.trim() === '' ? null : Number(v))

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    const entry = Number(price)
    const stopAt = Number(stop)
    if (!match) return setError(`${symbol.toUpperCase()} is not in the tracked universe`)
    if (!(entry > 0)) return setError('Entry price must be a positive number')
    if (!(stopAt > 0)) return setError('A stop is required')
    if (stopAt >= entry) return setError('The stop must sit below the entry price')

    setBusy(true)
    const { error } = await supabase!.from('positions').insert({
      symbol: match.symbol,
      entry_date: entryDate,
      entry_price: entry,
      quantity: Number(qty),
      stop_price: stopAt,
      target_price: numeric(target),
      thesis: thesis.trim() || null,
      setup: match.winning_setup === 'none' ? 'other' : match.winning_setup,
    })
    setBusy(false)

    if (error) return setError(error.message)
    setSymbol(''); setQty(''); setPrice(''); setStop(''); setTarget(''); setThesis('')
    setOpen(false)
    onAdded()
  }

  if (!supabase) {
    return (
      <p className="text-sm text-slate-500">
        Recording holdings needs a database connection.
      </p>
    )
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
      >
        <Plus className="h-4 w-4" /> Add a holding
      </button>
    )
  }

  const risk =
    Number(qty) > 0 && Number(price) > 0 && Number(stop) > 0 && Number(stop) < Number(price)
      ? (Number(price) - Number(stop)) * Number(qty)
      : null

  return (
    <form
      onSubmit={submit}
      className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900"
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Symbol">
          <input
            list="tracked-symbols" value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            required placeholder="TATACHEM" className={input}
          />
          <datalist id="tracked-symbols">
            {rows.slice(0, 600).map((r) => (
              <option key={r.symbol} value={r.symbol}>{r.company_name}</option>
            ))}
          </datalist>
        </Field>
        <Field label="Quantity">
          <input type="number" step="any" min="0" value={qty}
                 onChange={(e) => setQty(e.target.value)} required className={input} />
        </Field>
        <Field label="Entry price">
          <input type="number" step="any" min="0" value={price}
                 onChange={(e) => setPrice(e.target.value)} required className={input} />
        </Field>
        <Field label="Stop" hint="required">
          <input type="number" step="any" min="0" value={stop}
                 onChange={(e) => setStop(e.target.value)} required className={input} />
        </Field>
        <Field label="Target" hint="optional">
          <input type="number" step="any" min="0" value={target}
                 onChange={(e) => setTarget(e.target.value)} className={input} />
        </Field>
        <Field label="Entry date">
          <input type="date" value={entryDate}
                 onChange={(e) => setEntryDate(e.target.value)} className={input} />
        </Field>
      </div>

      <Field label="Why you bought it" hint="optional, but it is what you check against later">
        <input value={thesis} onChange={(e) => setThesis(e.target.value)}
               placeholder="reversal off the 610 zone, ROCE improving" className={input} />
      </Field>

      {match && (
        <p className="mt-2 text-xs text-slate-500">
          {match.company_name} &middot; {match.sector}
          {match.blended !== null && <> &middot; currently scores {match.blended.toFixed(0)}</>}
          {match.decile !== null && <> (decile {match.decile})</>}
        </p>
      )}
      {risk !== null && (
        <p className="mt-1 text-xs text-slate-500">
          Risk to the stop: <span className="font-mono">₹{risk.toLocaleString('en-IN')}</span>
          {' '}({((Number(price) / Number(stop) - 1) * 100).toFixed(1)}% away)
        </p>
      )}
      {error && (
        <p className="mt-3 rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
          {error}
        </p>
      )}

      <div className="mt-3 flex gap-2">
        <button type="submit" disabled={busy}
          className="rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900">
          {busy ? 'Saving…' : 'Save holding'}
        </button>
        <button type="button" onClick={() => setOpen(false)}
          className="rounded-md border border-slate-300 px-4 py-1.5 text-sm dark:border-slate-700">
          Cancel
        </button>
      </div>
    </form>
  )
}

const input =
  'mt-1 w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm outline-none dark:border-slate-700 dark:bg-slate-950'

function Field({
  label, hint, children,
}: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="mt-3 block text-sm sm:mt-0">
      <span className="font-medium">{label}</span>
      {hint && <span className="ml-1 text-xs text-slate-400">{hint}</span>}
      {children}
    </label>
  )
}
