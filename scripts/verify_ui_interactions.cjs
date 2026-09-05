/* Run with an installed Playwright: NODE_PATH=<packages> node scripts/verify_ui_interactions.cjs [url] [evidence-dir]. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.argv[2] || 'http://127.0.0.1:3000/';
const out = process.argv[3];
(async () => {
  const browser = await chromium.launch({headless:true});
  const checks = [];
  const page = await browser.newPage({viewport:{width:393,height:852},reducedMotion:'reduce'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(base+'#overview');
  const chart = page.locator('.chart-carousel svg[tabindex="0"]:visible').first();
  const track = chart.locator('xpath=ancestor::*[@data-pulse-track]');
  await chart.focus();
  const start = await track.evaluate(e=>e.scrollLeft);
  await chart.press('End');
  await page.waitForTimeout(100);
  assert.equal(await track.evaluate(e=>e.scrollLeft),start,'Chart End must not move the carousel');
  await track.focus(); await track.press('End');
  assert((await track.evaluate(e=>e.scrollLeft))>start,'Track End must still work');
  checks.push('Chart and carousel keyboard input stay independent');

  await page.goto(base+'#data');
  await page.locator('#mobile-source-search').fill('recorded');
  await page.waitForTimeout(100);
  const groups = await page.locator('[data-source-group]').evaluateAll(es=>es.filter(e=>!e.hidden).map(e=>e.open));
  assert(groups.length>1 && groups.every(Boolean),'All matching source groups remain open');
  await page.locator('#mobile-source-search').fill('no-such-source-938452');
  await page.waitForTimeout(50);
  assert(await page.locator('#mobile-source-empty').isVisible());
  await page.locator('[data-reset-sources]').click();
  assert.equal(await page.locator('#mobile-source-search').inputValue(),'');
  checks.push('Source search exposes every matching group, empty state and reset');

  await page.goto(base+'#history');
  const trigger=page.locator('[data-history-picker-trigger="a"]');
  await trigger.click();
  assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-selected')),'true');
  await page.keyboard.press('Tab');
  assert(await page.locator('#mobile-history-picker-listbox').isVisible(),'Tab remains inside picker');
  for(let n=0;n<6 && !(await page.evaluate(()=>document.activeElement.classList.contains('mobile-history-picker-footer')));n++) await page.keyboard.press('Tab');
  assert(await page.evaluate(()=>document.activeElement.classList.contains('mobile-history-picker-footer')),'Picker archive link reachable by keyboard');
  await page.keyboard.press('Escape');
  assert(await trigger.evaluate(e=>e===document.activeElement));
  const historyChart=page.locator('.comparison-chart:visible').first();
  await historyChart.focus(); await historyChart.press('Home');
  const tooltip=page.locator('[data-chart-tooltip]:visible');
  assert.match(await tooltip.innerText(),/Recorded sample 1 of/);
  await historyChart.press('ArrowRight');
  assert.match(await tooltip.innerText(),/Recorded sample 2 of/);
  await historyChart.press('End');
  assert.match(await tooltip.innerText(),/Recorded sample 8 of 8/);
  const point=await historyChart.evaluate(e=>{const x=Number(e.dataset.chartLeft)+(Number(e.dataset.chartRight)-Number(e.dataset.chartLeft))*3/7; const p=new DOMPoint(x,140).matrixTransform(e.getScreenCTM());return {x:p.x,y:p.y};});
  await page.mouse.move(point.x,point.y);
  assert.match(await tooltip.innerText(),/Recorded sample 4 of 8/);
  checks.push('History picker focus, archive link, Escape, all-sample keyboard and pointer inspection');
  await trigger.click();
  await page.locator('.mobile-history-picker-option').last().click();
  const pair=await page.locator('[data-history-select-a]').inputValue();
  await page.setViewportSize({width:768,height:900}); await page.waitForTimeout(100);
  assert.equal(await page.locator('[data-desktop-history-a]').inputValue(),pair);
  await page.locator('[data-desktop-history-a]').selectOption('1');
  await page.setViewportSize({width:393,height:852}); await page.waitForTimeout(100);
  assert.equal(await page.locator('[data-history-select-a]').inputValue(),'1');
  checks.push('History selected pair survives both responsive transitions');

  for(const width of [393,768,1440]) {
    await page.setViewportSize({width,height:900}); await page.goto(base+'#project');
    const stream=page.locator('[data-development-stream]:visible');
    await stream.locator('[data-development-filter="all"]').click();
    const count=()=>stream.locator('[data-development-event]:visible').count();
    assert.equal(await count(),12);
    await stream.locator('[data-development-more]').click();
    assert.equal(await count(),24);
    await stream.locator('[data-development-view="grid"]').click();
    assert.equal(await count(),24);
    await stream.locator('[data-development-filter="release"]').click();
    assert((await count())<=12);
  }
  checks.push('Project batches and filter reset work in Timeline and Grid at 393, 768, 1440');
  await page.locator('[data-development-stream]:visible [data-development-filter="all"]').click();
  await page.locator('[data-development-stream]:visible [data-development-more]').click();
  await page.setViewportSize({width:393,height:852}); await page.waitForTimeout(80);
  assert.equal(await page.locator('[data-development-stream]:visible [data-development-event]:visible').count(),24);
  assert.equal(await page.locator('[data-development-stream]:visible [data-development-view="grid"]').getAttribute('aria-pressed'),'true');
  await page.locator('[data-development-stream]:visible [data-development-filter="release"]').click();
  await page.setViewportSize({width:1440,height:900}); await page.waitForTimeout(80);
  assert.equal(await page.locator('[data-development-stream]:visible [data-development-filter="release"]').getAttribute('aria-pressed'),'true');
  checks.push('Project filter, view and expanded batch survive responsive transitions');
  await page.goto(base+'#methods');
  const schedule=page.locator('[data-collection-schedule]:visible');
  assert((await schedule.boundingBox()).height<90,'Closed Methods disclosure is compact');
  await schedule.locator('summary').click();
  assert((await schedule.boundingBox()).height>150);
  checks.push('Methods disclosure grows with content and collapses compactly');
  await page.goto(base+'#overview');
  const colors=await page.locator('.chart-card--tps-overlay:visible').first().evaluate(e=>({a:getComputedStyle(e.querySelector('.chart-series--non-vote polyline')).stroke,b:getComputedStyle(e.querySelector('.sparkline polyline')).stroke}));
  assert.notEqual(colors.a,colors.b,'TPS series retain distinct colors at desktop');
  checks.push('Desktop total and non-vote TPS remain visually distinct');
  for(const route of ['overview','data','methods','history','project']) {
    await page.goto(base+'#'+route);
    assert.equal(await page.locator('.prototype-nav__link[aria-current="page"]').getAttribute('href'),'#'+route);
  }
  await page.goBack();
  assert.equal(await page.locator('.prototype-nav__link[aria-current="page"]').getAttribute('href'),'#history');
  const json=await page.request.get(new URL('report.json',base).href);
  assert(json.ok()); assert((await json.json()).release.release_id);
  const md=await page.request.get(new URL('report.md',base).href); assert(md.ok());
  for(const state of ['loading','empty','error']) {
    await page.goto(base+'?ui='+state+'#data');
    assert.equal(await page.locator('body').getAttribute('data-ui-state'),state);
    assert(await page.locator('.ui-state-surface').isVisible());
  }
  const offline=await browser.newPage({javaScriptEnabled:false,viewport:{width:393,height:852}});
  await offline.goto(base+'#project');
  assert((await offline.locator('[data-development-event]:visible').count())>24);
  await offline.close();
  checks.push('All routes, browser Back, both downloads, UI test states, and no-JavaScript activity fallback');
  assert.deepEqual(errors,[]);
  if(out) {
    fs.mkdirSync(out,{recursive:true});
    const captures=[];
    for(const theme of ['light','dark']) for(const width of (theme==='light'?[320,393,768,1440]:[393,1440])) {
      const p=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});
      await p.goto(base); await p.evaluate(t=>{localStorage.setItem('solana-report-theme',t);},theme); await p.reload();
      for(const route of ['overview','data','methods','history','project']) {
        await p.goto(base+'#'+route); await p.waitForTimeout(80);
        assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth-innerWidth),0,`${route} ${width} overflow`);
        const filename=`${route}-${width}-${theme}.png`;
        await p.screenshot({path:path.join(out,filename),fullPage:true});
        captures.push({route,width,theme,file:filename,height:await p.evaluate(()=>document.documentElement.scrollHeight)});
      }
      await p.close();
    }
    fs.writeFileSync(path.join(out,'verification.json'),JSON.stringify({checks,errors,captures},null,2)+'\n');
  }
  console.log(JSON.stringify({checks,errors},null,2));
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
