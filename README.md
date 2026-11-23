# Physio Digest

Daily physiotherapy research (RCTs, systematic reviews, meta-analyses) from PubMed, 2-3 studies a day across
specializations (Orthopaedic, Neurological, Sports, Cardiopulmonary, Paediatric, Geriatric, Women's health, Pain),
in plain English and Hindi.

## Setup (one time)
1. Create a GitHub repo and upload all files, keeping the folder structure (including the hidden `.github` folder).
2. Settings > Pages > Source: "Deploy from a branch", branch `main`, folder `/ (root)`.
3. Free AI summaries: get a key from https://aistudio.google.com/apikey (Gemini free tier), then
   Settings > Secrets and variables > Actions > New repository secret: name `GEMINI_API_KEY`.
   (Optional paid alternative: `ANTHROPIC_API_KEY`.) With no key, the page shows English abstract conclusions only.
4. Actions tab > "Daily physio digest" > Run workflow to test. After that it runs daily at 7:00 AM IST.

## Customise
- Papers per day: `ENTRIES_PER_DAY` in `.github/workflows/daily.yml`.
- Specializations and their keywords: `SPECIALIZATIONS` in `scripts/fetch_daily.py`.
- Search query: `QUERY` in the same file.
- Gemini model name: set `GEMINI_MODEL` in the workflow if the default is retired.
