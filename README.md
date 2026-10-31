# jefflouden.com

The site is plain static files in `public/`, served as is. Open `public/index.html` in any browser, no server needed.

The pages are built from `src/` by `build.py`, which needs only Python 3:

- `src/site.json` holds the facts (profile, roles, organizations, links), checked against the schema beside it
- `src/templates/` holds each page's shell and `src/partials/` the pieces they share
- `python3 build.py` writes whatever changed into `public/` (and `resume/resume.html`), and `python3 build.py --check` fails if anything is stale
- `python3 -m unittest discover tests` tests the build and `node --test tests/middleware.test.mjs` the Cloudflare middleware, with nothing to install

The built files are committed, so the site works, and can be edited by hand, without the build. After changing the résumé, render its PDF with `resume/build.sh`.

**Please support free and open-source software.**
