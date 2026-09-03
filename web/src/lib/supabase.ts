import { createClient, type SupabaseClient } from '@supabase/supabase-js'

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined

export const isConfigured = Boolean(url && anonKey)

// The browser only ever reads. All writes go through the Python ingestion
// jobs using the service-role key, which never reaches the client.
export const supabase: SupabaseClient | null = isConfigured
  ? createClient(url!, anonKey!)
  : null
