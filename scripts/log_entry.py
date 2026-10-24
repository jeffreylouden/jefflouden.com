#!/usr/bin/env python3
"""Turns a "Log entry" issue (.github/ISSUE_TEMPLATE/log-entry.yml) into an
entry at the top of src/log.json, with its photo prepared into
public/images/log/. The log-entry workflow runs it, then build.py.

    ISSUE_BODY="$(cat body.md)" python3 scripts/log_entry.py

The issue body is data, never instructions: only its fields are read, and
only a GitHub attachment URL is ever fetched. On a problem it exits with a
message meant for Jeff, which the workflow posts on the issue.
"""

import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import build  # noqa: E402

LOG = ROOT / 'src' / 'log.json'
KINDS = ['photo', 'movie', 'album', 'song', 'book', 'site', 'thing', 'quote', 'note']
# GitHub's own attachment hosts. Nothing else is fetched.
PHOTO_URL = re.compile(r'https://(?:github\.com/user-attachments/assets/[0-9a-fA-F-]+|private-user-images\.githubusercontent\.com/[^\s)"\'<>]+|user-images\.githubusercontent\.com/[^\s)"\'<>]+)')
MAX_DOWNLOAD = 30_000_000


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


def slug(text):
    words = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return (words[:40].rstrip('-') or 'entry')


def one_line(value, label):
    if value and '\n' in value:
        raise EntryError(f'{label} should be one line')
    return value


def entry_from(fields, today):
    kind = (fields.get('Kind') or '').strip().lower()
    if kind not in KINDS:
        raise EntryError(f'Kind must be one of {", ".join(KINDS)}')
    title = one_line(fields.get('Title'), 'Title')
    url = one_line(fields.get('Link'), 'Link')
    quote = fields.get('Quote')
    who = one_line(fields.get('Who said it'), 'Who said it')
    text = fields.get('Your words')
    place = one_line(fields.get('Place'), 'Place')
    photo = fields.get('Photo')
    alt = one_line(fields.get('What the photo shows'), 'What the photo shows')
    if text:
        text = ' '.join(text.split())

    entry = {'kind': kind, 'date': today}
    if kind == 'quote':
        if not quote or not who:
            raise EntryError('A quote needs the Quote and Who said it')
        if title or url or photo:
            raise EntryError('A quote has no title, link or photo')
        entry['quote'] = ' '.join(quote.strip('“”"').split())
        entry['who'] = who
    elif kind == 'photo':
        if not photo:
            raise EntryError('A photo needs a picture in Photo')
        if url and not title:
            raise EntryError('A Link needs a Title to hang on')
        if title:
            entry['title'] = title
        if url:
            entry['url'] = url
    elif kind == 'note':
        if not text:
            raise EntryError('A note needs Your words')
        if title or url:
            raise EntryError('A note has no title or link')
    else:
        if not title or not text:
            raise EntryError(f'A {kind} needs a Title and Your words')
        entry['title'] = title
        if url:
            entry['url'] = url
    if quote and kind != 'quote':
        raise EntryError('Only a quote has a Quote')
    if text:
        entry['text'] = text
    if photo:
        if not alt:
            raise EntryError('A photo needs What the photo shows, for anyone who can’t see it')
        match = PHOTO_URL.search(photo)
        if not match:
            raise EntryError('Photo should be a picture dropped into the field')
        name = f'{today}-{slug(title or alt)}'
        n, base = 2, name
        while (build.LOG_IMAGES / f'{name}.jpg').exists():
            name, n = f'{base}-{n}', n + 1
        entry['image'] = {'src': f'images/log/{name}.jpg', 'alt': alt}
        entry['_photo_url'] = match.group(0)
    if place:
        entry['place'] = place
    return entry


def download(url, token=None, open_url=urllib.request.urlopen):
    """Fetches an attachment, first as anyone could. If GitHub refuses, it tries
    once more with the workflow's token, as an unredirected header so it reaches
    github.com and never the storage host GitHub redirects to."""
    def fetch(with_token):
        request = urllib.request.Request(url, headers={'User-Agent': 'jefflouden.com log-entry'})
        if with_token:
            request.add_unredirected_header('Authorization', f'Bearer {token}')
        with open_url(request, timeout=60) as res:
            return res.read(MAX_DOWNLOAD + 1)

    try:
        try:
            data = fetch(False)
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403, 404) or not token:
                raise
            data = fetch(True)
    except urllib.error.URLError as e:
        raise EntryError(f'GitHub wouldn’t hand over the photo ({getattr(e, "code", None) or e.reason}). Try attaching it again') from None
    if len(data) > MAX_DOWNLOAD:
        raise EntryError('That photo is over 30 MB')
    return data


def add(entry, fetch=None):
    from log_photo import prepare

    fetch = fetch or (lambda url: download(url, os.environ.get('GH_TOKEN')))

    entry = dict(entry)
    photo_url = entry.pop('_photo_url', None)
    log = json.loads(LOG.read_text(encoding='utf-8'))
    log['entries'].insert(0, entry)
    schema = json.loads((build.SRC / 'log.schema.json').read_text(encoding='utf-8'))
    try:
        build.validate(log, schema, schema, 'log')
    except build.BuildError as e:
        raise EntryError(f'That entry doesn’t fit the log: {e}') from None
    if photo_url:
        try:
            data = prepare(fetch(photo_url))
        except ValueError as e:
            raise EntryError(f'The photo didn’t work: {e}') from None
        build.LOG_IMAGES.mkdir(parents=True, exist_ok=True)
        (build.PUBLIC / entry['image']['src']).write_bytes(data)
    LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return entry


def commit_message(entry):
    if entry['kind'] == 'quote':
        return f'Add a quote from {entry["who"]} to the log'
    if entry['kind'] == 'note':
        return 'Add a note to the log'
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
