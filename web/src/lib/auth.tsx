import { createContext, useContext, useEffect, useState } from 'react'
import type { Session } from '@supabase/supabase-js'
import { supabase } from './supabase'

/**
 * Sign-in state.
 *
 * A password, not a magic link. The link flow was abandoned for good reasons:
 * Supabase's built-in mailer allows two emails an hour, which is a wall rather
 * than a limit when a link opens in the wrong browser; and a link tapped from a
 * mail app opens inside *that app's* embedded browser, which keeps its own
 * storage, so the session lands somewhere the real browser cannot see. A
 * password has neither failure mode — it signs in whatever browser is already
 * in front of you, as many times as you like.
 *
 * Only the holdings are behind it. The screener, the charts and every score
 * stay open to anyone with the link, which is what they were always meant to
 * be. And the withholding happens in the database, not here: see migration
 * 0016. A gate in this file would stop someone who clicks a link and nobody
 * else, because the anon key is compiled into the bundle and can be read out
 * of it.
 *
 * The account's email is configuration rather than a field to fill in — there
 * is exactly one owner, and typing an address every time to prove you are the
 * only person who could be is friction with no security in it.
 */
export const OWNER_EMAIL =
  (import.meta.env.VITE_OWNER_EMAIL as string | undefined) ?? ''

interface AuthState {
  session: Session | null
  loading: boolean
  signIn: (password: string) => Promise<{ error: string | null }>
  signOut: () => Promise<void>
}

const Ctx = createContext<AuthState>({
  session: null,
  loading: true,
  signIn: async () => ({ error: 'auth is not configured' }),
  signOut: async () => {},
})

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!supabase) {
      setLoading(false)
      return
    }
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session)
      setLoading(false)
    })
    const { data: sub } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next)
      setLoading(false)
    })
    return () => sub.subscription.unsubscribe()
  }, [])

  const value: AuthState = {
    session,
    loading,
    async signIn(password: string) {
      if (!supabase) return { error: 'auth is not configured' }
      if (!OWNER_EMAIL) {
        return { error: 'VITE_OWNER_EMAIL is not set for this build' }
      }
      const { error } = await supabase.auth.signInWithPassword({
        email: OWNER_EMAIL,
        password,
      })
      // Supabase answers a wrong password with "Invalid login credentials",
      // which reads as though the account might not exist. There is only one
      // account, so say the thing that is actually true.
      if (error) {
        return {
          error: /invalid login credentials/i.test(error.message)
            ? 'Wrong password.'
            : error.message,
        }
      }
      return { error: null }
    },
    async signOut() {
      await supabase?.auth.signOut()
    },
  }

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useAuth = () => useContext(Ctx)
