import { useState } from 'react'
import { Pencil, X } from 'lucide-react'
import { supabase } from '../lib/supabase'
import type { PositionView } from '../lib/load'

/**
 * Correct the recorded facts about a holding.
 *
 * Entry date, price and quantity: the three things typed once and then wrong
 * for good. Six of the seven holdings here were loaded from screenshots that
 * carried no purchase date, so they went in as the day they were entered and
 * every "held 65 days of ~180" was a fiction until this existed.
 *
 * The stop is not here on purpose. It moves through its own path, which refuses
 * to lower one — and a stop field on an edit form would route straight around
 * that rule, at exactly the moment somebody most wants it to.
 */
export function EditHolding({
  position,
  onSaved,
}: {
  position: PositionView
  onSaved: () => void
}) {
  const [open, setOpen] = useState(false)
  const [entryDate, setEntryDate] = useState(position.entry_date ?? '')
  const [price, setPrice] = useState(String(position.entry_price))
  const [qty, setQty] = useState(String(position.quantity))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    const entry = Number(price)
    const quantity = Number(qty)
    if (!entryDate) return setError('An entry date is required')
    if (!(entry > 0)) return setError('Entry price must be a positive number')
    if (!(quantity > 0)) return setError('Quantity must be a positive number')
    if (new Date(entryDate) > new Date()) return setError('That date is in the future')

    setBusy(true)
    const { error } = await supabase!
      .from('positions')
      .update({ entry_date: entryDate, entry_price: entry, quantity })
      .eq('id', position.id)
    setBusy(false)

    if (error) return setError(error.message)
    setOpen(false)
    onSaved()
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        title="Correct the entry date, price or quantity"
        className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
      >
        <Pencil className="h-3.5 w-3.5" />
      </button>
    )
  }

  return (
    <form
      onSubmit={save}
      className="mt-3 grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 sm:grid-cols-4 dark:border-slate-700 dark:bg-slate-950/40"
    >
      <Field label="Entry date">
        <input
          type="date"
          value={entryDate}
          max={new Date().toISOString().slice(0, 10)}
          onChange={(e) => setEntryDate(e.target.value)}
          className={inputClass}
        />
      </Field>
      <Field label="Entry price">
        <input
          type="number"
          step="0.01"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          className={inputClass}
        />
      </Field>
      <Field label="Quantity">
        <input
          type="number"
          step="any"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          className={inputClass}
        />
      </Field>

      <div className="flex items-end gap-2">
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
        >
          {busy ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded p-1.5 text-slate-500 hover:bg-slate-200 dark:hover:bg-slate-800"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {error && (
        <p className="text-sm text-red-700 sm:col-span-4 dark:text-red-400">{error}</p>
      )}
      <p className="text-xs text-slate-500 sm:col-span-4">
        The stop is changed from the command line, which will not lower one.
      </p>
    </form>
  )
}

const inputClass =
  'w-full rounded-md border border-slate-300 bg-white px-2 py-1 text-sm outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid gap-1">
      <span className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
        {label}
      </span>
      {children}
    </label>
  )
}
