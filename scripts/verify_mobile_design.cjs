/* Requires an existing Playwright installation. No production dependency. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.argv[2]||'http://localhost:3000/';
const out=process.argv[3];
(async()=>{
 if(out) fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch();
 const context=await browser.newContext({viewport:{width:393,height:852},isMobile:true,hasTouch:true,reducedMotion:'reduce'});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const cdp=await context.newCDPSession(page);
 const swipe=async(x,y,dx,dy)=>{
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
  for(let n=1;n<=8;n++){
   await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x+dx*n/8,y:y+dy*n/8}]});
   await page.waitForTimeout(25);
  }
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  await page.waitForTimeout(400);
 };
 const checks=[];
 await page.goto(base+'#overview');
 const chart=page.locator('[data-overview-chart]:visible').first();
 const track=page.locator('.mobile-network-pulse [data-pulse-track]').first();
 await chart.scrollIntoViewIfNeeded();
 let box=await chart.boundingBox();let start=await track.evaluate(e=>e.scrollLeft);
 await swipe(box.x+box.width*.85,box.y+box.height*.5,-box.width*.65,0);
 assert((await track.evaluate(e=>e.scrollLeft))>start+50,'Swipe starting on chart must move carousel');
 assert(await page.locator('.mobile-network-pulse [data-overview-chart-inspector]').first().isHidden(),'Swipe must not pin tooltip');
 await page.reload(); await chart.scrollIntoViewIfNeeded();
 box=await chart.boundingBox();await page.touchscreen.tap(box.x+box.width*.5,box.y+box.height*.5);
 assert(await page.locator('.mobile-network-pulse [data-overview-chart-inspector]').first().isVisible(),'Tap inspects value');
 checks.push('Real touch swipe on inline chart moves carousel; tap inspects without swipe');
 await page.locator('.chart-expand-button:visible').first().click();
 const dialog=page.locator('dialog.chart-explorer[open]');assert(await dialog.isVisible());
 const expanded=dialog.locator('[data-overview-chart]');box=await expanded.boundingBox();
 start=await track.evaluate(e=>e.scrollLeft);
 await swipe(box.x+box.width*.8,box.y+box.height*.5,-box.width*.55,0);
 assert.equal(await track.evaluate(e=>e.scrollLeft),start,'Expanded scrub must not move carousel');
 assert(await dialog.locator('[data-overview-chart-inspector]').isVisible());
 await page.keyboard.press('Escape');assert(await dialog.count()===0);
 assert(await page.evaluate(()=>document.activeElement.classList.contains('chart-expand-button')));
 assert.equal(await track.evaluate(e=>e.scrollLeft),start,'Close restores card position');
 checks.push('Expanded chart drag owns inspection; Escape restores original card and focus');
 await page.goto(base+'#history');
 const panel=page.locator('.mobile-history-comparison-card');
 assert(await panel.locator('[data-history-picker-trigger="a"]').isVisible());
 assert.equal(await panel.locator('[data-history-chart-panel]:visible').count(),1);
 const chartSurface=panel.locator('.mobile-history-trend:visible');
 const geometry=await panel.evaluate(e=>{const toolbar=e.querySelector('.mobile-history-toolbar')||e.querySelector('[data-history-picker-trigger]').parentElement.parentElement;return {width:e.getBoundingClientRect().width,background:getComputedStyle(e).backgroundColor};});
 assert(geometry.width<=393);
 await panel.locator('[data-history-picker-trigger="a"]').click();
 await page.locator('.mobile-history-picker-option').last().click();
 assert.equal(await panel.locator('[data-history-chart-panel]:visible').count(),1);
 checks.push('History selectors and selected chart share one containing component');
 await page.goto(base+'#data');
 const rail=page.locator('.data-domain-rail:visible');
 assert.equal(await rail.locator('a').count(),5);
 for(const a of await rail.locator('a').all()){
  const href=await a.getAttribute('href');
  assert(await page.locator(href).count(),`Contents target ${href} exists`);
 }
 checks.push('Data contents retains five real section destinations');
 const cards=page.locator('.validator-workbench--mobile [data-validator-metric-card], .growth-workbench--mobile [data-growth-metric-card]');
 const cardCount=await cards.count();assert(cardCount>=10);
 for(let i=0;i<cardCount;i++){
  const card=cards.nth(i);await card.scrollIntoViewIfNeeded();
  const details=card.locator('[data-metric-inspector]');assert.equal(await details.count(),1);
  await details.locator('summary').tap();assert(await details.evaluate(e=>e.open));
  assert((await details.locator('.metric-inspector-panel p').innerText()).length>60);
  if(out && i===0) await page.screenshot({path:path.join(out,'validator-explanation-mobile.png'),fullPage:true});
  await details.locator('[data-metric-inspector-close]').tap();assert(!(await details.evaluate(e=>e.open)));
  const target=card.locator('[data-metric-tap], [data-metric-chart]').first();
  if(await target.count()){
   await target.tap();assert(await details.evaluate(e=>e.open));
   assert((await details.locator('[data-metric-inspector-value]').innerText()).length>0);
   await page.keyboard.press('Escape');assert(!(await details.evaluate(e=>e.open)));
  }
 }
 checks.push(`All ${cardCount} validator and growth cards explain their metric; recorded targets support tap and Escape`);
 await page.goto(base+'#overview');
 await page.locator('.chart-expand-button:visible').first().click();
 if(out) await page.screenshot({path:path.join(out,'expanded-chart-mobile.png'),fullPage:false});
 await page.setViewportSize({width:768,height:900});await page.waitForTimeout(100);
 assert.equal(await page.locator('dialog.chart-explorer[open]').count(),0);
 assert.notEqual(await page.evaluate(()=>document.activeElement.tagName),'BODY');
 await page.locator('.chart-expand-button:visible').first().click();
 await page.goto(base+'#methods');await page.waitForTimeout(100);
 assert.equal(await page.locator('dialog.chart-explorer[open]').count(),0);
 assert.equal(await page.evaluate(()=>document.activeElement.id),'methods-title');
 checks.push('Expanded chart closes on resize or route change and restores visible focus');
 assert.deepEqual(errors,[]);
 if(out){
  fs.mkdirSync(out,{recursive:true});const captures=[];
  for(const theme of ['light','dark']) for(const width of(theme==='light'?[320,393,768,1440]:[393,1440])){
   const p=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});
   p.on('pageerror',e=>errors.push(e.message));await p.goto(base);
   await p.evaluate(t=>localStorage.setItem('solana-report-theme',t),theme);await p.reload();
   for(const route of ['overview','data','methods','history','project']){
    await p.goto(base+'#'+route);await p.waitForTimeout(80);
    assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth-innerWidth),0,`${route} ${width} overflow`);
    const footerOverlap=await p.evaluate(()=>{const a=document.querySelector('.report-footer__brand').getBoundingClientRect(),b=document.querySelector('.report-footer__meta').getBoundingClientRect();return a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;});
    assert.equal(footerOverlap,false,`${route} ${width} footer overlap`);
    const file=`${route}-${width}-${theme}.png`;await p.screenshot({path:path.join(out,file),fullPage:true});
    captures.push({route,width,theme,file,height:await p.evaluate(()=>document.documentElement.scrollHeight)});
   }await p.close();
  }
  fs.writeFileSync(path.join(out,'verification.json'),JSON.stringify({checks,errors,captures},null,2)+'\n');
 }
 console.log(JSON.stringify({checks,errors},null,2));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
