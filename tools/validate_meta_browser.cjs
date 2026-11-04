'use strict';
// META_PLAYWRIGHT_MODULE로 세션 전용 Playwright 모듈 경로를 지정할 수 있다.
const { chromium } = require(process.env.META_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const out = path.resolve(process.argv[2] || path.join(root, 'evals/results/meta_browser_e2e_20261104_v1'));
const base = 'http://127.0.0.1:7705';
const source = path.join(root, 'evals/results/meta_live_provider_20261104_v1/mollet-behavior-contract.json');
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

async function main() {
  await fs.mkdir(out); // 기존 실행 근거를 덮어쓰지 않는다.
  const report = { checked_at: new Date().toISOString(), url: base, checks: [], page_errors: [], http_errors: [], forbidden_requests: [], screenshots: [], browser_closed: false };
  let browser;
  let page;
  let failed;
  const status = async () => (await (await fetch(base + '/api/meta/status')).json()).status;
  try {
    report.status_before = await status();
    report.playwright_version = require(path.join(path.dirname(require.resolve(process.env.META_PLAYWRIGHT_MODULE || 'playwright')), 'package.json')).version;
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    report.browser = browser.version();
    const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, acceptDownloads: true });
    await context.route('**/*', route => {
      const url = route.request().url();
      if (url.includes('/api/meta/run/') || (/^https?:/.test(url) && !url.startsWith(base + '/'))) {
        report.forbidden_requests.push(url); return route.abort();
      }
      return route.continue();
    });
    page = await context.newPage();
    page.setDefaultTimeout(12000);
    page.on('pageerror', error => report.page_errors.push(error.message));
    page.on('response', response => {
      if (response.status() >= 400 && /\/(api|media)\//.test(response.url())) report.http_errors.push({url:response.url(),status:response.status()});
    });
    const check = (name, details = {}) => report.checks.push({ name, passed: true, ...details });
    const screenshot = async name => {
      const file = name + '.png'; await page.screenshot({ path: path.join(out, file), fullPage: true });
      report.screenshots.push({ file, sha256: digest(await fs.readFile(path.join(out, file))) });
    };
    const loadedVideo = async () => page.waitForFunction(() => {
      const video = document.querySelector('#video'); return video.readyState >= 2 && video.videoWidth === 1280 && video.duration > 12;
    });
    const play = async () => {
      await loadedVideo();
      await page.locator('#video').press('Space');
      await page.waitForFunction(() => { const v = document.querySelector('#video'); return !v.paused && v.currentTime > 0.45; });
      const details = await page.locator('#video').evaluate(v => ({ current_time: v.currentTime, duration: v.duration, width: v.videoWidth, height: v.videoHeight, paused: v.paused }));
      await page.locator('#video').press('Space');
      assert.equal(await page.locator('#video').evaluate(v => v.paused), true);
      return details;
    };
    await page.goto(base, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => document.querySelector('#caption').textContent.includes('H1'));
    const clips = await page.locator('#clip option').evaluateAll(items => items.map(item => ({ id:item.value, disabled:item.disabled })));
    assert.equal(clips.length, 6); assert(clips.every(item => !item.disabled));
    check('six_recordings_available');
    for (const clip of clips) {
      await page.locator('#clip').selectOption(clip.id);
      await page.waitForFunction(id => document.querySelector('#video').getAttribute('src') === '/media/' + id, clip.id);
      check('select_and_play_' + clip.id, await play());
      assert.equal(await page.locator('#evaluate').isDisabled(), clip.id.endsWith('-fast'));
    }
    check('summary_only_examples_disable_time_evaluation');
    await page.locator('#restoreExport').setInputFiles(source);
    await page.waitForFunction(() => document.querySelector('#importStatus').textContent.includes('복구했습니다'));
    assert.equal(await page.locator('#clip').inputValue(), 'h1-up');
    assert.equal(await page.locator('#threshold').inputValue(), '0.8');
    assert.equal(await page.locator('#deadline').inputValue(), '8');
    assert.equal(await page.locator('#hold').inputValue(), '0.5');
    assert.equal(await page.locator('#reviewed').isChecked(), false);
    assert.equal(await page.locator('#stage').textContent(), '미검토 초안');
    assert.deepEqual(await page.locator('#form :invalid').evaluateAll(items => items.map(item => ({id:item.id,message:item.validationMessage}))), []);
    check('restored_half_second_hold_passes_native_html_constraint_validation');
    check('actual_saved_spark_sam_export_restored_with_review_reset');
    await play();
    const alphaPixels = await page.locator('#overlay').evaluate(canvas => {
      const pixels = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
      let count = 0; for(let i=3;i<pixels.length;i+=4) if(pixels[i]) count++; return count;
    });
    assert(alphaPixels > 100);
    check('sam_bbox_drawn_in_actual_canvas', { nontransparent_pixels: alphaPixels });
    await screenshot('01-restored-sam');
    await page.locator('#reviewed').check();
    await page.locator('#evaluate').click();
    await page.waitForFunction(() => document.querySelector('#stage').textContent === '조건 확인 후 판정');
    assert.equal(await page.locator('#resultHeading').textContent(), '조건 충족');
    check('review_and_evaluate_pass');
    await screenshot('02-reviewed-pass');
    const downloaded = page.waitForEvent('download');
    await page.locator('#export').click();
    const download = await downloaded;
    assert.equal(download.suggestedFilename(), 'mollet-behavior-contract.json');
    const exportPath = path.join(out, 'browser-downloaded-contract.json');
    await download.saveAs(exportPath);
    const raw = await fs.readFile(exportPath);
    const saved = JSON.parse(raw);
    const expected = JSON.parse(await fs.readFile(source, 'utf8'));
    assert.equal(saved.clip.video_sha256, expected.clip.video_sha256);
    assert.equal(saved.observations.samples.length, 625);
    assert.equal(saved.observations.samples.filter(s => s.tracks.length).length, 624);
    assert.equal(saved.result.verdict, 'PASS');
    assert.equal(saved.result.stage, 'reviewed');
    assert.deepEqual(saved.result.interval, { start_s: 0, end_s: 0.5 });
    assert.equal(saved.approval.actor, 'local_browser_review');
    assert.equal(saved.contract.source.provider_authentication, 'unverified_saved_response');
    assert.equal(saved.observations.provenance.capture_approved, false);
    assert.equal(saved.observations.provenance.model_ready, false);
    assert.equal(saved.model_calls_in_this_app, report.status_before.live_requests_started);
    report.download = { file: path.basename(exportPath), sha256: digest(raw), bytes: raw.length, verdict: saved.result.verdict };
    check('real_browser_download_and_saved_json_content');
    await page.locator('#threshold').fill('1.0');
    assert.equal(await page.locator('#reviewed').isChecked(), false);
    await page.locator('#reviewed').check(); await page.locator('#evaluate').click();
    await page.waitForFunction(() => document.querySelector('#resultHeading').textContent === '조건 위반');
    check('changed_threshold_resets_review_and_returns_fail');
    await page.locator('#restoreExport').setInputFiles(exportPath);
    await page.waitForFunction(() => document.querySelector('#threshold').value === '0.8' && document.querySelector('#resultHeading').textContent === '조건 충족');
    assert.equal(await page.locator('#reviewed').isChecked(), false);
    assert.equal(await page.locator('#stage').textContent(), '미검토 초안');
    check('browser_download_reload_roundtrip_and_approval_reset');
    await page.locator('#restoreExport').setInputFiles({name:'invalid.json',mimeType:'application/json',buffer:Buffer.from('{}')});
    await page.locator('#error').waitFor({state:'visible'});
    assert.match(await page.locator('#error').textContent(), /내보내기 형식/);
    assert.equal(await page.locator('#threshold').inputValue(), '0.8');
    check('invalid_export_rejected_without_overwriting_current_contract');
    report.status_after = await status();
    assert.equal(report.status_after.live_requests_started, report.status_before.live_requests_started);
    assert.equal(report.status_after.reserved_usd, report.status_before.reserved_usd);
    assert.equal(report.status_after.key_configured, true);
    assert.deepEqual(report.forbidden_requests, []);
    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.http_errors, []);
    check('no_additional_meta_calls_or_browser_runtime_errors');
    report.passed = true;
    await context.close();
  } catch (error) {
    failed = error; report.passed = false; report.error = error.stack;
    if (page && !page.isClosed()) {
      report.failure_ui = await page.evaluate(() => ({ error:document.querySelector('#error')?.textContent,stage:document.querySelector('#stage')?.textContent,invalid:Array.from(document.querySelectorAll('#form :invalid')).map(item=>({id:item.id,message:item.validationMessage})) }));
      await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});
    }
  } finally {
    if (browser) { await browser.close(); report.browser_closed = true; }
    await fs.writeFile(path.join(out,'verification.json'), JSON.stringify(report,null,2)+'\n');
  }
  console.log(JSON.stringify({ passed:report.passed,checks:report.checks.length,out,error:report.error },null,2));
  if (failed) throw failed;
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
