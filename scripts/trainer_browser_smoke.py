#!/usr/bin/env python3
"""Browser smoke test for the trainer's cinematic asset and detail routes.

The test expects a running training-challenges service. In development mode it
can use any learner ID/token because the challenge service deliberately skips
learner enrollment checks. In strict mode, pass a real enrolled learner token.

Usage:
  python3 scripts/trainer_browser_smoke.py
  python3 scripts/trainer_browser_smoke.py --base-url http://127.0.0.1:8060

Install locally with the existing Python environment:
  python3 -m pip install playwright
  python3 -m playwright install chromium
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright


STAGE_COVERS = (
    "stage-l00-foundation",
    "stage-l01-recon",
    "stage-l02-prompt-injection",
    "stage-l03-rag",
    "stage-l04-agent-protocols",
    "stage-l05-memory",
    "stage-l06-identity",
    "stage-l07-supply-chain",
    "stage-l08-detection-evasion",
    "stage-l09-apt-capstone",
)


def cover_assets() -> list[str]:
    return ["hero-operative.png", *[stage + ".png" for stage in STAGE_COVERS]]


def challenge_assets() -> list[str]:
    return [f"challenge-{ordinal:02d}.png" for ordinal in range(1, 101)]


def flow_assets() -> list[str]:
    return [stage + "-flow.svg" for stage in STAGE_COVERS]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("TRAINER_BASE_URL", "http://127.0.0.1:8060"))
    parser.add_argument("--learner", default=os.environ.get("TRAINER_SMOKE_LEARNER", "browser-smoke"))
    parser.add_argument("--token", default=os.environ.get("TRAINER_SMOKE_TOKEN", "browser-smoke-token"))
    parser.add_argument("--timeout-ms", type=int, default=int(os.environ.get("TRAINER_SMOKE_TIMEOUT_MS", "15000")))
    parser.add_argument("--screenshot-dir", type=Path, default=None)
    parser.add_argument("--skip-login", action="store_true", help="Use only when the page is pre-authenticated by a test fixture")
    parser.add_argument("--mock-gate", action="store_true", help="Stub the optional Training Gate curriculum request")
    return parser.parse_args()


def assert_cover_response(context: Any, url: str) -> None:
    response = context.request.get(url)
    assert response.ok, f"{url} returned HTTP {response.status}"
    content_type = response.headers.get("content-type", "")
    body = response.body()
    if content_type.startswith("image/jpeg"):
        assert body[:2] == b"\xff\xd8", f"{url} is not JPEG-encoded raster art"
    elif content_type.startswith("image/png"):
        assert body[:8] == b"\x89PNG\r\n\x1a\n", f"{url} is not PNG-encoded raster art"
    else:
        raise AssertionError(f"{url} returned unsupported image type {content_type!r}")
    assert len(body) > 1000, f"{url} returned an unexpectedly small image"


def run_smoke(args: argparse.Namespace) -> None:
    base_url = args.base_url.rstrip("/")
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_responses: list[str] = []
    failed_requests: list[str] = []

    def same_origin(url: str) -> bool:
        return url.startswith(base_url)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page: Page = context.new_page()
        if args.mock_gate:
            page.route(
                f"{args.base_url.rstrip('/')}/__smoke_gate__/**",
                lambda route: route.fulfill(status=200, content_type="application/json", body='{"stages":[],"bank_profile":null}'),
            )
            page.route(
                "http://127.0.0.1:5050/api/curriculum**",
                lambda route: route.fulfill(status=200, content_type="application/json", body='{"stages":[],"bank_profile":null}'),
            )
        def record_console(message: Any) -> None:
            location = message.location.get("url", "")
            if message.type == "error" and (not location or same_origin(location)):
                console_errors.append(f"{message.text} ({location})")

        def record_response(response: Any) -> None:
            if response.status >= 400 and same_origin(response.url):
                failed_responses.append(f"{response.status} {response.url}")

        def record_request_failure(request: Any) -> None:
            if same_origin(request.url):
                failed_requests.append(f"{request.url}: {request.failure}")

        page.on("console", record_console)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("response", record_response)
        page.on("requestfailed", record_request_failure)

        try:
            page.goto(f"{base_url}/#/login", wait_until="domcontentloaded", timeout=args.timeout_ms)
            if not args.skip_login:
                page.locator("#learner-id").fill(args.learner)
                page.locator("#learner-token").fill(args.token)
                page.locator("#login-btn").click()
            expect(page.locator("#challenge-grid .challenge-card").first).to_be_visible(timeout=args.timeout_ms)

            cards = page.locator("#challenge-grid .challenge-card")
            assert cards.count() > 0, "challenge grid rendered no cards"

            for flow in flow_assets():
                response = context.request.get(f"{base_url}/assets/flows/{flow}")
                assert response.ok, f"{flow} returned HTTP {response.status}"
                assert response.headers.get("content-type", "").startswith("image/svg+xml"), f"{flow} was not SVG"
                assert len(response.body()) > 1000, f"{flow} returned an unexpectedly small image"

            for asset in cover_assets():
                assert_cover_response(context, f"{base_url}/assets/covers/{asset}")
            for asset in challenge_assets():
                assert_cover_response(context, f"{base_url}/assets/covers/{asset}")

            hero_image = page.locator("#hero-operative-art")
            assert hero_image.get_attribute("src").endswith("hero-operative.png")
            grid_images = page.locator("#challenge-grid .cover-art")
            assert grid_images.count() > 0, "grid rendered no cover images"
            grid_sources = [grid_images.nth(index).get_attribute("src") for index in range(grid_images.count())]
            assert len(set(grid_sources)) == len(grid_sources), "visible challenge cards do not have unique numbered art paths"
            for index in range(grid_images.count()):
                image = grid_images.nth(index)
                expect(image).to_be_visible(timeout=args.timeout_ms)
                assert image.evaluate("img => img.complete && img.naturalWidth > 0"), f"grid cover {index} did not load"

            cards.first.click()
            expect(page.locator("#detail-hero")).to_be_visible(timeout=args.timeout_ms)
            expect(page.locator("#detail-hero .detail-illus .cover-art")).to_be_visible(timeout=args.timeout_ms)
            expect(page.locator("#detail-flow .flow-art")).to_be_visible(timeout=args.timeout_ms)
            flow_image = page.locator("#detail-flow .flow-art")
            assert flow_image.evaluate("img => img.complete && img.naturalWidth > 0"), "attack-flow image did not load"
            assert "assets/flows/" in (flow_image.get_attribute("src") or ""), "detail flow image path missing"
            detail_image = page.locator("#detail-hero .detail-illus .cover-art")
            assert detail_image.evaluate("img => img.complete && img.naturalWidth > 0"), "detail cover did not load"
            assert "/#/challenge/" in page.url, f"detail route did not open: {page.url}"

            if args.screenshot_dir:
                args.screenshot_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.screenshot_dir / "challenge-detail-generated-art.png"), full_page=True)

            assert not page_errors, f"page errors: {page_errors}"
            assert not console_errors, f"console errors: {console_errors}"
            assert not failed_responses, f"HTTP failures: {failed_responses}"
            assert not failed_requests, f"network failures: {failed_requests}"
            print(f"PASS trainer browser smoke: {cards.count()} cards, {len(challenge_assets())} numbered challenge routes, {len(flow_assets())} attack-flow assets, detail route verified")
        finally:
            browser.close()


def main() -> int:
    args = parse_args()
    try:
        run_smoke(args)
    except (AssertionError, PlaywrightError) as error:
        print(f"FAIL trainer browser smoke: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
