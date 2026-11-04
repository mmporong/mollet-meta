'use strict';
// 실제 과거 Meta 응답 복구와 현재 로컬 UI를 녹화한다. 모델 호출은 차단한다.
const {chromium}=require(process.env.META_PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const crypto=require('node:crypto');
const {execFileSync}=require('node:child_process');
const assert=require('node:assert/strict');
const base=process.env.META_WORKBENCH_URL||'http://127.0.0.1:7706';
const out=path.resolve(process.argv[2]);
const root=path.resolve(__dirname,'..');
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');

async function main(){
  await fs.mkdir(out,{recursive:false});
  const report={schema:'meta-saved-recovery-capture-v1',scope:'actual_historical_provider_results_replayed_in_current_browser',
    source_commit:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),url:base,
    checks:[],page_errors:[],blocked_provider_requests:[],screenshots:[],downloads:[],new_model_calls:0,gpu_jobs:0,browser_closed:false};
  const status=async()=>(await(await fetch(base+'/api/meta/status')).json()).status;
  let browser,page;
  const check=(name,details={})=>report.checks.push({name,passed:true,...details});
  try{
    report.status_before=await status();
    const saved=await(await fetch(base+'/api/recovery/saved')).json();
    assert.equal(saved.available,true);assert.equal(saved.replay.new_model_calls,0);
    assert.equal(saved.replay.verification_level,'local_provider_receipts_verified');
    report.replay=saved.replay;report.video_sha256=saved.clip.video_sha256;
    report.original_condition=saved.contract.condition;
    check('actual_historical_spark_sam_receipts_verified');
    browser=await chromium.launch({channel:'chrome',headless:true});report.browser=browser.version();
    const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true,
                                           recordVideo:{dir:path.join(out,'browser-video'),size:{width:1440,height:1000}}});
    await context.route('**/*',route=>{
      const url=route.request().url();
      if(url.includes('/api/meta/run/')||url.includes('/api/improvement/analyze')||(/^https?:/.test(url)&&!url.startsWith(base+'/'))){report.blocked_provider_requests.push(url);return route.abort();}
      return route.continue();
    });
    page=await context.newPage();page.setDefaultTimeout(15000);
    page.on('pageerror',error=>report.page_errors.push(error.message));
    const screenshot=async(name,selector)=>{
      const file=name+'.png';const target=selector?page.locator(selector):page;
      await target.screenshot({path:path.join(out,file),...(selector?{}:{fullPage:true})});
      report.screenshots.push({file,sha256:hash(await fs.readFile(path.join(out,file)))});
    };
    const download=async(name,action)=>{const wait=page.waitForEvent('download');await action();const item=await wait;await item.saveAs(path.join(out,name));const raw=await fs.readFile(path.join(out,name));report.downloads.push({file:name,sha256:hash(raw),bytes:raw.length});return JSON.parse(raw.toString('utf8'));};
    const restore=async()=>{
      await page.locator('#savedMetaRestore').click();
      await page.waitForFunction(()=>document.querySelector('#clip').value==='h1-up'&&document.querySelector('#hold').value==='0.5'&&document.querySelector('.result').dataset.verdict==='PASS');
      assert.equal(await page.locator('#reviewed').isChecked(),false);
    };
    const evaluate=async expected=>{
      await page.locator('#reviewed').check();await page.locator('#evaluate').click();
      await page.waitForFunction(verdict=>document.querySelector('.result').dataset.verdict===verdict,expected);
    };
    const playAt=async(seconds)=>{
      await page.locator('#video').scrollIntoViewIfNeeded();await page.waitForFunction(()=>document.querySelector('#video').readyState>=2);
      await page.locator('#video').evaluate((video,t)=>{video.currentTime=t;return video.play();},seconds);
      await page.waitForFunction(t=>document.querySelector('#video').currentTime>t+.15,seconds);
      await page.locator('#video').evaluate(v=>v.pause());
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    };
    await page.goto(base,{waitUntil:'networkidle'});
    await page.locator('#savedMetaRestore').waitFor({state:'visible'});
    await restore();check('saved_provider_results_restore_without_api_key');
    await playAt(7);
    const pixels=await page.locator('#overlay').evaluate(c=>{const a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let count=0;for(let i=3;i<a.length;i+=4)if(a[i])count++;return count;});
    assert(pixels>100);check('actual_h1_playback_and_restored_sam_bbox',{nontransparent_pixels:pixels});
    await screenshot('01-restored-workspace','.workspace');
    await evaluate('PASS');
    const first=await download('01-provider-contract.json',()=>page.locator('#export').click());
    assert.equal(first.result.verdict,'PASS');assert.equal(first.contract.condition.hold_s,.5);
    assert.equal(first.observations.samples.length,625);
    assert.equal(first.observations.samples.filter(s=>s.tracks.length).length,624);
    assert.equal(first.observations.provenance.capture_approved,false);assert.equal(first.observations.provenance.model_ready,false);
    report.provider_result=first.result;check('review_download_625_original_states_and_624_tracks');
    await screenshot('02-provider-result','.result');
    await page.locator('#hold').fill('8');assert.equal(await page.locator('#reviewed').isChecked(),false);
    await evaluate('PASS');
    const manual=await download('02-manual-eight-second-contract.json',()=>page.locator('#export').click());
    assert.equal(manual.contract.condition.hold_s,8);assert.equal(manual.result.verdict,'PASS');
    assert.equal(manual.result.interval.end_s-manual.result.interval.start_s,8);
    report.manual_eight_second_result=manual.result;check('user_condition_edit_resets_review_and_rechecks_full_eight_seconds');
    await page.locator('#restoreExport').setInputFiles(path.join(out,'02-manual-eight-second-contract.json'));
    await page.waitForFunction(()=>document.querySelector('#hold').value==='8'&&state.result?.stage==='draft'&&state.result?.approval_status==='unreviewed');
    assert.equal(await page.locator('#reviewed').isChecked(),false);check('manual_edited_export_roundtrip_preserves_values_and_resets_approval');
    await screenshot('03-eight-second-workspace','.workspace');
    await page.locator('#threshold').fill('1');await page.locator('#deadline').fill('0');await evaluate('FAIL');
    const fail=await download('03-manual-strict-condition.json',()=>page.locator('#export').click());
    assert.equal(fail.result.verdict,'FAIL');check('stricter_condition_returns_fail');
    await restore();check('repeat_recovery_restores_original_provider_draft');
    await page.locator('#clip').selectOption('go2-down-slow');
    await page.waitForFunction(()=>document.querySelector('#video').getAttribute('src')==='/media/go2-down-slow'&&!document.querySelector('#evaluate').disabled);
    await page.locator('#threshold').fill('0.8');await page.locator('#hold').fill('12.48');await page.locator('#deadline').fill('0');
    await evaluate('FAIL');await playAt(11.7);
    const quadruped=await download('04-go2-recorded-condition.json',()=>page.locator('#export').click());
    assert.equal(quadruped.result.verdict,'FAIL');assert.equal(quadruped.contract.source.model_called,false);
    report.go2_result=quadruped.result;check('recorded_go2_time_condition_fail_is_manual_not_a_new_model_result');
    await screenshot('04-go2-failure-workspace','.workspace');await screenshot('05-go2-failure-result','.result');
    await restore();await playAt(7);await screenshot('06-full-workbench');
    assert.equal(await page.locator('html').getAttribute('lang'),'en');
    const englishViews=[];
    for(const width of [1440,900,390]){
      await page.setViewportSize({width,height:1000});
      const content=await page.locator('body').innerText();
      const inputValues=await page.locator('input:not([type=password]):not([type=file]),textarea').evaluateAll(nodes=>nodes.map(n=>n.value).join(' '));
      const hangul=(content+' '+inputValues).match(/[가-힣]/g)||[];
      assert.equal(hangul.length,0,'Visible Korean remains in the English workbench');
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1);
      assert.equal(overflow,false,`Horizontal overflow at ${width}px`);
      await screenshot(`english-layout-${width}`);
      englishViews.push({width,visible_korean_characters:hangul.length,horizontal_overflow:overflow});
    }
    check('english_interface_and_responsive_layout',{viewports:englishViews});
    report.ui_language='en';report.visible_korean_text=[];report.english_ui_text_check_passed=true;
    report.status_after=await status();
    assert.equal(report.status_after.live_requests_started,report.status_before.live_requests_started);
    assert.equal(report.status_after.reserved_usd,report.status_before.reserved_usd);
    assert.equal(report.status_after.key_configured,report.status_before.key_configured);
    assert.deepEqual(report.blocked_provider_requests,[]);assert.deepEqual(report.page_errors,[]);
    check('recovery_and_exports_preserve_credentials_budget_and_provider_call_count');
    await context.close();report.passed=true;
  }catch(error){report.passed=false;report.error=String(error);if(page&&!page.isClosed()){report.ui_error=await page.locator('#error').textContent();await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});}throw error;}
  finally{
    if(browser)await browser.close();report.browser_closed=true;
    const recordings=await fs.readdir(path.join(out,'browser-video')).catch(()=>[]);
    report.browser_video=recordings.find(name=>name.endsWith('.webm'))||null;
    if(report.browser_video){const raw=await fs.readFile(path.join(out,'browser-video',report.browser_video));report.browser_video_sha256=hash(raw);report.browser_video_bytes=raw.length;}
    await fs.writeFile(path.join(out,'verification.json'),JSON.stringify(report,null,2)+'\n');
  }
  process.stdout.write(JSON.stringify({passed:report.passed,checks:report.checks.length,new_model_calls:0,out})+'\n');
}
main().catch(error=>{process.stderr.write(String(error)+'\n');process.exitCode=1;});
