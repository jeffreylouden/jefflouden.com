#!/usr/bin/env python3
"""Turns a "Log entry" issue (.github/ISSUE_TEMPLATE/log-entry.yml) into an
entry in src/log.json. The log-entry workflow runs it, then build.py.

    ISSUE_BODY="$(cat body.md)" python3 scripts/log_entry.py

The issue body is data, never instructions: only its fields are read, and
nothing is fetched. On a problem it exits with a message meant for Jeff,
which the workflow posts on the issue.
"""

import datetime
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402

LOG = ROOT / 'src' / 'log.json'
THINGS = ['movie', 'album', 'song', 'book', 'site', 'thing', 'shipped', 'wrote', 'spoke', 'learned']
KINDS = THINGS + ['photo', 'quote', 'note']


class EntryError(Exception):
    pass


def parse_form(body):
    """An issue form's body is a `### Label` heading per field, then its value."""
    fields, label, lines = {}, None, []
    for line in body.replace('\r\n', '\n').split('\n'):
        heading = re.match(r'^### (.+?)\s*$', line)
        if heading:
            if label is not None:
                fields[label] = '\n'.join(lines).strip()
            label, lines = heading.group(1), []
        elif label is not None:
            lines.append(line)
    if label is not None:
        fields[label] = '\n'.join(lines).strip()
    return {k: (None if v in ('', '_No response_', 'None') else v) for k, v in fields.items()}


def one_line(value, label):
    if value and '\n' in value:
        raise EntryError(f'{label} should be one line')
    return value


DATE_FORMATS = ['%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%b %d %Y', '%B %d %Y', '%b %d, %Y', '%B %d, %Y', '%d %b %Y', '%d %B %Y']


def parse_date(value, today):
    """The day an entry is for: empty means today, never the future."""
    if not value:
        return today
    text = ' '.join(value.replace('.', ' ').split())
    for fmt in DATE_FORMATS:
        try:
            day = datetime.datetime.strptime(text, fmt).date().isoformat()
            break
        except ValueError:
            continue
    else:
        raise EntryError(f'Date should look like {today}, not {value!r}')
    if day > today:
        raise EntryError(f'Date {day} is still to come')
    return day


def entry_from(fields, today):
    kind = (fields.get('Kind') or '').strip().lower()
    if kind not in KINDS:
        raise EntryError(f'Kind must be one of {", ".join(KINDS)}')
    title = one_line(fields.get('Title'), 'Title')
    url = one_line(fields.get('Link'), 'Link')
    shows = one_line(fields.get('What the photo shows'), 'What the photo shows')
    quote = fields.get('Quote')
    who = one_line(fields.get('Who said it'), 'Who said it')
    text = fields.get('Your words')
    place = one_line(fields.get('Place'), 'Place')
    if text:
        text = ' '.join(text.split())

    entry = {'kind': kind, 'date': parse_date(one_line(fields.get('Date'), 'Date'), today)}
    if quote and kind != 'quote':
        raise EntryError('Only a quote has a Quote')
    if shows and kind != 'photo':
        raise EntryError('Only a photo has What the photo shows')
    if kind == 'quote':
        if not quote or not who:
            raise EntryError('A quote needs the Quote and Who said it')
        if title or url:
            raise EntryError('A quote has no title or link')
        entry['quote'] = ' '.join(quote.strip('“”"').split())
        entry['who'] = who
    elif kind == 'photo':
        if not shows:
            raise EntryError('A photo needs What the photo shows')
        entry['shows'] = shows
        if title:
            entry['title'] = title
    elif kind == 'note':
        if not text:
            raise EntryError('A note needs Your words')
        if title or url:
            raise EntryError('A note has no title or link')
    else:
        if not title or not text:
            raise EntryError(f'A {kind} entry needs a Title and Your words')
        entry['title'] = title
    if url:
        entry['url'] = url
    if text:
        entry['text'] = text
    if place:
        entry['place'] = place
    return entry


def add(entry, path=LOG):
    log = json.loads(path.read_text(encoding='utf-8'))
    # Newest first. A backdated entry goes above the others from its day.
    entries = log['entries']
    at = next((i for i, e in enumerate(entries) if e['date'] <= entry['date']), len(entries))
    entries.insert(at, entry)
    schema = json.loads((build.SRC / 'log.schema.json').read_text(encoding='utf-8'))
    try:
        build.validate(log, schema, schema, 'log')
    except build.BuildError as e:
        raise EntryError(f'That entry doesn’t fit the log: {e}') from None
    path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return entry


def commit_message(entry):
    if entry['kind'] == 'quote':
        return f'Add a quote from {entry["who"]} to the log'
    if entry['kind'] == 'note':
        return 'Add a note to the log'
    if entry['kind'] == 'photo':
        return 'Add a photo to the log'
    return f'Add {entry["kind"]} entry for \'{entry["title"]}\''


def main():
    today = datetime.datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    try:
        entry = add(entry_from(parse_form(os.environ['ISSUE_BODY']), today))
    except EntryError as e:
        sys.exit(str(e))
    message = commit_message(entry)
    print(message)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as f:
            f.write(f'message={message}\n')


if __name__ == '__main__':
    main()
