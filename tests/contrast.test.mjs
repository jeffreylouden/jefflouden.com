// Tests that the flashlight never dims text below WCAG's 4.5:1. Run with `node --test tests/contrast.test.mjs`.
// It reads the dark colors and the flashlight's darkest shadow straight from public/site.css, and the flicker from its
// script, so a change to either that would leave text hard to read fails here.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const css = await readFile(new URL('../public/site.css', import.meta.url), 'utf8');
const flashlight = await readFile(new URL('../src/partials/flashlight.html', import.meta.url), 'utf8');

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
function lum([r, g, b]) {
  const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const mix = (fg, bg, a) => fg.map((c, i) => c * a + bg[i] * (1 - a));
const dim = (c, a) => c.map((v) => v * (1 - a));

// Both dark copies of the tokens, which must agree.
const darkBlocks = [...css.matchAll(/:root(?::not\(\[data-theme="light"\]\)|\[data-theme="dark"\])\s*\{([^}]*)\}/g)].map((m) => m[1]);
const token = (block, name) => block.match(new RegExp(`--${name}:\\s*(#[0-9a-f]{6})`, 'i'))[1];

test('both dark copies of the tokens agree', () => {
  assert.ok(darkBlocks.length >= 2);
  for (const name of ['paper', 'ink', 'muted']) assert.equal(new Set(darkBlocks.map((b) => token(b, name))).size, 1, name);
});

const kinds = () => {
  const bg = hex(token(darkBlocks[0], 'paper')), ink = hex(token(darkBlocks[0], 'ink')), muted = hex(token(darkBlocks[0], 'muted'));
  return { bg, list: [['body text', ink], ['muted text', muted], ['links (80% opacity)', mix(ink, bg, 0.8)]] };
};
const worst = (a) => { const { bg, list } = kinds(); return Math.min(...list.map(([, c]) => ratio(dim(c, a), dim(bg, a)))); };

test('the layer above the text darkens no further than --fs, at every stop', () => {
  const gradient = css.match(/\.flashlight \{[^}]*background:\s*radial-gradient\(([^;]*)\);/)[1];
  assert.equal([...gradient.matchAll(/rgb\(0 0 0 \/ ([\d.]+)\)/g)].length, 0, 'no fixed shadow stops, only --fs');
  for (const m of gradient.matchAll(/calc\(var\(--fs\) \* ([\d.]+)\)/g)) assert.ok(+m[1] <= 1);
});

test('phones and reduced motion keep 4.5:1 in the deepest shadow, and the flicker too', () => {
  const fs = +css.match(/:root \{[^}]*--fs:\s*([\d.]+)/)[1];
  const flicker = Math.max(...[...flashlight.matchAll(/'--fd', '([\d.]+)'/g)].map((m) => +m[1]));
  for (const a of [fs, flicker]) assert.ok(worst(a) >= 4.5, `${a * 100}% shadow leaves ${worst(a).toFixed(2)}:1`);
});

test('where the light can move, text far from it never drops below 3:1', () => {
  const fs = +css.match(/:root\[data-lit\] \{[^}]*--fs:\s*([\d.]+)/)[1];
  assert.ok(worst(fs) >= 3, `${fs * 100}% shadow leaves ${worst(fs).toFixed(2)}:1`);
});

test('the warm glow behind the text never lifts the page so far that muted text loses 4.5:1', () => {
  const bg = hex(token(darkBlocks[0], 'paper')), muted = hex(token(darkBlocks[0], 'muted'));
  const glow = css.match(/\.flashlight-glow \{[^}]*background:\s*radial-gradient\(([^;]*)\);/)[1];
  const warm = Math.max(...[...glow.matchAll(/calc\(var\(--fw\) \* ([\d.]+)\)/g)].map((m) => +m[1]));
  const fw = +css.match(/--fw:\s*([\d.]+)/)[1];
  const lifted = mix([255, 236, 200], bg, warm * fw);
  const r = ratio(muted, lifted);
  assert.ok(r >= 4.5, `muted text on the brightest glow is ${r.toFixed(2)}:1`);
  // And the glow sits behind the text, so its shadow never touches the words.
  assert.match(css, /\.flashlight-glow \{[^}]*z-index: -1;/);
});
