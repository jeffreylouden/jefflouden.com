// Tests for the holiday mark (and the Arsenal match-day gothic A) on the home page. Run with `node --test tests/holiday.test.mjs`.
// The page's own script runs against a stand-in for the mark, a clock set to the day under test, and the test's own
// list of matches, in New York time.

process.env.TZ = 'America/New_York';

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const html = await readFile(new URL('../public/index.html', import.meta.url), 'utf8');
const script = html.match(/<script>\s*(\/\/ The light and dark toggle[\s\S]*?)<\/script>/)[1];
const holiday = script.slice(script.indexOf('// The holiday mark.'), script.indexOf('// Printing from the keyboard'));

function on(y, m, d, matches = [], spooky = '') {
  const mark = { hidden: true, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
  const Real = Date;
  class Clock extends Real {
    constructor(...a) { super(...(a.length ? a : [y, m - 1, d, 12])); }
  }
  const src = holiday.replace(/var matches = \(\s*[\s\S]*?\n\s*\);/, `var matches = ${JSON.stringify(matches)};`);
  assert.notEqual(src, holiday);
  vm.runInNewContext(src, { Date: Clock, Math, document: { getElementById: () => mark, documentElement: { dataset: { spooky } } } });
  return mark.hidden ? null : mark;
}

test('nothing shows on an ordinary day', () => {
  assert.equal(on(2026, 8, 12), null);
  assert.equal(on(2026, 11, 4), null);
});

test('Election Day is the Tuesday after the first Monday in November, shown the week before', () => {
  for (const [y, d] of [[2026, 3], [2027, 2], [2028, 7], [2032, 2]]) {
    assert.equal(on(y, 11, d).attrs['aria-label'], 'Election Day is today. Find your polling place at vote.gov');
  }
  assert.equal(on(2028, 11, 1).attrs['aria-label'], 'Election Day is in 6 days. Find your polling place at vote.gov');
  assert.equal(on(2026, 11, 1).attrs['aria-label'], 'Election Day is in 2 days. Find your polling place at vote.gov');
  assert.equal(on(2026, 11, 1).href, 'https://vote.gov');
  assert.equal(on(2026, 11, 4), null);
});

test('Halloween keeps the corner through October 31, then Election Day takes it', () => {
  assert.equal(on(2026, 10, 31).attrs['aria-label'], 'Halloween is today');
  assert.match(on(2026, 11, 1).attrs['aria-label'], /^Election Day/);
});

test('holidays that move land on the right day', () => {
  assert.equal(on(2026, 11, 26).attrs['aria-label'], 'Thanksgiving is today');
  assert.equal(on(2027, 11, 25).attrs['aria-label'], 'Thanksgiving is today');
  assert.equal(on(2026, 5, 25).attrs['aria-label'], 'Memorial Day is today');
  assert.equal(on(2026, 9, 7).attrs['aria-label'], 'Labor Day is today');
  assert.equal(on(2027, 2, 15).attrs['aria-label'], 'Presidents’ Day is today');
});

test('New Year’s Eve looks ahead to the next year, after the holidays mark', () => {
  assert.equal(on(2026, 12, 20).attrs['aria-label'], 'Happy holidays');
  assert.equal(on(2026, 12, 30).attrs['aria-label'], 'Happy holidays');
  assert.equal(on(2026, 12, 31).attrs['aria-label'], 'New Year’s Day is tomorrow');
  assert.equal(on(2027, 1, 1).attrs['aria-label'], 'New Year’s Day is today');
});

test('Halloween shows the whole week before', () => {
  assert.equal(on(2026, 10, 25).attrs['aria-label'], 'Halloween is in 6 days');
  assert.equal(on(2026, 10, 24), null);
});

test('holidays link to Wikipedia, and Election Day to vote.gov', () => {
  assert.equal(on(2026, 3, 17).href, 'https://en.wikipedia.org/wiki/Saint_Patrick%27s_Day');
  assert.equal(on(2026, 6, 19).href, 'https://en.wikipedia.org/wiki/Juneteenth');
  assert.equal(on(2026, 9, 7).href, 'https://en.wikipedia.org/wiki/Labor_Day_(United_States)');
  assert.equal(on(2026, 11, 3).href, 'https://vote.gov');
  assert.equal(on(2026, 3, 17).attrs.role, undefined);
});

const liverpool = ['2026-11-01T16:30:00Z', 'Liverpool', false, 'Premier League'];
const madrid = ['2026-12-09T20:00:00Z', 'Real Madrid', true, 'Champions League'];
const late = ['2027-01-20T01:00:00Z', 'Somebody', true, 'FA Cup'];

test('a match day shows the gothic A, linked to the fixtures, with the kickoff in local time', () => {
  const m = on(2026, 11, 1, [liverpool]);
  assert.equal(m.attrs['aria-label'], 'Liverpool v Arsenal today at 11:30 AM, Premier League');
  assert.equal(m.href, 'https://www.nytimes.com/athletic/football/team/arsenal/schedule/');
  assert.match(m.innerHTML, /accent/);
  assert.equal(on(2026, 12, 9, [madrid]).attrs['aria-label'], 'Arsenal v Real Madrid today at 3:00 PM, Champions League');
  assert.equal(on(2026, 12, 10, [madrid]), null);
});

test('a match goes by the visitor’s own day', () => {
  assert.match(on(2027, 1, 19, [late]).attrs['aria-label'], /^Arsenal v Somebody today at 8:00 PM, FA Cup/);
  assert.equal(on(2027, 1, 20, [late]), null);
});

test('a match takes over a holiday’s run-up, but not the holiday itself', () => {
  assert.match(on(2026, 11, 1, [liverpool]).attrs['aria-label'], /^Liverpool v Arsenal/);
  assert.equal(on(2026, 11, 3, [['2026-11-03T20:00:00Z', 'Somebody', true, 'League Cup']]).attrs['aria-label'],
    'Election Day is today. Find your polling place at vote.gov');
});

test('the page carries this season’s matches, soonest first', () => {
  const list = JSON.parse(holiday.match(/var matches = \(\s*([\s\S]*?)\n\s*\);/)[1]);
  assert.ok(list.length > 0);
  assert.deepEqual(list.map((m) => m[0]), list.map((m) => m[0]).sort());
});

test('Friday the 13th and Programmers’ Day', () => {
  assert.equal(on(2026, 11, 13).attrs['aria-label'], 'Friday the 13th is today');
  assert.equal(on(2026, 11, 13).href, 'https://en.wikipedia.org/wiki/Friday_the_13th');
  assert.match(on(2026, 11, 13).innerHTML, /rotate\(-35/);
  assert.equal(on(2026, 11, 20), null);
  assert.equal(on(2028, 2, 29), null);
  assert.equal(on(2026, 9, 13).attrs['aria-label'], 'Programmers’ Day is today');
  assert.equal(on(2028, 9, 12).attrs['aria-label'], 'Programmers’ Day is today');
});

test('a full moon takes the corner only when nothing else wants it', () => {
  const m = on(2026, 11, 24, [], 'moon');
  assert.equal(m.attrs['aria-label'], 'Full moon tonight');
  assert.equal(m.href, 'https://en.wikipedia.org/wiki/Full_moon');
  assert.match(on(2026, 11, 26, [], 'moon').attrs['aria-label'], /^Thanksgiving/);
  assert.match(on(2026, 11, 24, [['2026-11-24T20:00:00Z', 'Somebody', true, 'Champions League']], 'moon').attrs['aria-label'], /^Arsenal v Somebody/);
});

// The spooky days, worked out before paint on both pages.
const spookySrc = await readFile(new URL('../src/partials/spooky.html', import.meta.url), 'utf8');
function spookyOn(y, m, d) {
  const root = { dataset: {} };
  class Clock extends Date { constructor(...a) { super(...(a.length ? a : [y, m - 1, d, 12])); } }
  vm.runInNewContext(spookySrc, { Date: Clock, Math, document: { documentElement: root } });
  return root.dataset;
}

test('Halloween, a Friday the 13th and a full moon are dark only', () => {
  assert.deepEqual({ ...spookyOn(2026, 10, 31) }, { theme: 'dark', spooky: 'halloween' });
  assert.deepEqual({ ...spookyOn(2026, 11, 13) }, { theme: 'dark', spooky: 'friday13' });
  assert.deepEqual({ ...spookyOn(2026, 3, 3) }, { theme: 'dark', spooky: 'moon' });
  assert.deepEqual({ ...spookyOn(2026, 11, 2) }, {});
});

test('full moons land on the right day', () => {
  // Total lunar eclipses fall on full moons: September 7 2025 and March 3 2026 (in New York time, as these tests run).
  for (const [y, m, d] of [[2025, 9, 7], [2026, 3, 3], [2026, 8, 28], [2026, 11, 24], [2026, 12, 23]]) {
    assert.equal(spookyOn(y, m, d).spooky, 'moon', `${y}-${m}-${d}`);
    assert.equal(spookyOn(y, m, d + 2).spooky, undefined, `${y}-${m}-${d + 2}`);
  }
});

test('both pages run the spooky days before paint, and the toggle stays hidden on them', async () => {
  const notFound = await readFile(new URL('../public/404.html', import.meta.url), 'utf8');
  for (const page of [html, notFound]) assert.ok(page.indexOf('var spooky') > 0 && page.indexOf('var spooky') < page.indexOf('<body'));
  assert.match(html, /if \(root\.dataset\.spooky\) return;/);
});
