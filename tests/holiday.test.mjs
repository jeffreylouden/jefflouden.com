// Tests for the holiday mark on the home page. Run with `node --test tests/holiday.test.mjs`.
// The page's own script runs against a stand-in for the mark and a clock set to the day under test.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const html = await readFile(new URL('../public/index.html', import.meta.url), 'utf8');
const script = html.match(/<script>\s*(\/\/ The light and dark toggle[\s\S]*?)<\/script>/)[1];
const holiday = script.slice(script.indexOf('// The holiday mark.'), script.indexOf('// Printing from the keyboard'));

function on(y, m, d) {
  const mark = { hidden: true, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
  const Real = Date;
  class Clock extends Real {
    constructor(...a) { super(...(a.length ? a : [y, m - 1, d, 12])); }
  }
  vm.runInNewContext(holiday, { Date: Clock, Math, document: { getElementById: () => mark } });
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

test('marks without a link are labelled images', () => {
  const m = on(2026, 3, 17);
  assert.equal(m.attrs.role, 'img');
  assert.equal(m.href, undefined);
});
