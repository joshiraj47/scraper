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
  .logo { width: 48px; height: 48px; border-radius: 6px; object-fit: contain; background: #f0f0f0; }
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
    <table cellpadding="0" cellspacing="0" border="0"><tr>
      {% if job.company_logo %}<td style="vertical-align:top;padding-right:12px;"><img class="logo" src="{{ job.company_logo }}" alt=""></td>{% endif %}
      <td style="vertical-align:top;">
        <h2>{% if job.job_url %}<a href="{{ job.job_url }}">{{ job.title }}</a>{% else %}{{ job.title }}{% endif %}</h2>
        <div class="meta">{{ job.company }} &middot; {{ job.location }}{% if job.posted_time %} &middot; <span class="time">{{ job.posted_time }}</span>{% endif %}</div>
      </td>
    </tr></table>
  </div>
  {% endfor %}
  <div class="footer">Sent by OpenClaw Job Scraper</div>
</div>
</body>
</html>
""")


JOBS_PER_EMAIL = 50  # Gmail clips emails >102KB; ~50 jobs keeps it safe


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
    Render job listings into HTML emails (max 50 jobs each to avoid Gmail clipping).
    Returns True if all emails sent successfully, False on failure.
    """
    if not jobs:
        logger.info("No jobs to send — skipping email.")
        return False

    password = os.environ.get(password_env)
    if not password:
        logger.error("Email password env var '%s' is not set.", password_env)
        return False

    total_jobs = len(jobs)
    total_parts = (total_jobs + JOBS_PER_EMAIL - 1) // JOBS_PER_EMAIL

    try:
        if use_tls:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.ehlo()
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        server.login(sender, password)
    except Exception:
        logger.exception("Failed to connect to SMTP server")
        return False

    try:
        for part_idx in range(total_parts):
            start = part_idx * JOBS_PER_EMAIL
            end = min(start + JOBS_PER_EMAIL, total_jobs)
            batch = jobs[start:end]

            if total_parts == 1:
                subject = f"{subject_prefix} {total_jobs} new jobs — {timestamp}"
            else:
                subject = f"{subject_prefix} {total_jobs} jobs — Part {part_idx + 1}/{total_parts} ({start + 1}–{end}) — {timestamp}"

            html_body = EMAIL_TEMPLATE.render(
                subject=subject,
                job_count=total_jobs,
                timestamp=timestamp,
                jobs=batch,
            )

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = recipient
            msg.attach(MIMEText(html_body, "html"))

            server.sendmail(sender, [recipient], msg.as_string())
            logger.info("Email part %d/%d sent to %s (%d jobs)",
                        part_idx + 1, total_parts, recipient, len(batch))

        server.quit()
        return True

    except Exception:
        logger.exception("Failed to send email")
        try:
            server.quit()
        except Exception:
            pass
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
