import { createClient } from '@supabase/supabase-js'

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined

export const isConfigured = Boolean(url && anonKey)

// The browser only ever reads. All writes go through the Python ingestion
// jobs using the service-role key, which never reaches the client.
// The tracker's tables live in their own schema, not `public` — the project
// also hosts another application. PostgREST only serves schemas listed under
// Settings -> API -> Exposed schemas.
const schema = (import.meta.env.VITE_SUPABASE_SCHEMA as string | undefined) ?? 'n500'

// The type is inferred rather than annotated: SupabaseClient's default
// generics pin the schema to "public", which a client on another schema does
// not satisfy.
export const supabase = isConfigured
  ? createClient(url!, anonKey!, {
      db: { schema },
      auth: {
        // Signing in is meant to be a one-off per device. The access token
        // lasts an hour, but the refresh token rotates with no fixed expiry
        // and no inactivity timeout, so a persisted session renews itself
        // indefinitely. Stated explicitly rather than left to defaults,
        // because "do I have to log in again?" is the whole question.
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  : null
