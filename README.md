# Takachiho calendar watcher

`bot.py` checks the public Takachiho boat calendar and emails when it detects
newly available morning slots on the dates configured in the script. It only
checks availability: it does not reserve, book, or pay for a boat.

## Requirements

- Python 3.9 or newer
- An SMTP account that can send email. For iCloud Mail, use an app-specific
  password, not your Apple Account password.

## Setup

Open Terminal in this repository folder, then create an environment and install
the dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Create a local configuration file and edit it:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Set these values in `.env`:

- `URL`: calendar page to check
- `ROOT`: directory for `state.json`; relative paths are resolved from the
  directory containing `bot.py`
- `JAPAN`: IANA time zone name used by the browser and alert messages
- `TARGETS`: comma-separated calendar dates in `YYYY-MM-DD` format
- `SMTP_USER`: the full email address used to log in and send alerts
- `SMTP_PASSWORD`: its SMTP or app-specific password
- `ALERT_TO`: the destination email address for alerts
- `SMTP_HOST` and `SMTP_PORT`: SMTP server settings; the defaults are for
  iCloud Mail

Keep `.env` private. It is intentionally excluded from Git. The script loads it
from the same folder as `bot.py`, regardless of the configured `ROOT`.
Environment variables already set in the shell take precedence.

## Choose the dates to check

Set `TARGETS` in `.env`, using comma-separated dates, for example:

```dotenv
TARGETS=2026-11-20,2026-11-21
```

Replace the example with your desired upcoming date or dates before running
the watcher. All target dates need to appear together in the calendar's
displayed seven-day week.

## Run the watcher

Activate the virtual environment whenever you open a new Terminal:

```bash
source .venv/bin/activate
```

First send a test message to verify SMTP settings:

```bash
python bot.py --test-email
```

Then check the calendar once:

```bash
python bot.py --once
```

Start continuous monitoring with the default five-minute interval:

```bash
python bot.py
```

To use a different polling interval, specify seconds (the minimum is 60):

```bash
python bot.py --interval 120
```

Keep the computer awake and connected to the internet while monitoring. Stop
the watcher with `Ctrl+C`.

The watcher logs its checks in the Terminal and saves the most recently
available slots in `state.json`. It emails when a slot newly appears, and
avoids repeat alerts while that slot remains available. If a slot disappears
and later reappears, it can send another alert. Calendar or email errors are
logged; the watcher also attempts to email when it encounters a new error.
Deleting `state.json` resets the alert history.

Availability can change before you act. Follow the booking site's rules and
complete any reservation yourself.
