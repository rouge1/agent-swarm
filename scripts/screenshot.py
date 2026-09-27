#!/usr/bin/env python3
"""Generic headless screenshot/playtest runner (Playwright + chromium).

Drives any URL through a small JSON script of steps and saves screenshots,
console messages and page errors -- for reviewers and managers to sanity
check a running app without writing a one-off Playwright script each time.

Usage:
  screenshot.py URL --out DIR [--size 900x1000] [--video] [--steps steps.json]

steps.json is a JSON list of single-key step objects, applied in order:
  {"wait": 1000}          page.wait_for_timeout(ms)
  {"shot": "name"}        screenshot to DIR/<NN>-name.png
  {"down": "Space"}       keyboard.down(key)
  {"up": "Space"}         keyboard.up(key)
  {"press": "p"}          keyboard.press(key)
  {"click": "selector"}   page.click(selector)
  {"goto": "url"}         page.goto(url) -- e.g. to reload with a query string
  {"eval": "js"}          page.evaluate(js); the result is ignored, errors are not

Multiple keys in one step object are applied in the (insertion) order they
appear, so e.g. {"down": "Space", "wait": 500} both fires in one step.

With no --steps, a single "loaded" screenshot is taken 1s after page load.

Writes DIR/playtest.json ({"console": [...], "errors": [...], "shots": [...]})
and also prints it, so a caller can pipe this straight into a report.
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

STEP_HANDLERS = ("wait", "shot", "down", "up", "press", "click", "goto", "eval")


def parse_size(s: str) -> dict:
    w, h = s.lower().split("x")
    return {"width": int(w), "height": int(h)}


def run_step(page, out: Path, log: dict, step: dict) -> None:
    """Apply every recognized key in one step object, in the order it was written."""
    for key in step:
        val = step[key]
        if key == "wait":
            page.wait_for_timeout(int(val))
        elif key == "shot":
            path = out / f"{len(log['shots']):02d}-{val}.png"
            page.screenshot(path=str(path))
            log["shots"].append(str(path))
        elif key == "down":
            page.keyboard.down(val)
        elif key == "up":
            page.keyboard.up(val)
        elif key == "press":
            page.keyboard.press(val)
        elif key == "click":
            page.click(val)
        elif key == "goto":
            page.goto(val)
        elif key == "eval":
            page.evaluate(val)
        else:
            raise SystemExit(f"unknown step key {key!r} (known: {', '.join(STEP_HANDLERS)})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--out", default="shots/latest")
    ap.add_argument("--size", default="900x1000", help="WIDTHxHEIGHT viewport, e.g. 1280x800")
    ap.add_argument("--video", action="store_true", help="also record a .webm of the run")
    ap.add_argument("--steps", help="JSON file with a list of step objects (see module docstring)")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    size = parse_size(a.size)
    steps = json.loads(Path(a.steps).read_text()) if a.steps else [{"wait": 1000}, {"shot": "loaded"}]

    log = {"console": [], "errors": [], "shots": []}

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            # some environments ship no bundled chromium but do have system chrome
            browser = p.chromium.launch(channel="chrome")
        ctx_args = {"viewport": size, "device_scale_factor": 1}
        if a.video:
            ctx_args["record_video_dir"] = str(out)
            ctx_args["record_video_size"] = size
        ctx = browser.new_context(**ctx_args)
        page = ctx.new_page()
        page.on("console", lambda m: log["console"].append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: log["errors"].append(str(e)))

        page.goto(a.url)
        for step in steps:
            run_step(page, out, log, step)

        ctx.close()
        browser.close()

    (out / "playtest.json").write_text(json.dumps(log, indent=1))
    print(json.dumps(log, indent=1))


if __name__ == "__main__":
    main()
