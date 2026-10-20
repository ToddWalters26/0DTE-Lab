# 0DTE Desk (auto-updating)

This repo builds a simple SPY/AAPL 0DTE decision page and publishes it to **GitHub Pages**.

On weekdays it rebuilds around **9:50 AM, 10:05 AM, and 3:45 PM Eastern** (cron is in UTC; see workflow comments for EST adjustment).

## One-time setup

1. Create a new **private** or public GitHub repository (example name: `0dte-desk`).
2. Upload everything in this folder to the repo root:
   - `build_desk.py`
   - `.github/workflows/update-desk.yml`
   - this `README.md`
3. In the repo on GitHub go to **Settings → Pages**.
4. Under **Build and deployment**, set **Source** to **GitHub Actions**.
5. Go to the **Actions** tab. Open **Update 0DTE Desk** and click **Run workflow**.
6. When it finishes, open **Settings → Pages** again. Your site URL will look like:
   `https://YOUR_USERNAME.github.io/0dte-desk/`
7. On iPhone: open that URL in **Safari** → Share → **Add to Home Screen**.

## Daily use

- Open the Home Screen icon.
- The page is already rebuilt on the schedule above.
- For an extra rebuild anytime: Actions → Update 0DTE Desk → Run workflow, wait ~30–60 seconds, then refresh the page.

## Notes

- Scheduled GitHub Actions can lag a few minutes.
- In winter (Eastern Standard Time, UTC-5), edit the cron lines in the workflow and add 1 hour to each UTC time (e.g. 9:50 ET → `50 14 * * 1-5`).
- This is research/decision support only, not financial advice.
