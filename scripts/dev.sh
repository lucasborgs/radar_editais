#!/usr/bin/env bash
#
# dev.sh — start the local Supabase stack for Radar de Editais development.
#
# Requires:
#   - Docker running
#   - Supabase CLI installed (https://supabase.com/docs/guides/cli)
#
# After this script finishes, run `supabase status` to grab the local URL,
# anon key, service role key and JWT secret, and copy them into your `.env`.
set -e

supabase start

echo "Supabase running. Copy values from \`supabase status\` into .env"
