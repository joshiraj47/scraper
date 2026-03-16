"""
LinkedIn job scraper module.

Uses Playwright with a **persistent browser profile** so that LinkedIn sees a
real, logged-in user session rather than a fresh headless bot.  The user logs
in once interactively (via ``--login``), and all subsequent automated runs
reuse the saved cookies/session.
"""

import logging
import random
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, BrowserContext

logger = logging.getLogger(__name__)

# Default persistent profile location (relative to project root)
DEFAULT_PROFILE_DIR = str(Path(__file__).parent / "browser_profile")


class LoginRequiredError(Exception):
    """Raised when LinkedIn redirects to a login/authwall page."""


class ScrapeError(Exception):
    """Raised on unexpected scraping failures."""


@dataclass
class JobListing:
    title: str
    company: str
    location: str
    description: str
    job_url: str
    posted_time: str = ""


# ---------------------------------------------------------------------------
# Human-like helpers
# ---------------------------------------------------------------------------

def _random_delay(low: float = 0.8, high: float = 2.5) -> None:
    """Sleep a random duration to mimic human pacing."""
    time.sleep(random.uniform(low, high))


def _human_scroll(page, scrolls: int = 4) -> None:
    """Scroll down in variable increments like a real user."""
    for _ in range(scrolls):
        distance = random.randint(400, 900)
        page.mouse.wheel(0, distance)
        _random_delay(1.0, 2.5)


# ---------------------------------------------------------------------------
# Persistent browser context
# ---------------------------------------------------------------------------

def _launch_persistent_context(
    pw,
    profile_dir: str,
    headless: bool,
    page_timeout: int,
) -> BrowserContext:
    """
    Launch Chromium with a persistent user-data directory.
    Cookies, localStorage, and session data survive across runs.
    """
    context = pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=headless,
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="Asia/Kolkata",
        args=[
            "--disable-blink-features=AutomationControlled",
        ],
    )
    context.set_default_timeout(page_timeout * 1000)
    return context


# ---------------------------------------------------------------------------
# Interactive login (one-time setup)
# ---------------------------------------------------------------------------

def interactive_login(profile_dir: str = DEFAULT_PROFILE_DIR) -> None:
    """
    Open a visible browser so the user can log into LinkedIn manually.
    The session is saved to *profile_dir* for future headless runs.
    """
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    logger.info("Opening browser for LinkedIn login — profile: %s", profile_dir)
    logger.info("Please log in to LinkedIn, then close the browser window.")

    with sync_playwright() as pw:
        context = _launch_persistent_context(
            pw,
            profile_dir=profile_dir,
            headless=False,  # must be visible for manual login
            page_timeout=120,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.linkedin.com/login")

        # Wait until the user logs in and we detect the feed or search page
        try:
            page.wait_for_url("**/feed/**", timeout=300_000)  # 5 min window
            logger.info("Login detected — saving session.")
        except Exception:
            logger.info("Browser closed — session saved (if login was completed).")

        context.close()

    logger.info("Session saved to %s. You can now run the scraper in headless mode.", profile_dir)


# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------

LINKEDIN_PAGE_SIZE = 25  # LinkedIn shows 25 jobs per page


def _build_page_url(base_url: str, page: int) -> str:
    """
    Append or update the ``start`` query parameter for pagination.
    Page 1 → start=0, Page 2 → start=25, etc.
    """
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

    parsed = urlparse(base_url)
    params = parse_qs(parsed.query)
    params["start"] = [str((page - 1) * LINKEDIN_PAGE_SIZE)]
    new_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _has_next_page(page) -> bool:
    """Check if a 'Next' pagination button exists and is enabled."""
    try:
        next_btn = page.query_selector(
            "button[aria-label='View next page'], "
            "button[aria-label='Page forward'], "
            "button.artdeco-pagination__button--next, "
            "li.artdeco-pagination__indicator--number:last-child"
        )
        if next_btn and next_btn.is_enabled():
            return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Right-panel detail extractor
# ---------------------------------------------------------------------------

# Selectors for clickable job cards in the left panel
# Verified against real authenticated LinkedIn HTML (Mar 2026).
# The inner div.job-card-container is the clickable element;
# each lives inside li.scaffold-layout__list-item[data-occludable-job-id].
_CARD_SELECTORS = (
    "li.scaffold-layout__list-item div.job-card-container"
)

# Selectors for the right-side detail panel
_DETAIL_SELECTORS = {
    "title": (
        "h1.t-24, "                                      # authenticated
        "h2.t-24, "
        "h1.jobs-unified-top-card__job-title, "
        "h2.jobs-unified-top-card__job-title, "
        "h1.topcard__title, "                             # public
        "h2.top-card-layout__title"
    ),
    "company": (
        "div.job-details-jobs-unified-top-card__company-name a, "
        "div.job-details-jobs-unified-top-card__company-name, "
        "span.jobs-unified-top-card__company-name a, "
        "a.topcard__org-name-link, "
        "span.topcard__flavor"
    ),
    "location": (
        "div.job-details-jobs-unified-top-card__primary-description-container span.tvm__text, "
        "span.jobs-unified-top-card__bullet, "
        "span.topcard__flavor--bullet"
    ),
    "posted_time": (
        "div.job-details-jobs-unified-top-card__primary-description-container "
        "span.tvm__text--low-emphasis, "
        "span.jobs-unified-top-card__posted-date, "
        "span.posted-time-ago__text"
    ),
    "description": (
        "div.jobs-description__content, "
        "div.jobs-description-content, "
        "div#job-details, "
        "section.show-more-less-html"
    ),
}


def _extract_detail_from_panel(page, skip_reposted: bool) -> Optional[JobListing]:
    """
    Read the right-side detail panel after a job card has been clicked.
    Returns None if the job is reposted (and skip_reposted is True)
    or if extraction fails.
    """
    try:
        # Wait for the detail panel to load
        page.wait_for_selector(
            "div.jobs-description__content, div.jobs-description-content, "
            "div#job-details, section.show-more-less-html",
            timeout=8000,
        )
        _random_delay(0.5, 1.2)
    except Exception:
        logger.info("  → SKIP: detail panel did not load in time.")
        return None

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    # --- Check for "Reposted" in the detail header ---
    if skip_reposted:
        # The "Reposted X ago" text appears near the top of the detail panel
        top_card = soup.select_one(
            "div.job-details-jobs-unified-top-card__primary-description-container, "
            "div.jobs-unified-top-card__content, "
            "div.topcard"
        )
        if top_card:
            top_text = top_card.get_text(" ", strip=True).lower()
            if "reposted" in top_text:
                logger.info("  → SKIP: reposted job detected in detail panel.")
                return None

    # --- Extract fields from the right panel ---
    def _text(selector_group: str) -> str:
        for sel in selector_group.split(", "):
            el = soup.select_one(sel.strip())
            if el:
                return el.get_text(strip=True)
        return "N/A"

    title = _text(_DETAIL_SELECTORS["title"])
    company = _text(_DETAIL_SELECTORS["company"])
    location = _text(_DETAIL_SELECTORS["location"])
    posted_time = _text(_DETAIL_SELECTORS["posted_time"])

    # Description — get the full text from "About the job" section
    description = ""
    for sel in _DETAIL_SELECTORS["description"].split(", "):
        desc_el = soup.select_one(sel.strip())
        if desc_el:
            description = desc_el.get_text("\n", strip=True)
            break

    # Truncate very long descriptions for the email
    if len(description) > 1000:
        description = description[:1000].rsplit(" ", 1)[0] + "…"

    # Job URL — extract currentJobId from the page URL query string
    job_url = ""
    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(page.url)
        params = parse_qs(parsed.query)
        job_id = params.get("currentJobId", [None])[0]
        if job_id:
            job_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    except Exception:
        pass
    if not job_url:
        job_url = page.url

    if title == "N/A":
        logger.info("  → SKIP: could not extract title (selectors did not match).")
        return None

    return JobListing(
        title=title,
        company=company,
        location=location,
        description=description,
        job_url=job_url,
        posted_time=posted_time,
    )


# ---------------------------------------------------------------------------
# Scroll the left job list panel
# ---------------------------------------------------------------------------

def _scroll_job_list(page, max_scrolls: int = 25) -> int:
    """
    Scroll the left-side job list container **incrementally** until all
    cards are loaded.  LinkedIn uses a virtual/occluded list that only
    renders cards near the viewport, so we must scroll gradually (not
    jump to the bottom) to trigger lazy-loading of each batch.
    Returns the final card count.
    """
    # Find the scrollable container — try several known selectors
    container = None
    for sel in (
        "div.jobs-search-results-list",
        "div.scaffold-layout__list > div",
        "div.scaffold-layout__list",
    ):
        container = page.query_selector(sel)
        if container:
            logger.debug("Scroll container found: %s", sel)
            break

    prev_count = len(page.query_selector_all(_CARD_SELECTORS))
    stable_rounds = 0

    for i in range(max_scrolls):
        # Scroll incrementally — small steps to trigger lazy-load
        scroll_px = random.randint(400, 700)
        if container:
            container.evaluate(f"el => el.scrollBy(0, {scroll_px})")
        else:
            page.evaluate(f"window.scrollBy(0, {scroll_px})")

        _random_delay(0.6, 1.2)

        current_count = len(page.query_selector_all(_CARD_SELECTORS))
        logger.debug("Scroll %d (+%dpx): %d cards visible", i + 1, scroll_px, current_count)

        if current_count == prev_count:
            stable_rounds += 1
            if stable_rounds >= 3:
                break
        else:
            stable_rounds = 0
            prev_count = current_count

    # Scroll back to top so clicking starts from the first card
    if container:
        container.evaluate("el => el.scrollTop = 0")
    else:
        page.evaluate("window.scrollTo(0, 0)")
    _random_delay(0.3, 0.6)

    final_count = len(page.query_selector_all(_CARD_SELECTORS))
    logger.info("Scroll complete — %d cards loaded", final_count)
    return final_count


# ---------------------------------------------------------------------------
# Main scraper (with pagination + detail extraction)
# ---------------------------------------------------------------------------

def scrape_linkedin_jobs(
    job_url: str,
    headless: bool = True,
    page_timeout: int = 30,
    max_jobs: int = 50,
    max_pages: int = 3,
    skip_reposted: bool = True,
    skip_viewed: bool = True,
    profile_dir: str = DEFAULT_PROFILE_DIR,
) -> List[JobListing]:
    """
    Navigate to the LinkedIn job search URL, paginate through results,
    click each job card to read the right-side detail panel,
    filter out reposted/viewed listings, and extract full job data including JD.

    Uses a persistent browser profile to maintain the logged-in session.
    Returns a list of JobListing dataclass instances.
    """
    all_jobs: List[JobListing] = []
    seen_keys: set = set()  # deduplicate across pages
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as pw:
            context = _launch_persistent_context(
                pw,
                profile_dir=profile_dir,
                headless=headless,
                page_timeout=page_timeout,
            )
            page = context.pages[0] if context.pages else context.new_page()

            for page_num in range(1, max_pages + 1):
                page_url = _build_page_url(job_url, page_num)
                logger.info("Page %d/%d — %s", page_num, max_pages, page_url)

                _random_delay(0.5, 1.5)
                try:
                    page.goto(page_url, wait_until="domcontentloaded")
                except Exception as nav_err:
                    logger.warning("Page %d navigation failed: %s — stopping.", page_num, nav_err)
                    break
                _random_delay(2.0, 4.0)

                # Check if page is still alive
                if page.is_closed():
                    logger.warning("Page closed after navigation — stopping.")
                    break

                # Check if we got redirected to login
                if "/login" in page.url or "/authwall" in page.url:
                    msg = (
                        f"Redirected to login ({page.url}). "
                        "Session expired or cookies cleared. "
                        "Run `python main.py --login` to re-authenticate."
                    )
                    logger.error(msg)
                    context.close()
                    raise LoginRequiredError(msg)

                # Scroll the left job list to ensure all cards are loaded
                try:
                    card_count = _scroll_job_list(page)
                except Exception as scroll_err:
                    logger.warning("Scroll failed on page %d: %s — stopping.", page_num, scroll_err)
                    break
                _random_delay(1.0, 2.0)

                # Get all clickable job cards on this page
                cards = page.query_selector_all(_CARD_SELECTORS)
                if not cards:
                    # Save a debug screenshot to understand what LinkedIn showed
                    try:
                        screenshot_path = Path(profile_dir).parent / "debug_screenshot.png"
                        page.screenshot(path=str(screenshot_path), full_page=True)
                        logger.info("Debug screenshot saved to %s", screenshot_path)
                        logger.info("Current URL: %s", page.url)
                    except Exception:
                        pass
                    logger.info("No job cards found on page %d — stopping.", page_num)
                    break

                logger.info("Page %d: found %d cards in left panel", page_num, len(cards))

                skip_count = 0
                new_count = 0

                for i in range(len(cards)):
                    if len(all_jobs) >= max_jobs:
                        break

                    # Re-query cards each iteration to avoid stale element refs
                    # (clicking a card can cause the DOM to update)
                    live_cards = page.query_selector_all(_CARD_SELECTORS)
                    if i >= len(live_cards):
                        break
                    card = live_cards[i]

                    # Check card footer labels before clicking (saves time)
                    try:
                        card.scroll_into_view_if_needed()
                        _random_delay(0.2, 0.4)

                        # Get the parent li to read footer labels
                        parent_li = card.evaluate_handle(
                            "el => el.closest('li.scaffold-layout__list-item')"
                        )
                        if parent_li:
                            footer_text = parent_li.evaluate(
                                "el => { const f = el.querySelector('ul.job-card-list__footer-wrapper'); return f ? f.textContent.toLowerCase() : ''; }"
                            )
                            if skip_viewed and "viewed" in footer_text:
                                logger.info("  [%d] SKIP: already viewed.", i)
                                skip_count += 1
                                continue

                        card.click()
                    except Exception:
                        logger.debug("Could not click card %d — skipping.", i)
                        continue

                    _random_delay(1.0, 2.5)

                    # Extract job details from the right panel
                    job = _extract_detail_from_panel(page, skip_reposted=skip_reposted)

                    if job is None:
                        skip_count += 1
                        continue

                    # Deduplicate
                    key = job.job_url or job.title
                    if key in seen_keys:
                        continue

                    seen_keys.add(key)
                    all_jobs.append(job)
                    new_count += 1
                    logger.debug("  [%d] %s @ %s", len(all_jobs), job.title, job.company)

                logger.info(
                    "Page %d: %d new jobs extracted, %d skipped. Total: %d",
                    page_num, new_count, skip_count, len(all_jobs),
                )

                if len(all_jobs) >= max_jobs:
                    all_jobs = all_jobs[:max_jobs]
                    logger.info("Reached max_jobs limit (%d). Stopping.", max_jobs)
                    break

                # Check if there's a next page
                if not _has_next_page(page):
                    logger.info("No next page button found — last page reached.")
                    break

                # Human-like pause between pages
                _random_delay(3.0, 6.0)

            context.close()

        logger.info("Scraped %d total job listings across pages", len(all_jobs))

    except (LoginRequiredError, ScrapeError):
        raise  # propagate to caller for alert email
    except Exception as exc:
        logger.exception("Error scraping LinkedIn jobs")
        raise ScrapeError(str(exc)) from exc

    return all_jobs
