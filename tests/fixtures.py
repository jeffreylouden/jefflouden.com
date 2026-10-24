"""A fixed sample log for the tests, so they never depend on Jeff's real
entries (which grow every day and may carry photos the tests' temporary
folders don't have). The real src/log.json is checked by build.py itself
and by test_build.Committed.
"""

LOG = {
    'entries': [
        {
            'kind': 'movie',
            'date': '2026-10-23',
            'title': 'You Can See Everything',
            'url': 'https://www.imdb.com/title/tt45368240',
            'text': 'See. It. Immediately.',
            'place': 'San Francisco',
        },
        {
            'kind': 'site',
            'date': '2026-10-23',
            'title': 'Hundred Rabbits',
            'url': 'https://100r.co/site/home.html',
            'text': 'What lovely humans.',
        },
    ],
}
