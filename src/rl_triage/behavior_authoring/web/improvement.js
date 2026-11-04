'use strict';
(() => {
  let research = null, pending = false, generation = 0;
  const origins = {
    manual_condition_not_model_analysis: '사용자 조건 · Spark 분석 아님',
    unverified_imported_spark_video_response: '가져온 Spark 영상 응답 · 공급자 인증 미확인',
    authenticated_live_spark_video_response: '실제 Spark 영상 응답',
  };
  function render(value) {
    research = value;
    const hypotheses = value.analysis?.hypotheses || [];
    $('visionStatus').textContent = `${origins[value.analysis_origin] || '영상 요청 준비'} · 영상 SHA ${value.input_manifest.video_sha256.slice(0,16)}… · 검사 ${value.history.length}회 · 새 생성 ${value.new_model_calls}회 / 토큰 계수 ${value.new_preflight_requests||0}회`;
    const cards = hypotheses.map(h => {
      const card = document.createElement('article');card.className = 'hypothesis-card';
      const heading = document.createElement('h3');heading.textContent = `${h.id} · ${h.claim}`;
      const evidence = document.createElement('p');evidence.textContent = `근거 프레임 ${h.evidence_frames.join(', ')} · 교정 제안 ${h.correction_id}`;
      const reason = document.createElement('p');reason.textContent = h.rationale;
      const seek = document.createElement('button');seek.className='secondary';seek.textContent='근거 프레임 보기';
      seek.addEventListener('click',()=>{const row=state.bundle?.samples.find(s=>s.frame===h.evidence_frames[0]);if(row)$('video').currentTime=row.t_s;});
      card.append(heading,evidence,reason,seek);return card;
    });
    $('hypothesisList').replaceChildren(...cards);
    $('visionLimitations').replaceChildren(...(value.analysis?.limitations||[]).map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
    $('trainingHypothesis').replaceChildren(...hypotheses.map(h=>{const o=document.createElement('option');o.value=h.id;o.textContent=`${h.id} · ${h.correction_id}`;return o;}));
    $('researchHistory').replaceChildren(...value.history.map(h=>{const li=document.createElement('li');li.textContent=`${h.round}회 · ${h.selected_hypothesis_id}: ${h.result.verdict} — ${h.result.reason} (저장 기록 검사)`;return li;}));
    $('visionStep').disabled=!hypotheses.length || value.history.length >= Math.min(3,hypotheses.length);
    $('visionReflect').disabled=!value.history.length || value.provider_imports>=3;
    $('visionExport').disabled=false;$('trainingPrepare').disabled=!hypotheses.length;
  }
  function closeSession(value){if(value?.session_id)api('/api/improvement/cancel',{session_id:value.session_id}).catch(()=>{});}
  function reset() {generation++;closeSession(research);research=null;$('hypothesisList').replaceChildren();$('visionLimitations').replaceChildren();$('researchHistory').replaceChildren();$('recoveryResult').hidden=true;for(const id of ['visionStep','visionReflect','visionExport','trainingPrepare','visionLive'])$(id).disabled=true;$('visionStatus').textContent='입력이 변경되었습니다. 새 영상 분석 요청을 준비하세요.';}
  // 이전 입력으로 늦게 도착한 결과를 현재 편집에 적용하지 않는다.
  $('clip').addEventListener('change',reset);for(const id of fieldIds)$(id).addEventListener('input',reset);
  $('restoreExport').addEventListener('change',reset);
  async function action(fn) {if(pending)return;pending=true;error('');const version=state.revision,seq=generation;try{const apply=value=>{if(version!==state.revision||seq!==generation){closeSession(value);return false;}if(research?.session_id!==value.session_id)closeSession(research);render(value);return true;};await fn(apply);}catch(e){error(e.message);}finally{pending=false;}}
  function input(){if(!state.clip||!state.bundle)throw new Error('프레임별 상태가 있는 기록을 선택하세요.');return {example_id:state.clip.id,instruction:$('instruction').value,observations:state.bundle};}
  async function prepare(apply,save=true) {const value=await api('/api/improvement/prepare',input());if(apply({...value,analysis:null,history:[],provider_imports:0,new_model_calls:0,analysis_origin:'prepared_video_request'})){$('visionLive').disabled=!(state.metaStatus?.video_calls_enabled&&state.metaStatus?.enabled);if(save)download('muse-spark-video-request.json',value);return value;}return null;}
  $('visionPrepare').addEventListener('click',()=>action(apply=>prepare(apply)));
  $('visionSeed').addEventListener('click',()=>action(async apply=>{apply(await api('/api/improvement/seed',{...input(),contract:spec()}));}));
  $('visionLive').addEventListener('click',()=>action(async apply=>{if(!research)throw new Error('영상 요청을 먼저 준비하세요.');const value=await api('/api/improvement/analyze',{session_id:research.session_id});if(apply(value)&&value.status)renderMetaStatus(value.status);}));
  $('visionResponse').addEventListener('change',event=>action(async apply=>{try{const file=event.target.files[0];if(!file)return;if(file.size>10*1024*1024)throw new Error('10MB 이하 응답을 선택하세요.');if(!research)throw new Error('영상 분석 요청 저장을 먼저 실행하세요.');apply(await api('/api/improvement/import',{session_id:research.session_id,response:await file.text()}));}finally{event.target.value='';}}));
  $('visionStep').addEventListener('click',()=>action(async apply=>{if(research)apply(await api('/api/improvement/round',{session_id:research.session_id}));}));
  $('visionReflect').addEventListener('click',()=>action(async()=>{if(research)download('muse-spark-video-reflection-request.json',await api('/api/improvement/reflect',{session_id:research.session_id}));}));
  $('visionExport').addEventListener('click',()=>action(async()=>{if(research)download('mollet-research-session.json',await api('/api/improvement/export',{session_id:research.session_id}));}));
  $('trainingPrepare').addEventListener('click',()=>action(async()=>{if(!research)return;const value=await api('/api/improvement/training',{session_id:research.session_id,hypothesis_id:$('trainingHypothesis').value,change_id:$('trainingChange').value||null,recovery_mode:$('trainingMode').value});download('mollet-training-proposal.json',value);$('recoveryStatus').textContent='교정·재학습 제안을 저장했습니다. 실제 case 결합과 기존 승인 대기 절차가 필요하며 학습을 실행하지 않았습니다.';}));
  $('recoveryResponse').addEventListener('change',event=>action(async apply=>{try{const file=event.target.files[0];if(!file)return;if(!research)throw new Error('분석 가설을 먼저 가져오세요.');if(file.size>10*1024*1024)throw new Error('10MB 이하 평가를 선택하세요.');const comparison=await file.text();const value=await api('/api/improvement/recovery',{session_id:research.session_id,hypothesis_id:$('trainingHypothesis').value,comparison});if(!apply(value))return;const r=value.recovery_comparison.result;const names={recovered:'회복 기준 충족',not_recovered:'회복 기준 미충족',undetermined:'판단 보류',unknown:'판단 보류',unnecessary_intervention:'불필요한 개입',regression:'성능 저하'};$('recoveryResult').hidden=false;$('recoveryResult').textContent=`${names[r.outcome||r.status]||r.outcome||r.status} · ${value.recovery_comparison.intervention.correction_id}\n저장 평가 비교 · 새 학습 실행 및 인과 효과 확인 없음`;$('recoveryStatus').textContent='기존 회복 판정기로 비교하고 다음 재검토 입력에 연결했습니다. 상세 근거는 분석 이력에 저장됩니다.';}finally{event.target.value='';}}));
})();
