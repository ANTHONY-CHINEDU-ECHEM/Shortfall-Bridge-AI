"""Capture README screenshots of a running deployment with Playwright.

Run after ingesting the full corpus so the images show real data:

    pip install playwright && python -m playwright install chromium
    python scripts/capture_screenshots.py --base http://localhost:8080 --out docs/screenshots
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--out", type=Path, default=Path("docs/screenshots"))
    ap.add_argument("--drug", default="furosemide")
    ap.add_argument("--route", default="intravenous")
    ap.add_argument("--dose", default="40")
    ap.add_argument("--crcl", default="42")
    ap.add_argument("--med", default="lisinopril")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in ("light", "dark"):
            ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=2, color_scheme=theme)
            page = ctx.new_page()
            page.goto(f"{args.base}/#/workbench")
            page.evaluate(f"localStorage.setItem('sfb-theme', '{theme}')")
            page.reload()
            page.fill("#drug-input", args.drug)
            page.keyboard.press("Escape")
            page.select_option("select >> nth=0", args.route)
            page.fill("input[aria-label='Dose amount']", args.dose)
            page.fill("label:has-text('CrCl') + input", args.crcl)
            page.fill("input[placeholder='Add a medicine']", args.med)
            page.keyboard.press("Enter")
            page.click("button[type=submit]")
            page.wait_for_selector(".options", timeout=60_000)
            page.wait_for_timeout(400)
            page.screenshot(path=str(args.out / f"workbench_{theme}.png"), full_page=False)
            if theme == "light":
                page.click(".option__body .btn--secondary")
                page.wait_for_selector(".drawer")
                page.wait_for_timeout(300)
                page.screenshot(path=str(args.out / "evidence_drawer.png"))
                page.keyboard.press("Escape")
                page.goto(f"{args.base}/#/shortages")
                page.wait_for_selector(".table, .empty", timeout=30_000)
                page.screenshot(path=str(args.out / "shortage_monitor.png"))
                page.goto(f"{args.base}/#/method")
                page.wait_for_selector(".stats", timeout=30_000)
                page.screenshot(path=str(args.out / "method.png"), full_page=True)
                mobile = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3)
                mp = mobile.new_page()
                mp.goto(f"{args.base}/#/workbench")
                mp.screenshot(path=str(args.out / "mobile_workbench.png"))
                mobile.close()
            ctx.close()
        browser.close()
    print(f"screenshots written to {args.out}")


if __name__ == "__main__":
    main()
