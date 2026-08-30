const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });

  for (const [label, port] of [['Prop', 8055], ['Deriv', 8052]]) {
    console.log(`\n--- ${label} :${port} ---`);
    const page = await context.newPage();
    
    // Collect console errors
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    page.on('pageerror', err => errors.push(err.message));

    try {
      await page.goto(`http://127.0.0.1:${port}`, { waitUntil: 'networkidle', timeout: 30000 });
      
      // Wait for WS to connect and data to populate
      await page.waitForTimeout(5000);

      // Check connection status dot
      const dotClass = await page.$eval('#status-dot', el => el.className);
      const statusText = await page.$eval('#status-text', el => el.textContent);
      const isLive = dotClass.includes('on') || statusText === 'Live';
      console.log(`  Status dot: ${dotClass} | Text: "${statusText}" | Live: ${isLive}`);

      // Check account data
      const balance = await page.$eval('#c-balance', el => el.textContent);
      const equity = await page.$eval('#c-equity', el => el.textContent);
      const login = await page.$eval('#h-login', el => el.textContent);
      console.log(`  Login: ${login} | Balance: ${balance} | Equity: ${equity}`);

      // Check positions count
      const posCount = await page.$eval('#h-pos-count', el => el.textContent);
      console.log(`  Positions: ${posCount}`);

      // Check server time
      const serverTime = await page.$eval('#h-server-time', el => el.textContent);
      console.log(`  Server time: ${serverTime}`);

      // Check if chart rendered
      const chartHasContent = await page.$eval('#price-chart', el => el.children.length > 0);
      console.log(`  Chart rendered: ${chartHasContent}`);

      // Check active tabs
      const activeSections = await page.$$eval('.hf-section.active', els => els.map(e => e.id));
      console.log(`  Active sections: ${activeSections.join(', ')}`);

      // Check if live content is visible (hf-section-live has children)
      const liveChildren = await page.$eval('#hf-section-live', el => el.children.length);
      console.log(`  Live section children: ${liveChildren}`);

      // Check for JS errors
      console.log(`  Console errors: ${errors.length}`);
      if (errors.length > 0) {
        errors.slice(0, 5).forEach(e => console.log(`    - ${e.substring(0, 120)}`));
      }

      // Screenshot
      const ssPath = `E:\\Project\\Mt5 Ea\\logs\\screenshot_${label.toLowerCase()}.png`;
      await page.screenshot({ path: ssPath, fullPage: false });
      console.log(`  Screenshot: ${ssPath}`);

      console.log(`  RESULT: ${isLive ? 'PASS' : 'FAIL'}`);

    } catch (e) {
      console.log(`  ERROR: ${e.message.substring(0, 120)}`);
    }
    await page.close();
  }

  await browser.close();
  console.log('\nDone.');
})();
