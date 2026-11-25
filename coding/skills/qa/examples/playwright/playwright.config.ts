// Config for the sample `coding:qa` channel only; the project's own e2e config is untouched.
// No `webServer`: the channel must drive the app the skill started, never a server of its own.
// No retries: a retry would repeat steps that change state.
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'channel.spec.ts',
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: { headless: true, trace: 'off', screenshot: 'off', video: 'off' },
});
