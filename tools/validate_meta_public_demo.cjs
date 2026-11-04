'use strict';
const {chromium}=require(process.env.META_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');

const base=process.argv[2]||'http://127.0.0.1:8795';
const screenshot=process.argv[3]||null;

(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const errors=[];
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base,{waitUntil:'networkidle',timeout:45000});
    await page.locator('#savedMetaRestore').waitFor({state:'visible'});
    assert.equal(await page.locator('.public-banner').isVisible(),true);
    assert.equal(await page.locator('.connections').first().isVisible(),false);
    assert.equal((await page.locator('body').innerText()).match(/[가-힣]/),null);
    await page.locator('#savedMetaRestore').click();
    await page.waitForFunction(()=>document.querySelector('.result').dataset.verdict==='PASS');
    assert.equal(await page.locator('#resultHeading').textContent(),'Condition satisfied');
    assert.equal((await page.locator('#title').inputValue()).match(/[가-힣]/),null);
    assert.equal((await page.locator('#instruction').inputValue()).match(/[가-힣]/),null);
    await page.locator('#video').evaluate(video=>video.play());
    await page.waitForFunction(()=>document.querySelector('#video').currentTime>.15);
    await page.locator('#video').evaluate(video=>video.pause());
    await page.locator('#reviewed').check();
    await page.locator('#evaluate').click();
    await page.waitForFunction(()=>document.querySelector('#stage').textContent==='Reviewed verdict');
    assert.equal((await page.locator('#resultReason').textContent()).match(/[가-힣]/),null);
    if(screenshot)await page.screenshot({path:screenshot,fullPage:true});
    const mobile=await browser.newPage({viewport:{width:390,height:844}});
    await mobile.goto(base,{waitUntil:'networkidle',timeout:45000});
    const overflow=await mobile.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth);
    assert(overflow<=1,`mobile overflow: ${overflow}px`);
    assert.deepEqual(errors,[]);
    process.stdout.write(JSON.stringify({passed:true,english_visible_ui:true,saved_recovery:true,video_playback:true,evaluation:true,mobile_overflow_px:overflow,page_errors:errors})+'\n');
  }finally{await browser.close();}
})().catch(error=>{process.stderr.write(String(error)+'\n');process.exit(1);});
