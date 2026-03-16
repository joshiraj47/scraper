# OpenClaw LinkedIn Job Scraper

Automated LinkedIn job scraper that runs on a schedule, respects configurable active hours (no runs at night), and emails you a formatted list of job listings.

## Features

- **Persistent browser session** — log in once, reuse your real LinkedIn session (no scraping detection)
- **Scheduled scraping** — runs every N minutes via APScheduler
- **Configurable active hours** — e.g., only 8 AM – 10 PM (skip nighttime)
- **Human-like behavior** — random delays, variable scrolling to avoid bot detection
- **HTML email reports** — clean, formatted job listings with title, company, location, and direct links
- **One-shot mode** — run once for testing (`--once`)
- **Systemd integration** — auto-start, auto-restart on VPS
- **Configurable via YAML** — no code changes needed

## Project Structure

```
├── main.py              # Entry point — scheduler + CLI (--login, --once)
├── scraper.py           # Playwright-based LinkedIn scraper (persistent session)
├── emailer.py           # HTML email formatter + SMTP sender
├── config.yaml          # All settings (hours, URL, email, profile)
├── requirements.txt     # Python dependencies
├── deploy/
│   └── openclaw-scraper.service   # systemd unit file
├── browser_profile/     # Persistent Chromium profile (git-ignored)
└── logs/                # Auto-created log directory
```

## Quick Start (Local)

```bash
# 1. Clone and enter the project
git clone <your-repo-url>
cd scraper

# 2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Playwright browsers
playwright install chromium
# On a VPS you also need system deps:
playwright install-deps chromium

# 5. Set your email password
export EMAIL_PASSWORD="your-app-password"

# 6. Edit config.yaml with your LinkedIn URL and email

# 7. Log into LinkedIn (one-time — opens a browser window)
#    Log in manually, the session is saved to browser_profile/
python main.py --login

# 8. Test with a one-shot run (uses your saved session)
python main.py --once

# 9. Start the scheduler (runs until stopped)
python main.py
```

## Email Setup (Gmail)

1. Go to https://myaccount.google.com/apppasswords
2. Generate an **App Password** for "Mail"
3. Use that password as `EMAIL_PASSWORD` (not your real Gmail password)
4. Set `sender` and `recipient` in `config.yaml`

## VPS Deployment

### 1. Provision a VPS

Any cheap VPS works:
- **Hetzner** — €3.79/mo (CX22)
- **DigitalOcean** — $6/mo (Basic Droplet)
- **Oracle Cloud** — Free tier (ARM, 4 CPU / 24 GB RAM)
- **Vultr** — $6/mo

Ubuntu 22.04+ recommended.

### 2. Server Setup

```bash
# SSH into your VPS
ssh root@your-server-ip

# Update system
apt update && apt upgrade -y

# Install Python 3.11+
apt install -y python3 python3-venv python3-pip

# Create a dedicated user
useradd -m -s /bin/bash scraper
mkdir -p /opt/openclaw-scraper
chown scraper:scraper /opt/openclaw-scraper
```

### 3. Deploy Code

```bash
# As the scraper user
su - scraper
cd /opt/openclaw-scraper

# Clone your repo (or scp the files)
git clone <your-repo-url> .

# Setup venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install Playwright + browser
playwright install chromium
playwright install-deps chromium

# Create .env file for secrets
echo 'EMAIL_PASSWORD=your-app-password-here' > .env
chmod 600 .env

# Create logs dir
mkdir -p logs
```

### 3b. LinkedIn Login (one-time)

You need a GUI session for this step (VNC, X11 forwarding, or do it locally and copy the profile):

```bash
# Option A: On a machine with a display (local or VNC on VPS)
python main.py --login
# → A browser opens. Log into LinkedIn. Close the browser when done.
# → Session saved to browser_profile/

# Option B: Log in locally and copy the profile to VPS
# On your local machine:
python main.py --login
scp -r browser_profile/ scraper@your-server-ip:/opt/openclaw-scraper/browser_profile/
```

### 4. Install systemd Service

```bash
# As root
sudo cp /opt/openclaw-scraper/deploy/openclaw-scraper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable openclaw-scraper
sudo systemctl start openclaw-scraper
```

### 5. Monitor

```bash
# Check status
sudo systemctl status openclaw-scraper

# View logs (live)
sudo journalctl -u openclaw-scraper -f

# Or check the log file
tail -f /opt/openclaw-scraper/logs/scraper.log
```

## Configuration Reference

Edit `config.yaml`:

| Setting | Description | Default |
|---------|-------------|---------|
| `schedule.active_hours_start` | Hour (24h) to start scraping | `8` |
| `schedule.active_hours_end` | Hour (24h) to stop scraping | `22` |
| `schedule.timezone` | IANA timezone | `Asia/Kolkata` |
| `schedule.interval_minutes` | Minutes between scrape cycles | `60` |
| `linkedin.job_url` | LinkedIn job search URL | — |
| `email.smtp_host` | SMTP server | `smtp.gmail.com` |
| `email.smtp_port` | SMTP port | `587` |
| `email.sender` | Sender email | — |
| `email.password_env` | Env var name for password | `EMAIL_PASSWORD` |
| `email.recipient` | Where to send reports | — |
| `openclaw.headless` | Run browser headless | `true` |
| `openclaw.max_jobs` | Max jobs per scrape | `50` |
| `openclaw.max_pages` | Max pages to paginate through (~25 jobs/page) | `3` |
| `openclaw.skip_reposted` | Filter out reposted job listings | `true` |
| `openclaw.profile_dir` | Persistent browser profile path | `browser_profile` |

## Troubleshooting

- **Redirected to login**: Run `python main.py --login` to save your LinkedIn session first.
- **No jobs found**: LinkedIn may have changed its page layout. Check `logs/scraper.log` for which selector matched.
- **Playwright errors on VPS**: Run `playwright install-deps chromium` as root to install system libraries.
- **Email not sending**: Check that `EMAIL_PASSWORD` is set and you're using a Gmail App Password (not your real password). Check `logs/scraper.log`.
- **Service won't start**: Check `journalctl -u openclaw-scraper -e` for errors.

## License

MIT
