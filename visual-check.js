const { chromium } = require('playwright');
const fs = require('fs');

async function run() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  
  // Enable console logging
  page.on('console', msg => console.log(`[CONSOLE ${msg.type()}]`, msg.text()));
  page.on('pageerror', err => console.log(`[PAGE ERROR]`, err.message));
  
  try {
    console.log('Navigating to dashboard...');
    await page.goto('http://127.0.0.1:8050', { waitUntil: 'networkidle', timeout: 30000 });
    
    // Wait for WebSocket connection and initial data
    await page.waitForTimeout(3000);
    
    // Take full page screenshot
    await page.screenshot({ path: 'screenshots/full-page.png', fullPage: true });
    console.log('Full page screenshot saved');
    
    // Take viewport screenshot
    await page.screenshot({ path: 'screenshots/viewport.png' });
    console.log('Viewport screenshot saved');
    
    // Check for specific elements
    const elements = [
      'header',
      '.cards',
      '.grid',
      '.chart-panel',
      '#price-chart',
      '#rsi-chart', 
      '#macd-chart',
      '.panel',
      '.backtest-panel',
      '.toast-container',
      '.status-dropdown',
      '#theme-toggle',
      '#sidebar-toggle',
    ];
    
    for (const selector of elements) {
      const el = await page.$(selector);
      const visible = el ? await el.isVisible() : false;
      const boundingBox = el ? await el.boundingBox() : null;
      console.log(`Element ${selector}: visible=${visible}, box=${JSON.stringify(boundingBox)}`);
    }
    
    // Check for overlapping elements
    const allElements = await page.$$('*');
    const boxes = [];
    for (const el of allElements) {
      try {
        const box = await el.boundingBox();
        if (box && box.width > 0 && box.height > 0) {
          boxes.push({ selector: await el.evaluate(e => e.tagName + (e.id ? '#' + e.id : '') + (e.className ? '.' + e.className.split(' ')[0] : '')), box });
        }
      } catch {}
    }
    
    // Check for overlaps (simplified)
    let overlapCount = 0;
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i].box;
        const b = boxes[j].box;
        if (a && b) {
          const overlap = !(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y);
          if (overlap && a.width > 50 && a.height > 50 && b.width > 50 && b.height > 50) {
            overlapCount++;
            if (overlapCount <= 10) {
              console.log(`OVERLAP: ${boxes[i].selector} <-> ${boxes[j].selector}`);
            }
          }
        }
      }
    }
    console.log(`Total significant overlaps: ${overlapCount}`);
    
    // Check chart canvases
    const canvases = await page.$$('canvas');
    for (const canvas of canvases) {
      const box = await canvas.boundingBox();
      const parent = await canvas.evaluate(e => e.parentElement?.className);
      console.log(`Canvas: box=${JSON.stringify(box)}, parent=${parent}`);
    }
    
    // Check for CSS issues (elements with 0 size, negative positions, etc.)
    const issues = [];
    for (const { selector, box } of boxes) {
      if (box.width === 0 || box.height === 0) {
        issues.push(`Zero size: ${selector}`);
      }
      if (box.x < -100 || box.y < -100) {
        issues.push(`Negative position: ${selector} at (${box.x}, ${box.y})`);
      }
      if (box.width > 3000 || box.height > 3000) {
        issues.push(`Huge element: ${selector} ${box.width}x${box.height}`);
      }
    }
    if (issues.length) {
      console.log('LAYOUT ISSUES:', issues);
    } else {
      console.log('No obvious layout issues detected');
    }
    
  } catch (err) {
    console.error('Error:', err);
  }
  
  await browser.close();
}

run().catch(console.error);