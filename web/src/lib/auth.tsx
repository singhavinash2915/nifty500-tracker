import { createContext, useContext, useEffect, useState } from 'react'
import type { Session } from '@supabase/supabase-js'
import { supabase } from './supabase'

/**
 * Sign-in state.
 *
 * A magic link rather than a password: there is one account, the link is one
 * click, and nothing has to store or transmit a password. It also keeps the
 * failure modes small — no reset flow, no strength rules, no leaked reuse.
 *
 * Every table now requires an authenticated role, so this is not decoration.
 * Without a session the app can read nothing at all.
 */
interface AuthState {
  session: Session | null
  loading: boolean
  signIn: (email: string) => Promise<{ error: string | null }>
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
    async signIn(email: string) {
      if (!supabase) return { error: 'auth is not configured' }
      const { error } = await supabase.auth.signInWithOtp({
        email,
        options: {
          // Hash routing means the redirect must land on the app root; the
          // router takes over from there.
          emailRedirectTo: window.location.origin + import.meta.env.BASE_URL,
        },
      })
      return { error: error?.message ?? null }
    },
    async signOut() {
      await supabase?.auth.signOut()
    },
  }

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useAuth = () => useContext(Ctx)
