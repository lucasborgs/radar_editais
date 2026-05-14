#!/usr/bin/env bash
# First-deploy checklist for Fly.io. Run each line manually and verify.
set -e
echo "1. Install flyctl if needed:  curl -L https://fly.io/install.sh | sh"
echo "2. Login:  fly auth login"
echo "3. Launch (only the first time):  fly launch --no-deploy --name radar-editais --region gru"
echo "4. Set secrets:"
echo "     fly secrets set OPENAI_API_KEY=<from openai.com>"
echo "     fly secrets set SUPABASE_URL=<from supabase project>"
echo "     fly secrets set SUPABASE_ANON_KEY=<from supabase project>"
echo "     fly secrets set SUPABASE_SERVICE_KEY=<from supabase project>"
echo "     fly secrets set SUPABASE_JWT_SECRET=<from supabase project>"
echo "5. Deploy:  fly deploy"
echo "6. Tail logs:  fly logs"
echo ""
echo "Frontend (Vercel): connect repo at vercel.com, set NEXT_PUBLIC_API_URL to the fly app URL"
