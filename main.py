#!/usr/bin/env python3
"""
OpenClaw LinkedIn Job Scraper — Main entry point.

Runs on a schedule (APScheduler), respects configurable active hours,
scrapes LinkedIn jobs, and emails results.
"""

import argparse
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

import pytz
import yaml
from apscheduler.schedulers.blocking import BlockingScheduler

from scraper import scrape_linkedin_jobs, interactive_login, LoginRequiredError, ScrapeError
from emailer import send_job_email, send_alert_email

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "scraper.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("openclaw")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)

# ---------------------------------------------------------------------------
# Core job
# ---------------------------------------------------------------------------

def _send_alert(config: dict, error_type: str, message: str, details: str = "", action: str = "") -> None:
    """Send an alert email using the configured email settings."""
    email_cfg = config.get("email", {})
    sched_cfg = config.get("schedule", {})
    tz = pytz.timezone(sched_cfg.get("timezone", "UTC"))
    timestamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    try:
        send_alert_email(
            error_type=error_type,
            message=message,
            details=details,
            action=action,
            smtp_host=email_cfg["smtp_host"],
            smtp_port=email_cfg["smtp_port"],
            use_tls=email_cfg.get("use_tls", True),
            sender=email_cfg["sender"],
            password_env=email_cfg["password_env"],
            recipient=email_cfg["recipient"],
            timestamp=timestamp,
        )
    except Exception:
        logger.exception("Failed to send alert email")


def run_scrape_job(config: dict) -> None:
    """Single scrape-and-email cycle."""
    sched_cfg = config["schedule"]
    tz = pytz.timezone(sched_cfg["timezone"])
    now = datetime.now(tz)

    start_hour = sched_cfg["active_hours_start"]
    end_hour = sched_cfg["active_hours_end"]

    if not (start_hour <= now.hour < end_hour):
        logger.info(
            "Outside active hours (%02d:00–%02d:00). Current: %s. Skipping.",
            start_hour, end_hour, now.strftime("%H:%M"),
        )
        return

    logger.info("=== Scrape cycle started at %s ===", now.strftime("%Y-%m-%d %H:%M:%S"))

    linkedin_cfg = config["linkedin"]
    oc_cfg = config.get("openclaw", {})

    try:
        jobs = scrape_linkedin_jobs(
            job_url=linkedin_cfg["job_url"],
            headless=oc_cfg.get("headless", True),
            page_timeout=oc_cfg.get("page_timeout", 30),
            max_jobs=oc_cfg.get("max_jobs", 50),
            max_pages=oc_cfg.get("max_pages", 3),
            skip_reposted=oc_cfg.get("skip_reposted", True),
            skip_viewed=oc_cfg.get("skip_viewed", True),
            profile_dir=oc_cfg.get("profile_dir", "browser_profile"),
        )
    except LoginRequiredError as exc:
        logger.error("Login required: %s", exc)
        _send_alert(
            config,
            error_type="LinkedIn Session Expired",
            message="The scraper was redirected to LinkedIn's login page. Your saved session has expired or been invalidated.",
            details=str(exc),
            action="SSH into your VPS and run: python main.py --login",
        )
        return
    except ScrapeError as exc:
        logger.error("Scrape failed: %s", exc)
        _send_alert(
            config,
            error_type="Scraping Error",
            message="The scraper encountered an unexpected error while processing LinkedIn job listings.",
            details=str(exc),
            action="Check logs/scraper.log for full traceback.",
        )
        return

    if not jobs:
        logger.info("No jobs found this cycle.")
        return

    email_cfg = config["email"]
    timestamp = now.strftime("%Y-%m-%d %H:%M")

    send_job_email(
        jobs=jobs,
        smtp_host=email_cfg["smtp_host"],
        smtp_port=email_cfg["smtp_port"],
        use_tls=email_cfg.get("use_tls", True),
        sender=email_cfg["sender"],
        password_env=email_cfg["password_env"],
        recipient=email_cfg["recipient"],
        subject_prefix=email_cfg.get("subject_prefix", "[OpenClaw Jobs]"),
        timestamp=timestamp,
    )

    logger.info("=== Scrape cycle complete — %d jobs emailed ===", len(jobs))

# ---------------------------------------------------------------------------
# One-shot mode (for testing / cron)
# ---------------------------------------------------------------------------

def run_once(config: dict) -> None:
    """Run a single scrape cycle immediately (ignores active hours)."""
    sched_cfg = config["schedule"]
    tz = pytz.timezone(sched_cfg["timezone"])
    now = datetime.now(tz)
    logger.info("Running one-shot scrape at %s", now.strftime("%Y-%m-%d %H:%M:%S"))

    linkedin_cfg = config["linkedin"]
    oc_cfg = config.get("openclaw", {})

    try:
        jobs = scrape_linkedin_jobs(
            job_url=linkedin_cfg["job_url"],
            headless=oc_cfg.get("headless", True),
            page_timeout=oc_cfg.get("page_timeout", 30),
            max_jobs=oc_cfg.get("max_jobs", 50),
            max_pages=oc_cfg.get("max_pages", 3),
            skip_reposted=oc_cfg.get("skip_reposted", True),
            skip_viewed=oc_cfg.get("skip_viewed", True),
            profile_dir=oc_cfg.get("profile_dir", "browser_profile"),
        )
    except LoginRequiredError as exc:
        logger.error("Login required: %s", exc)
        _send_alert(
            config,
            error_type="LinkedIn Session Expired",
            message="The scraper was redirected to LinkedIn's login page. Your saved session has expired or been invalidated.",
            details=str(exc),
            action="SSH into your VPS and run: python main.py --login",
        )
        return
    except ScrapeError as exc:
        logger.error("Scrape failed: %s", exc)
        _send_alert(
            config,
            error_type="Scraping Error",
            message="The scraper encountered an unexpected error while processing LinkedIn job listings.",
            details=str(exc),
            action="Check logs/scraper.log for full traceback.",
        )
        return

    if jobs:
        email_cfg = config["email"]
        send_job_email(
            jobs=jobs,
            smtp_host=email_cfg["smtp_host"],
            smtp_port=email_cfg["smtp_port"],
            use_tls=email_cfg.get("use_tls", True),
            sender=email_cfg["sender"],
            password_env=email_cfg["password_env"],
            recipient=email_cfg["recipient"],
            subject_prefix=email_cfg.get("subject_prefix", "[OpenClaw Jobs]"),
            timestamp=now.strftime("%Y-%m-%d %H:%M"),
        )
        logger.info("One-shot complete — %d jobs found and emailed.", len(jobs))
    else:
        logger.info("One-shot complete — no jobs found.")

# ---------------------------------------------------------------------------
# Scheduler mode (daemon)
# ---------------------------------------------------------------------------

def run_scheduler(config: dict) -> None:
    """Start the APScheduler loop."""
    sched_cfg = config["schedule"]
    interval = sched_cfg.get("interval_minutes", 60)

    scheduler = BlockingScheduler(timezone=pytz.timezone(sched_cfg["timezone"]))
    scheduler.add_job(
        run_scrape_job,
        trigger="interval",
        minutes=interval,
        args=[config],
        id="linkedin_scrape",
        name="LinkedIn Job Scrape",
        next_run_time=datetime.now(pytz.timezone(sched_cfg["timezone"])),  # run immediately on start
    )

    def graceful_shutdown(signum, frame):
        logger.info("Received signal %s — shutting down.", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)

    logger.info(
        "Scheduler started — running every %d min, active %02d:00–%02d:00 %s",
        interval,
        sched_cfg["active_hours_start"],
        sched_cfg["active_hours_end"],
        sched_cfg["timezone"],
    )
    scheduler.start()

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="OpenClaw LinkedIn Job Scraper")
    parser.add_argument(
        "--config", default="config.yaml", help="Path to config file (default: config.yaml)"
    )
    parser.add_argument(
        "--once", action="store_true", help="Run a single scrape immediately and exit"
    )
    parser.add_argument(
        "--login", action="store_true",
        help="Open a browser to log into LinkedIn (one-time setup). "
             "Your session is saved for future headless runs.",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if args.login:
        oc_cfg = config.get("openclaw", {})
        interactive_login(profile_dir=oc_cfg.get("profile_dir", "browser_profile"))
    elif args.once:
        run_once(config)
    else:
        run_scheduler(config)


if __name__ == "__main__":
    main()
