# Aosom Air Conditioner Monitor

A free GitHub Actions monitor for all genuine air conditioners discovered in Aosom UK's Air Conditioning category.

## What it does

- Checks the full Air Conditioning category every five minutes.
- Follows category pagination automatically.
- Includes products whose titles contain `air conditioner` or `air conditioning unit`.
- Excludes air coolers.
- Detects restocks and newly added in-stock air conditioners.
- Sends Discord alerts only when an item changes from unavailable to available.
- Stores its memory in `state.json` to prevent duplicate alerts.

## Setup

1. Create a public GitHub repository named `aosom-air-conditioner-monitor`.
2. Upload `monitor.py`, `config.json`, `state.json`, and `README.md`.
3. Create `.github/workflows/monitor.yml` using GitHub's web editor if the hidden `.github` folder cannot be uploaded.
4. Create a Discord text channel, for example `#aosom-alerts`.
5. Create a Discord webhook for that channel and copy its URL.
6. In GitHub, open **Settings → Secrets and variables → Actions**.
7. Create a repository secret named `DISCORD_WEBHOOK_URL` and paste the webhook URL as its value.
8. Open **Actions → Aosom Air Conditioner Monitor → Run workflow**.
9. Tick the test-notification option and run it.
10. Run it once more without the test option to create the baseline.

## Important

The monitor depends on stock wording and product links exposed by Aosom's category pages. If Aosom substantially redesigns the site, the parser may need updating. A run fails visibly instead of silently overwriting the state when no products can be found.
