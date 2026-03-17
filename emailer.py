"""
Email module — formats job listings into an HTML email and sends via SMTP.
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

from jinja2 import Template

from scraper import JobListing

logger = logging.getLogger(__name__)

EMAIL_TEMPLATE = Template("""\
<!DOCTYPE html>
<html>
<head>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
  .container { max-width: 700px; margin: auto; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  .header { background: #0a66c2; color: #fff; padding: 20px 24px; }
  .header h1 { margin: 0; font-size: 20px; }
  .header p { margin: 4px 0 0; opacity: 0.85; font-size: 13px; }
  .job { padding: 16px 24px; border-bottom: 1px solid #eee; }
  .job:last-child { border-bottom: none; }
  .job h2 { margin: 0 0 4px; font-size: 16px; }
  .job h2 a { color: #0a66c2; text-decoration: none; }
  .job .meta { color: #666; font-size: 13px; margin-bottom: 6px; }
  .job .meta .time { color: #0a66c2; font-weight: 600; }
  .job details { margin-top: 8px; }
  .job details summary { cursor: pointer; font-size: 13px; color: #0a66c2; font-weight: 600; padding: 4px 0; }
  .job details summary:hover { text-decoration: underline; }
  .job .desc-content { font-size: 13px; color: #444; line-height: 1.6; margin-top: 8px; background: #f9f9f9; padding: 12px 14px; border-radius: 6px; border-left: 3px solid #0a66c2; }
  .job .desc-content ul { padding-left: 20px; margin: 6px 0; }
  .job .desc-content li { margin-bottom: 4px; }
  .job .desc-content p { margin: 6px 0; }
  .job .desc-content strong, .job .desc-content b { color: #333; }
  .footer { padding: 16px 24px; text-align: center; font-size: 12px; color: #999; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{{ subject }}</h1>
    <p>{{ job_count }} listing{{ 's' if job_count != 1 }} found &middot; {{ timestamp }}</p>
  </div>
  {% for job in jobs %}
  <div class="job">
    <h2>{% if job.job_url %}<a href="{{ job.job_url }}">{{ job.title }}</a>{% else %}{{ job.title }}{% endif %}</h2>
    <div class="meta">{{ job.company }} &middot; {{ job.location }}{% if job.posted_time %} &middot; <span class="time">{{ job.posted_time }}</span>{% endif %}</div>
  </div>
  {% endfor %}
  <div class="footer">Sent by OpenClaw Job Scraper</div>
</div>
</body>
</html>
""")


def send_job_email(
    jobs: List[JobListing],
    smtp_host: str,
    smtp_port: int,
    use_tls: bool,
    sender: str,
    password_env: str,
    recipient: str,
    subject_prefix: str,
    timestamp: str,
) -> bool:
    """
    Render all job listings into a single HTML email and send it.
    Returns True on success, False on failure.
    """
    if not jobs:
        logger.info("No jobs to send — skipping email.")
        return False

    password = os.environ.get(password_env)
    if not password:
        logger.error("Email password env var '%s' is not set.", password_env)
        return False

    subject = f"{subject_prefix} {len(jobs)} new jobs — {timestamp}"

    html_body = EMAIL_TEMPLATE.render(
        subject=subject,
        job_count=len(jobs),
        timestamp=timestamp,
        jobs=jobs,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    try:
        if use_tls:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.ehlo()
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)

        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())
        server.quit()
        logger.info("Email sent to %s (%d jobs)", recipient, len(jobs))
        return True

    except Exception:
        logger.exception("Failed to send email")
        return False


# ---------------------------------------------------------------------------
# Alert / error notification email
# ---------------------------------------------------------------------------

ALERT_TEMPLATE = Template("""\
<!DOCTYPE html>
<html>
<head>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
  .container { max-width: 600px; margin: auto; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  .header { background: #cc3333; color: #fff; padding: 20px 24px; }
  .header h1 { margin: 0; font-size: 20px; }
  .header p { margin: 4px 0 0; opacity: 0.85; font-size: 13px; }
  .body { padding: 20px 24px; }
  .body p { font-size: 14px; color: #333; line-height: 1.6; }
  .body pre { background: #f9f9f9; padding: 12px; border-radius: 6px; font-size: 13px; overflow-x: auto; }
  .footer { padding: 16px 24px; text-align: center; font-size: 12px; color: #999; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{{ subject }}</h1>
    <p>{{ timestamp }}</p>
  </div>
  <div class="body">
    <p>{{ message }}</p>
    {% if details %}<pre>{{ details }}</pre>{% endif %}
    {% if action %}<p><strong>Action required:</strong> {{ action }}</p>{% endif %}
  </div>
  <div class="footer">Sent by OpenClaw Job Scraper</div>
</div>
</body>
</html>
""")


def send_alert_email(
    error_type: str,
    message: str,
    details: str,
    action: str,
    smtp_host: str,
    smtp_port: int,
    use_tls: bool,
    sender: str,
    password_env: str,
    recipient: str,
    timestamp: str,
) -> bool:
    """
    Send an alert/error notification email (e.g. credential expiry, scrape failure).
    Returns True on success, False on failure.
    """
    password = os.environ.get(password_env)
    if not password:
        logger.error("Cannot send alert — email password env var '%s' is not set.", password_env)
        return False

    subject = f"[OpenClaw ALERT] {error_type}"

    html_body = ALERT_TEMPLATE.render(
        subject=subject,
        timestamp=timestamp,
        message=message,
        details=details,
        action=action,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    try:
        if use_tls:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.ehlo()
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)

        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())
        server.quit()
        logger.info("Alert email sent to %s: %s", recipient, error_type)
        return True

    except Exception:
        logger.exception("Failed to send alert email")
        return False
