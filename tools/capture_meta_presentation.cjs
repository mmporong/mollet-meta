'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

function fail(message) { throw new Error(message); }
function arg(name, fallback = null) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : fallback;
}
function readJson(file) { return JSON.parse(fs.readFileSync(file, 'utf8')); }
function digest(file) { return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'); }
function isHash(value) { return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value); }

function validateEvidence(folder) {
  const sessionFile = path.join(folder, 'final-session.json');
  const firstFile = path.join(folder, 'first-response.json');
  const reflectionFile = path.join(folder, 'reflection-response.json');
  const summaryFile = path.join(folder, 'summary.json');
  const firstAnalysis = path.join(folder, 'first-analysis.json');
  const firstRounds = path.join(folder, 'first-rounds.json');
  const reflectionAnalysis = path.join(folder, 'reflection-analysis.json');
  const reflectionRounds = path.join(folder, 'reflection-rounds.json');
  const baselineFile = path.join(folder, 'baseline-before-model.json');
  for (const file of [sessionFile, firstFile, reflectionFile, summaryFile, firstAnalysis, firstRounds, reflectionAnalysis, reflectionRounds, baselineFile]) {
    if (!fs.existsSync(file)) fail(`실제 증거 파일이 없습니다: ${file}`);
  }
  const session = readJson(sessionFile);
  const summary = readJson(summaryFile);
  const baseline = readJson(baselineFile);
  const receipts = session.receipts;
  if (summary.schema !== 'meta-actual-video-loop-presentation-v1' || summary.example_id !== 'go2-down-slow' ||
      summary.status !== 'completed_actual_video_and_reflection' || !summary.actual_video_inference_executed ||
      !summary.actual_reflection_executed || summary.new_generation_requests !== 2 ||
      summary.new_training_executed || summary.performance_improvement_measured) {
    fail('runner의 실제 Go2 영상 분석·재검토 완료 증거가 아닙니다.');
  }
  if (baseline.schema !== 'meta-recorded-static-baseline-v1' || baseline.example_id !== 'go2-down-slow' ||
      baseline.result?.verdict !== 'FAIL' || baseline.model_called !== false ||
      baseline.video_sha256 !== session.input_manifest?.video_sha256 || summary.static_baseline_verdict !== 'FAIL') {
    fail('모델 호출 전 자세 FAIL 기준선이 없거나 최종 입력과 다릅니다.');
  }
  if (session.schema !== 'meta-research-session-v1') fail('final-session.json 스키마가 다릅니다.');
  if (session.example_id !== 'go2-down-slow' || session.example_id !== summary.example_id) fail('최종 세션이 선택한 Go2 전도 사례와 다릅니다.');
  if (session.analysis_origin !== 'authenticated_live_spark_video_response' ||
      session.provider_authentication !== 'authenticated_https_api') {
    fail('인증된 실제 Spark 영상 분석 세션이 아닙니다.');
  }
  if (!session.input_manifest?.sent || !isHash(session.input_manifest.video_sha256)) {
    fail('실제로 전송된 영상 입력 manifest가 없습니다.');
  }
  if (!Array.isArray(receipts) || receipts.length < 2 || session.new_model_calls !== receipts.length) {
    fail('1차 분석과 재검토의 실제 provider 영수증 2건이 필요합니다.');
  }
  if (!Array.isArray(session.cycles) || !session.cycles.length ||
      !Array.isArray(session.cycles[0]?.history) || !session.cycles[0].history.length ||
      !session.analysis || !Array.isArray(session.history) || !session.history.length) {
    fail('1차 가설 검사와 재검토 후 검사가 모두 필요합니다.');
  }
  if (JSON.stringify(readJson(firstAnalysis)) !== JSON.stringify(session.cycles[0].analysis) ||
      JSON.stringify(readJson(firstRounds).history) !== JSON.stringify(session.cycles[0].history) ||
      JSON.stringify(readJson(reflectionAnalysis)) !== JSON.stringify(session.analysis) ||
      JSON.stringify(readJson(reflectionRounds).history) !== JSON.stringify(session.history)) {
    fail('분석·검사 파일이 최종 세션의 1차·재검토 이력과 다릅니다.');
  }
  const files = [firstFile, reflectionFile];
  receipts.slice(0, 2).forEach((receipt, index) => {
    if (receipt.status !== 'completed_http' || !receipt.run_id || !isHash(receipt.request_sha256) ||
        !isHash(receipt.response_sha256) || receipt.response_sha256 !== digest(files[index])) {
      fail(`${index + 1}차 provider 응답과 완료 영수증의 해시가 일치하지 않습니다.`);
    }
    if (receipt.context?.video_sha256 !== session.input_manifest.video_sha256 ||
        receipt.context?.input_kind !== 'video_and_synchronized_state') {
      fail(`${index + 1}차 영수증이 같은 영상 입력에 결합되지 않았습니다.`);
    }
    const proof = index === 0 ? summary.first : summary.reflection;
    if (!proof?.provider_request_id || proof.run_id !== receipt.run_id ||
        proof.request_sha256 !== receipt.request_sha256 || proof.response_sha256 !== receipt.response_sha256 ||
        proof.provider_authentication !== 'authenticated_https_api') {
      fail(`${index + 1}차 runner 요약의 provider ID·SHA 증거가 없습니다.`);
    }
  });
  if (new Set(receipts.slice(0, 2).map(row => row.run_id)).size !== 2) fail('두 provider 실행 ID가 같아 재검토 실행을 증명하지 못합니다.');
  return {session, summary, baseline, files, receipts: receipts.slice(0, 2)};
}

async function main() {
  const evidenceDir = path.resolve(arg('--evidence-dir') || fail('--evidence-dir가 필요합니다.'));
  const out = path.resolve(arg('--out') || fail('--out 새 폴더가 필요합니다.'));
  const base = arg('--base', 'http://127.0.0.1:7706/');
  if (fs.existsSync(out)) fail(`기존 출력 경로는 사용하지 않습니다: ${out}`);
  const checked = validateEvidence(evidenceDir);
  const modulePath = process.env.META_PLAYWRIGHT_MODULE;
  if (!modulePath) fail('세션 전용 META_PLAYWRIGHT_MODULE이 설정되지 않았습니다.');
  const {chromium} = require(modulePath);
  fs.mkdirSync(out, {recursive: false});
  const videoDir = path.join(out, 'browser-video');
  fs.mkdirSync(videoDir);
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  const context = await browser.newContext({
    viewport: {width: 1440, height: 1000},
    recordVideo: {dir: videoDir, size: {width: 1440, height: 1000}},
    acceptDownloads: true,
  });
  const page = await context.newPage();
  const blocked = [];
  await page.route('**/*', route => {
    const request = route.request();
    const url = new URL(request.url());
    const forbidden = request.method() !== 'GET' && (
      url.pathname.startsWith('/api/meta/run') || url.pathname === '/api/improvement/analyze');
    if (forbidden) { blocked.push(`${request.method()} ${url.pathname}`); return route.abort('blockedbyclient'); }
    if (url.origin !== new URL(base).origin) return route.abort('blockedbyclient');
    return route.continue();
  });
  try {
    await page.goto(base, {waitUntil: 'networkidle'});
    await page.waitForFunction(() => document.querySelector('#clip')?.options.length > 0);
    await page.selectOption('#clip', 'go2-down-slow');
    await page.waitForFunction(() => document.querySelector('#recordInfo')?.textContent.includes('상태 표본'));
    await page.locator('#video').evaluate(video => video.play().catch(() => {}));
    await page.waitForTimeout(1600);

    const prepared = page.waitForEvent('download');
    await page.click('#visionPrepare');
    await prepared;
    await page.waitForFunction(() => !document.querySelector('#visionExport').disabled);
    await page.setInputFiles('#visionResponse', checked.files[0]);
    await page.waitForFunction(() => document.querySelectorAll('#hypothesisList article').length > 0);
    await page.locator('.improvement').screenshot({path: path.join(out, '01-first-analysis.png')});
    const firstRounds = checked.session.cycles[0].history.length;
    for (let round = 1; round <= firstRounds; round += 1) {
      await page.click('#visionStep');
      await page.waitForFunction(expected => document.querySelectorAll('#researchHistory li').length === expected, round);
    }
    await page.locator('.improvement').screenshot({path: path.join(out, '02-first-check.png')});

    const reflected = page.waitForEvent('download');
    await page.click('#visionReflect');
    await reflected;
    await page.setInputFiles('#visionResponse', checked.files[1]);
    await page.waitForFunction(() => document.querySelectorAll('#hypothesisList article').length > 0 && document.querySelectorAll('#researchHistory li').length === 0);
    await page.locator('.improvement').screenshot({path: path.join(out, '03-reflection-analysis.png')});
    const reflectionRounds = checked.session.history.length;
    for (let round = 1; round <= reflectionRounds; round += 1) {
      await page.click('#visionStep');
      await page.waitForFunction(expected => document.querySelectorAll('#researchHistory li').length === expected, round);
    }
    await page.locator('.improvement').screenshot({path: path.join(out, '04-reflection-check.png')});

    const exported = page.waitForEvent('download');
    await page.click('#visionExport');
    const download = await exported;
    await download.saveAs(path.join(out, 'ui-replay-session.json'));
    await page.screenshot({path: path.join(out, '05-full-workbench.png'), fullPage: true});
  } finally {
    await context.close();
    await browser.close();
  }
  if (blocked.length) fail(`유료 API 경로 호출이 차단되었습니다: ${blocked.join(', ')}`);
  const videos = fs.readdirSync(videoDir).filter(name => name.endsWith('.webm'));
  if (videos.length !== 1) fail('브라우저 녹화 파일을 확인하지 못했습니다.');
  const summary = {
    schema: 'meta-actual-video-loop-capture-v1',
    evidence_authentication: 'authenticated_https_api_receipts_verified_before_ui_replay',
    ui_replay_authentication: 'unverified_saved_response_import',
    example_id: checked.session.example_id,
    video_sha256: checked.session.input_manifest.video_sha256,
    provider_runs: checked.receipts.map(row => ({run_id: row.run_id, model: row.model, request_sha256: row.request_sha256, response_sha256: row.response_sha256})),
    capture_api_calls: 0,
    capture_gpu_jobs: 0,
    browser_video: `browser-video/${videos[0]}`,
  };
  fs.writeFileSync(path.join(out, 'capture.json'), JSON.stringify(summary, null, 2) + '\n');
  process.stdout.write(JSON.stringify(summary, null, 2) + '\n');
}

if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = {validateEvidence};
