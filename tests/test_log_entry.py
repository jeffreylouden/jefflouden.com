"""Tests for scripts/log_entry.py, which turns a "Log entry" issue into an
entry. They write to a temporary copy of src/log.json, never the real one."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import log_entry  # noqa: E402

TODAY = '2026-11-01'


def form(**fields):
    labels = {'kind': 'Kind', 'title': 'Title', 'link': 'Link', 'shows': 'What the photo shows', 'quote': 'Quote',
              'who': 'Who said it', 'text': 'Your words', 'place': 'Place', 'date': 'Date'}
    return '\n\n'.join(f'### {label}\n\n{fields.get(key) or "_No response_"}' for key, label in labels.items())


def entry(**fields):
    return log_entry.entry_from(log_entry.parse_form(form(**fields)), TODAY)


class Entries(unittest.TestCase):
    def test_a_work_entry(self):
        self.assertEqual(entry(kind='shipped', title='The thing', link='https://example.com', text='It  works.\n'),
                         {'kind': 'shipped', 'date': TODAY, 'title': 'The thing', 'url': 'https://example.com', 'text': 'It works.'})

    def test_a_quote_loses_its_marks(self):
        self.assertEqual(entry(kind='quote', quote='“Ship it.”', who='Someone')['quote'], 'Ship it.')

    def test_a_photo_in_words(self):
        self.assertEqual(entry(kind='photo', shows='Two candles', place='San Francisco'),
                         {'kind': 'photo', 'date': TODAY, 'shows': 'Two candles', 'place': 'San Francisco'})

    def test_dates(self):
        self.assertEqual(entry(kind='note', text='x', date='Oct 24 2026')['date'], '2026-10-24')
        self.assertEqual(entry(kind='note', text='x', date='10/24/2026')['date'], '2026-10-24')

    def test_rejects(self):
        cases = {
            'an unknown kind': dict(kind='tweet', text='x'),
            'a thing with no words': dict(kind='book', title='T'),
            'a photo with nothing shown': dict(kind='photo'),
            'a quote with no one': dict(kind='quote', quote='Q'),
            'a note with a title': dict(kind='note', title='T', text='x'),
            'a date to come': dict(kind='note', text='x', date='2099-01-01'),
            'a bad date': dict(kind='note', text='x', date='soon'),
            'a two-line title': dict(kind='book', title='A\nB', text='x'),
        }
        for name, fields in cases.items():
            with self.subTest(name), self.assertRaises(log_entry.EntryError):
                entry(**fields)


class Adding(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.log = self.dir / 'log.json'
        shutil.copy(ROOT / 'src' / 'log.json', self.log)

    def entries(self):
        return json.loads(self.log.read_text(encoding='utf-8'))['entries']

    def test_new_goes_first_and_backdated_goes_above_its_day(self):
        log_entry.add({'kind': 'note', 'date': TODAY, 'text': 'new'}, self.log)
        log_entry.add({'kind': 'note', 'date': '2026-10-23', 'text': 'late'}, self.log)
        texts = [e.get('text') for e in self.entries()]
        self.assertEqual(texts[0], 'new')
        self.assertEqual(texts[1], 'late')
        self.assertEqual(self.entries()[2]['title'], 'You Can See Everything')

    def test_a_bad_entry_changes_nothing(self):
        before = self.log.read_text(encoding='utf-8')
        with self.assertRaises(log_entry.EntryError):
            log_entry.add({'kind': 'note', 'date': TODAY}, self.log)
        self.assertEqual(self.log.read_text(encoding='utf-8'), before)

    def test_commit_messages(self):
        self.assertEqual(log_entry.commit_message({'kind': 'shipped', 'title': 'X'}), "Add shipped entry for 'X'")
        self.assertEqual(log_entry.commit_message({'kind': 'quote', 'who': 'W'}), 'Add a quote from W to the log')


if __name__ == '__main__':
    unittest.main()
