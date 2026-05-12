# Stock watchlist pipeline — setup guide

## 1. Create a new GitLab repository and push this folder

```bash
git init
git add .
git commit -m "initial: stock watchlist pipeline"
git remote add origin https://gitlab.com/YOUR_USERNAME/stock-watchlist.git
git push -u origin main
```

## 2. Enable GitLab Pages
Settings → Pages → ensure Pages is enabled for the project.
Your report URL will be: `https://YOUR_USERNAME.gitlab.io/stock-watchlist/`

## 3. Set CI/CD variables
Settings → CI/CD → Variables → Add variable (mask all of these):

| Variable              | Required | Description                                      |
|-----------------------|----------|--------------------------------------------------|
| `ANTHROPIC_API_KEY`   | Optional | Claude Haiku for thesis summaries (pennies/run)  |
| `NEWS_API_KEY`        | Optional | newsapi.org free tier (100 calls/day)            |
| `ALPHA_VANTAGE_KEY`   | Optional | alphavantage.co free tier (25 calls/day)         |
| `GMAIL_USER`          | Required | sender Gmail address                             |
| `GMAIL_APP_PASSWORD`  | Required | Gmail App Password (not your login password)     |
| `NOTIFY_EMAIL`        | Required | your email address to receive digests            |

**Gmail App Password:**
Google Account → Security → 2-Step Verification → App passwords → Create

## 4. Schedule the pipeline
CI/CD → Schedules → New schedule:
- Description: Weekly watchlist digest
- Cron: `0 6 * * 0`  (Sunday 06:00 UTC — adjust to your timezone)
- Target branch: main

## 5. Add / remove tickers
Edit `config/watchlist.yaml` and add a block:
```yaml
  - ticker: TSLA
    archetype: largeg          # mega / largeg / smallg / spec / micro
    strategy: [growth]
    sector: Automotive
    notes: Your notes here.
```

Then add the thesis score entry in `config/thesis_scores.yaml`:
```yaml
TSLA:
  last_updated: "2026-05-12"
  fundamentals: 7
  valuation: 5
  moat: 8
  management: 7
  partnerships: 6
  macro: 6
  risk: 5
  overall_conviction: 7
  thesis_one_liner: null   # set to null to let AI generate, or write your own
```

## 6. Run locally for testing
```bash
pip install -r requirements.txt
python src/main.py --no-email            # full run, no email
python src/main.py --ticker MRAM --no-email  # single ticker debug
```
Open `public/index.html` in your browser to preview the report.
Open `public/email_preview.html` to preview the email.

## Architecture
```
main.py
├── fetcher.py    → yfinance + NewsAPI + FRED (data)
├── scorer.py     → archetype-aware category scores + bull/bear flags
├── signals.py    → technical signal + overnight flip detection
├── thesis_ai.py  → Claude Haiku one-liner (falls back to rule-based)
├── report.py     → detailed HTML → public/index.html (GitLab Pages)
└── emailer.py    → TLDR email → Gmail SMTP
```

## Two-cadence model (the key design decision)
- **Thesis score** — from `thesis_scores.yaml`, updates monthly after earnings.
  Does NOT change with daily price moves. This prevents MRAM-style whipsaw.
- **Technical signal** — computed fresh every run from live price data.
  Compared to yesterday's cached signal to detect overnight flips.
- **Divergence insight** — generated when thesis ≠ signal. If MRAM drops 7%
  and flips to RISK WATCH but thesis score is 7.5/10, you get:
  "Thesis intact. Micro-cap pullback is normal volatility. Do not exit on
  technical flip alone."

## Cost estimate (monthly)
| Service         | Usage                    | Cost     |
|-----------------|--------------------------|----------|
| yfinance        | 14 tickers × 4 runs/mo   | Free     |
| NewsAPI         | ~56 calls/month          | Free     |
| FRED            | ~20 calls/month          | Free     |
| GitLab CI       | ~20 min/month            | Free     |
| GitLab Pages    | Static hosting           | Free     |
| Claude Haiku    | 14 tickers × 4 runs/mo   | ~$0.02   |
| Gmail SMTP      | 4 emails/month           | Free     |
| **Total**       |                          | **~$0.02/month** |
