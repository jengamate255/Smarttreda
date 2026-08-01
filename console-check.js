const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.on('console', msg => console.log('[CONSOLE ' + msg.type() + ']', msg.text()));
  page.on('pageerror', err => console.log('[PAGE ERROR]', err.message));
  await page.goto('http://127.0.0.1:8050', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(5000);
  await browser.close();
})().catch(console.error);