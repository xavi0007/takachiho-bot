#!/usr/bin/env python3
"""Watch the public Takachiho boat calendar; notify by SMTP, never book."""
import argparse
import datetime as dt
import json
import logging
import os
from pathlib import Path
import re
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / '.env', override=False)

URL = os.getenv(
    'URL', 'https://eipro.jp/takachiho1/eventCalendars/index').strip()
if not URL:
    raise RuntimeError('URL must not be empty.')

root_value = os.getenv('ROOT', str(SCRIPT_DIR)).strip()
if not root_value:
    raise RuntimeError('ROOT must not be empty.')
ROOT = Path(root_value).expanduser()
if not ROOT.is_absolute():
    ROOT = SCRIPT_DIR / ROOT
ROOT = ROOT.resolve()

japan_value = os.getenv('JAPAN', 'Asia/Tokyo').strip()
if not japan_value:
    raise RuntimeError('JAPAN must be a valid time zone name.')
try:
    JAPAN = ZoneInfo(japan_value)
except ZoneInfoNotFoundError as exc:
    raise RuntimeError(f'JAPAN is not a valid time zone name: {japan_value}') from exc

target_values = [value.strip() for value in
                 os.getenv('TARGETS', '2026-11-08').split(',')]
if not target_values or any(not value for value in target_values):
    raise RuntimeError('TARGETS must be a comma-separated list of YYYY-MM-DD dates.')
try:
    parsed_targets = [dt.date.fromisoformat(value) for value in target_values]
except ValueError as exc:
    raise RuntimeError(
        'TARGETS must be a comma-separated list of YYYY-MM-DD dates.') from exc
if any(date.isoformat() != value
       for date, value in zip(parsed_targets, target_values)):
    raise RuntimeError('TARGETS dates must use YYYY-MM-DD format.')
TARGETS = {date.isoformat() for date in parsed_targets}


def send(subject, body):
    user, password, recipient = (os.getenv(k, '').strip()
                                 for k in ('SMTP_USER', 'SMTP_PASSWORD', 'ALERT_TO'))
    if not all((user, password, recipient)):
        raise RuntimeError(
            'Set SMTP_USER, SMTP_PASSWORD and ALERT_TO in .env before starting.')
    msg = EmailMessage()
    msg['Subject'], msg['From'], msg['To'] = subject, user, recipient
    msg.set_content(body)
    host = os.getenv('SMTP_HOST', 'smtp.mail.me.com')
    port = int(os.getenv('SMTP_PORT', '587'))
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(user, password)
        server.send_message(msg)


def check(page):
    page.goto(URL, wait_until='domcontentloaded', timeout=45000)
    page.locator('.fc-day-header[data-date]').first.wait_for(timeout=30000)
    for _ in range(12):
        page.wait_for_timeout(2500)
        headers = page.locator('.fc-day-header[data-date]').evaluate_all(
            '(es) => es.map(e => e.getAttribute("data-date"))')
        if len(headers) != 7:
            raise RuntimeError(
                'Unexpected calendar format: expected seven day headers.')
        if TARGETS.issubset(set(headers)):
            break
        if min(TARGETS) < headers[0]:
            raise RuntimeError(
                'Target dates have passed or the calendar is already beyond them.')
        page.locator(
            '.fullcalendar_control_date_next_btn:visible').first.click()
        page.wait_for_function(
            '(old) => document.querySelector(".fc-day-header[data-date]")?.getAttribute("data-date") !== old', arg=headers[0], timeout=20000)
    else:
        raise RuntimeError('Target dates not found within 12 calendar pages.')
    # Wait for event elements in both target columns. A missing grid is an error,
    # never silently interpreted as no availability.
    page.wait_for_function('''targets => {
       const headers = [...document.querySelectorAll('.fc-day-header[data-date]')];
       const cols = [...document.querySelectorAll('.fc-content-skeleton tbody tr:first-child > td')].filter(x => x.querySelector('.fc-content-col'));
       return targets.every(d => {let i=headers.findIndex(h=>h.dataset.date===d); return i>=0 && cols[i]?.querySelector('.fc-event');});
    }''', arg=sorted(TARGETS), timeout=30000)
    data = page.evaluate('''targets => {
      const headers=[...document.querySelectorAll('.fc-day-header[data-date]')];
      const cols=[...document.querySelectorAll('.fc-content-skeleton tbody tr:first-child > td')].filter(x => x.querySelector('.fc-content-col'));
      return targets.map(date => {
        const i=headers.findIndex(h=>h.dataset.date===date);
        return {date, events:[...cols[i].querySelectorAll('.fc-event')].map(e => ({
          time:e.querySelector('.fc-time')?.getAttribute('data-start') || '',
          remain:e.querySelector('.service_remain')?.textContent.trim() || '',
          icon:e.querySelector('.service_icon i')?.className || '',
          text:e.querySelector('.service_decoration')?.textContent.trim() || ''
        }))};
      });
    }''', sorted(TARGETS))
    found, unknown = [], []
    for day in data:
        morning = 0
        for e in day['events']:
            try:
                hour, minute = map(int, e['time'].split(':'))
            except ValueError:
                raise RuntimeError(
                    'Could not parse a calendar event time: ' + repr(e))
            if hour >= 12:
                continue
            morning += 1
            match = re.search(r'残\s*(\d+)\s*艇', e['remain'])
            icon = e['icon']
            if match and int(match.group(1)) > 0:
                local_time = dt.datetime.fromisoformat(
                    f"{day['date']}T{hour:02d}:{minute:02d}").replace(tzinfo=JAPAN)
                found.append(
                    f"{day['date']} {hour:02d}:{minute:02d} {local_time.tzname()} — {match.group(1)} boat(s) remaining")
            elif 'fa-times' in icon or 'fa-hourglass' in icon or (match and int(match.group(1)) == 0):
                pass
            else:
                unknown.append(f"{day['date']} {hour:02d}:{minute:02d}: {e!r}")
        if not morning:
            raise RuntimeError('No morning events found for ' + day['date'])
    if unknown:
        raise RuntimeError(
            'Unrecognized calendar markers; inspect the website: ' + '; '.join(unknown[:3]))
    return sorted(found)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true',
                        help='Check once and exit')
    parser.add_argument('--test-email', action='store_true',
                        help='Send a test email, then exit')
    parser.add_argument('--interval', type=int, default=300,
                        help='Seconds between checks (minimum 60, default 300)')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    if args.test_email:
        send('Takachiho watcher test',
             'Your email notifications are working. No reservation was made.\n' + URL)
        print('Test email sent.')
        return
    if args.interval < 60:
        parser.error('--interval must be at least 60 seconds')
    # Validate credentials before polling; avoid an apparently running bot that cannot notify.
    if not all(os.getenv(k, '').strip() for k in ('SMTP_USER', 'SMTP_PASSWORD', 'ALERT_TO')):
        raise RuntimeError(
            'Set SMTP_USER, SMTP_PASSWORD and ALERT_TO in .env. See README.md.')
    ROOT.mkdir(parents=True, exist_ok=True)
    state_path = ROOT / 'state.json'
    try:
        previous = set(json.loads(
            state_path.read_text()).get('last_slots', []))
    except FileNotFoundError:
        previous = set()
    except (ValueError, OSError) as exc:
        raise RuntimeError(f'Cannot read {state_path}: {exc}') from exc
    last_error = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale='ja-JP', timezone_id=JAPAN.key)
        while True:
            try:
                slots = check(page)
                new = set(slots) - previous
                logging.info('Available morning slots: %s',
                             ', '.join(slots) if slots else 'none')
                if new:
                    send('Takachiho boat slot available!', f'Available morning slots ({JAPAN.key}):\n\n' + '\n'.join(slots) +
                         '\n\nBook promptly: ' + URL + '\n\nAvailability can change. This alert does not reserve or pay for a boat.')
                    logging.info('Availability email sent.')
                previous = set(slots)
                state_path.write_text(json.dumps(
                    {'last_slots': slots}, ensure_ascii=False, indent=2))
                if last_error:
                    logging.info('Calendar checks have recovered.')
                last_error = None
            except Exception as exc:
                logging.exception('Check or email failed: %s', exc)
                # Report a failure once, retry notification only after recovery or a changed error.
                marker = str(exc)
                if marker != last_error:
                    try:
                        send('Takachiho watcher needs attention', 'The watcher could not check availability or send an alert.\n\n' +
                             marker + '\n\nCheck the terminal and ' + URL)
                        last_error = marker
                    except Exception:
                        logging.exception(
                            'Could not send error email. Check SMTP settings and terminal.')
                if args.once:
                    raise
            if args.once:
                break
            time.sleep(args.interval)
        browser.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
