// Sample `coding:qa` accept channel for Playwright. Copy this directory to `qa/` in the
// project under test; the actions it understands are listed in `channel.md`.
//
// Contract (SKILL.md, Channels): QA_STEPS names a JSON file
//   {"criterion": <n>, "steps": [{"action": "<action>", "args": {...}}]}
// and QA_URL is the app's loopback base address. Each step prints one JSON line of raw
// observations. Exit 0 means every step ran; a thrown error exits non-zero, and the skill
// then reports the criterion NOT RUN, never FAIL. Pass/fail is judged by the skill, so
// this file never prints a verdict.
import { errors, test } from '@playwright/test';
import { readFileSync } from 'node:fs';

type Args = Record<string, string>;
type Step = { action: string; args?: Args };
type StepFile = { criterion: number; steps: Step[] };

const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]']);

function baseUrl(): URL {
  const raw = process.env.QA_URL;
  if (!raw) throw new Error('QA_URL is not set');
  const url = new URL(raw);
  if (!LOOPBACK.has(url.hostname)) {
    throw new Error(`QA_URL is not a loopback address: ${url.hostname}`);
  }
  return url;
}

function stepFile(): StepFile {
  const path = process.env.QA_STEPS;
  if (!path) throw new Error('QA_STEPS is not set');
  const parsed = JSON.parse(readFileSync(path, 'utf8')) as StepFile;
  if (!Array.isArray(parsed.steps)) throw new Error('QA_STEPS file has no steps array');
  return parsed;
}

function arg(args: Args, name: string): string {
  const value = args[name];
  if (value === undefined) throw new Error(`missing arg: ${name}`);
  return value;
}

function observe(step: number, action: string, observed: Record<string, unknown>): void {
  console.log(JSON.stringify({ step, action, observed }));
}

test('qa channel', async ({ page }) => {
  const base = baseUrl();
  const file = stepFile();

  for (const [i, { action, args = {} }] of file.steps.entries()) {
    switch (action) {
      case 'goto': {
        const target = new URL(arg(args, 'path'), base);
        if (target.origin !== base.origin) {
          throw new Error(`goto would leave the app: ${target.origin}`);
        }
        const response = await page.goto(target.href);
        observe(i, action, {
          url: page.url(),
          status: response ? response.status() : null,
          title: await page.title(),
        });
        break;
      }
      case 'click': {
        const selector = arg(args, 'selector');
        await page.locator(selector).click();
        await page.waitForLoadState();
        observe(i, action, { selector, url: page.url() });
        break;
      }
      case 'fill': {
        const selector = arg(args, 'selector');
        await page.locator(selector).fill(arg(args, 'value'));
        observe(i, action, { selector, value: await page.locator(selector).inputValue() });
        break;
      }
      case 'press': {
        const selector = arg(args, 'selector');
        await page.locator(selector).press(arg(args, 'key'));
        await page.waitForLoadState();
        observe(i, action, { selector, url: page.url() });
        break;
      }
      case 'text': {
        const selector = arg(args, 'selector');
        observe(i, action, { selector, texts: await page.locator(selector).allInnerTexts() });
        break;
      }
      case 'visible': {
        const selector = arg(args, 'selector');
        observe(i, action, { selector, visible: await page.locator(selector).first().isVisible() });
        break;
      }
      case 'count': {
        const selector = arg(args, 'selector');
        observe(i, action, { selector, count: await page.locator(selector).count() });
        break;
      }
      case 'wait': {
        const selector = arg(args, 'selector');
        const visible = await page.locator(selector).first()
          .waitFor({ state: 'visible', timeout: 5000 })
          .then(() => true, (e: unknown) => {
            if (e instanceof errors.TimeoutError) return false;
            throw e;
          });
        observe(i, action, { selector, visible });
        break;
      }
      case 'url': {
        observe(i, action, { url: page.url() });
        break;
      }
      default:
        throw new Error(`unknown action: ${action}`);
    }

    const current = page.url();
    if (current !== 'about:blank' && new URL(current).origin !== base.origin) {
      throw new Error(`page left the app: ${new URL(current).origin}`);
    }
  }
});
