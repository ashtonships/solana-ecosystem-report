/* Run with an existing Playwright installation; no production dependency. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.argv[2]||'http://127.0.0.1:3000/';
const out=process.argv[3];
(async()=>{
 const browser=await chromium.launch();
 const page=await browser.newPage({reducedMotion:'reduce'});
 const checks=[];const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 if(out)fs.mkdirSync(out,{recursive:true});
 for(const width of [320,393,701,768,1024,1280,1440,1920]){
  await page.setViewportSize({width,height:900});
  await page.goto(base+'#overview');
  const cards=await page.locator('.chart-card:visible').evaluateAll(es=>es.map(e=>{
   const b=e.querySelector('.chart-expand-button');if(!b)return null;
   const c=e.getBoundingClientRect(),r=b.getBoundingClientRect();
   const track=e.closest('[data-pulse-track]').getBoundingClientRect();
   return {label:e.getAttribute('aria-label'),cardBottom:c.bottom,buttonBottom:r.bottom,buttonTop:r.top,cardTop:c.top,trackBottom:track.bottom};
  }).filter(Boolean));
  assert(cards.length>0);
  for(const c of cards){
   assert(c.buttonBottom<=c.cardBottom+1,`${width}: action escapes ${JSON.stringify(c)}`);
   assert(c.buttonTop>=c.cardTop,`${width}: action above card`);
   assert(c.buttonBottom<=c.trackBottom+1,`${width}: carousel clips action`);
  }
  if(width>700){
   const header=await page.locator('.prototype-header').evaluate(e=>{
    const r=s=>e.querySelector(s).getBoundingClientRect();
    return {brandRight:r('.prototype-wordmark').right,navLeft:r('.prototype-nav').left,navRight:r('.prototype-nav').right,toolsLeft:r('.prototype-header__tools').left};
   });
   assert(header.brandRight<=header.navLeft+1,`${width}: brand overlaps navigation ${JSON.stringify(header)}`);
   assert(header.navRight<=header.toolsLeft+1,`${width}: navigation overlaps tools`);
  }
  checks.push({width,chartActionsContained:cards.length});
  if(out&&[393,1440].includes(width))await page.screenshot({path:path.join(out,`overview-${width}.png`),fullPage:true});
 }
 for(const width of [701,1024,1440]){
  await page.setViewportSize({width,height:900});await page.goto(base+'#history');
  await page.locator('[data-desktop-history-a]').selectOption('0');
  await page.locator('[data-desktop-history-b]').selectOption('1');
  const panel=page.locator('[data-desktop-history-panel="0:1"]');
  assert(await panel.isVisible());
  const ledger=panel.locator('.desktop-history-ledger');
  assert.equal(await ledger.locator('tbody tr').count(),4);
  const text=await ledger.innerText();assert(text.includes('TPS'));
  const overflow=await ledger.evaluate(e=>e.scrollWidth-e.clientWidth);
  assert(overflow<=1,`${width}: ledger overflows ${overflow}px`);
  await page.locator('[data-desktop-history-b]').selectOption('2');
  assert(await page.locator('[data-desktop-history-panel="0:2"] .desktop-history-ledger').isVisible());
  assert(await panel.isHidden());
  await page.setViewportSize({width:393,height:900});
  assert.equal(await page.locator('[data-history-select-a]').inputValue(),'0');
  assert.equal(await page.locator('[data-history-select-b]').inputValue(),'2');
  await page.setViewportSize({width,height:900});
  assert(await page.locator('[data-desktop-history-panel="0:2"] .desktop-history-ledger').isVisible());
  if(out)await page.screenshot({path:path.join(out,`history-alternate-${width}.png`),fullPage:true});
  checks.push({width,selectedPairLedger:true,responsiveContinuity:true});
 }
 await page.emulateMedia({colorScheme:'dark'});
 await page.setViewportSize({width:1440,height:900});
 assert(await page.locator('[data-desktop-history-panel="0:2"] .desktop-history-ledger').isVisible());
 if(out)await page.screenshot({path:path.join(out,'history-alternate-1440-dark.png'),fullPage:true});
 await page.goto(base+'#overview');
 if(out)await page.screenshot({path:path.join(out,'overview-1440-dark.png'),fullPage:true});
 checks.push({darkModeCaptures:true});
 assert.deepEqual(errors,[]);
 const result={base,checks,pageErrors:errors};
 if(out)fs.writeFileSync(path.join(out,'verification.json'),JSON.stringify(result,null,2));
 console.log(JSON.stringify(result,null,2));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
