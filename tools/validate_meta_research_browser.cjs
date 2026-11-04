'use strict';
// 새 임시 서버와 합성 provider envelope를 사용하는 UI 통합 검사. 실제 모델 품질 검사가 아니다.
const {chromium}=require(process.env.META_PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const base=process.argv[2], out=path.resolve(process.argv[3]), fixture=path.resolve(process.argv[4]);
const sha=raw=>crypto.createHash('sha256').update(raw).digest('hex');
async function main(){
  const report={schema:'meta-video-research-browser-v1',scope:'synthetic_provider_envelope_and_actual_recorded_video_not_live_model_quality',checks:[],page_errors:[],new_model_calls:0,gpu_jobs:0};
  let browser, page;
  try{
    browser=await chromium.launch({channel:'chrome',headless:true});report.browser=browser.version();
    const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
    await context.route('**/*',route=>{
      const url=route.request().url();
      if(url.includes('/api/meta/run/')||url.endsWith('/api/improvement/analyze')||(/^https?:/.test(url)&&!url.startsWith(base+'/')))return route.abort();
      return route.continue();
    });
    page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>report.page_errors.push(e.message));
    const check=name=>report.checks.push({name,passed:true});
    const downloaded=async action=>{const wait=page.waitForEvent('download');await action();const download=await wait;return fs.readFile(await download.path());};
    await page.goto(base,{waitUntil:'networkidle'});await page.waitForFunction(()=>document.querySelector('#caption').textContent.includes('H1'));
    await page.waitForFunction(()=>document.querySelector('#video').readyState>=2);
    await page.locator('#video').press('Space');await page.waitForFunction(()=>document.querySelector('#video').currentTime>.4);await page.locator('#video').press('Space');check('actual_recorded_h1_video_played');
    assert(await page.locator('#visionLive').isDisabled());check('additional_paid_video_call_is_locked');
    await page.locator('#visionSeed').click();await page.waitForFunction(()=>document.querySelector('#visionStatus').textContent.includes('Spark 분석 아님'));
    await page.locator('#visionStep').click();await page.waitForFunction(()=>document.querySelectorAll('#researchHistory li').length===1);
    assert(await page.locator('#visionStep').isDisabled());check('manual_condition_checked_without_claiming_spark_inference');
    const manual=JSON.parse(await downloaded(()=>page.locator('#visionExport').click()));
    assert.equal(manual.analysis_origin,'manual_condition_not_model_analysis');assert.equal(manual.new_model_calls,0);check('actual_session_export_has_no_invented_model_call');
    const prepared=JSON.parse(await downloaded(()=>page.locator('#visionPrepare').click()));
    assert.equal(prepared.request.input[0].content[1].type,'input_video');assert.equal(prepared.sent,false);
    report.prepared_request_sha256=sha(JSON.stringify(prepared.request));report.video_sha256=prepared.input_manifest.video_sha256;
    check('downloaded_request_contains_actual_video_and_structured_output');
    await page.locator('#visionResponse').setInputFiles(path.join(fixture,'response.json'));
    await page.waitForFunction(()=>document.querySelectorAll('.hypothesis-card').length===2);
    assert((await page.locator('#visionStatus').textContent()).includes('공급자 인증 미확인'));check('synthetic_completed_envelope_imported_as_unverified');
    await page.locator('.hypothesis-card button').first().click();check('evidence_frame_jump_available');
    for(let i=1;i<=2;i++){await page.locator('#visionStep').click();await page.waitForFunction(n=>document.querySelectorAll('#researchHistory li').length===n,i);}
    assert(await page.locator('#visionStep').isDisabled());check('two_hypotheses_checked_once_then_stopped');
    await page.locator('#trainingHypothesis').selectOption('reward');
    const proposal=JSON.parse(await downloaded(()=>page.locator('#trainingPrepare').click()));
    assert.equal(proposal.meta_binding.correction_id,'restore_body_frame_reward');assert.equal(proposal.training_started,false);
    assert.equal(proposal.execution_authority,'none');await fs.writeFile(path.join(out,'training-proposal.json'),JSON.stringify(proposal,null,2));
    check('registered_reward_proposal_downloaded_without_execution_authority');
    await page.locator('#recoveryResponse').setInputFiles(path.join(fixture,'recovery.json'));
    await page.waitForFunction(()=>!document.querySelector('#recoveryResult').hidden);
    assert((await page.locator('#recoveryResult').textContent()).includes('회복 기준 충족'));check('fixed_evaluation_consumed_without_new_performance_claim');
    const reflect=JSON.parse(await downloaded(()=>page.locator('#visionReflect').click()));
    assert(reflect.request.input[0].content[0].text.includes('imported_fixed_evaluations_unverified_execution'));
    assert(reflect.request.input[0].content[0].text.includes('recovered'));check('recovery_comparison_is_included_in_next_spark_reflection');
    const exported=JSON.parse(await downloaded(()=>page.locator('#visionExport').click()));
    assert.equal(exported.history.length,2);assert.equal(exported.external_evaluations.length,1);
    assert.equal(exported.external_evaluations[0].comparison.performance_improvement_measured,false);
    await fs.writeFile(path.join(out,'research-session.json'),JSON.stringify(exported,null,2));check('full_hypotheses_checks_and_recovery_history_exported');
    await page.locator('#video').scrollIntoViewIfNeeded();
    await page.locator('#video').evaluate(v=>{v.currentTime=7;return v.play();});
    await page.waitForFunction(()=>document.querySelector('#video').currentTime>7.15);
    await page.locator('#video').evaluate(v=>v.pause());
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await page.screenshot({path:path.join(out,'research-workbench.png'),fullPage:true});
    await page.locator('#threshold').fill('0.9');assert(await page.locator('#visionExport').isDisabled());
    assert.equal(await page.locator('.hypothesis-card').count(),0);check('condition_edit_invalidates_previous_research_session');
    assert.equal(report.page_errors.length,0);assert(await page.locator('#error').isHidden());
    report.passed=true;
  }catch(e){report.passed=false;report.error=String(e);if(page){report.product_error=await page.locator('#error').textContent();report.recovery_status=await page.locator('#recoveryStatus').textContent();await page.screenshot({path:path.join(out,'failed-workbench.png'),fullPage:true});}throw e;}
  finally{if(browser)await browser.close();report.browser_closed=true;await fs.writeFile(path.join(out,'browser-verification.json'),JSON.stringify(report,null,2));}
}
main().catch(e=>{process.stderr.write(String(e)+'\n');process.exitCode=1;});
