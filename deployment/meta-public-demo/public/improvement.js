'use strict';
(() => {
  $('metaKeyOnly').addEventListener('click',()=>perform(async()=>{const key=$('metaApiKey');if(!key.value)throw new Error('Enter an existing API key.');setLivePending(true);try{const value=await api('/api/meta/configure',{api_key:key.value});renderMetaStatus(value.status);}finally{key.value='';setLivePending(false);}}));
  let research = null, pending = false, generation = 0;
  const origins = {
    manual_condition_not_model_analysis: 'User condition · not Spark analysis',
    unverified_imported_spark_video_response: 'Imported Spark video response · provider authentication unverified',
    authenticated_live_spark_video_response: 'Live Spark video response',
  };
  function render(value) {
    research = value;
    const hypotheses = value.analysis?.hypotheses || [];
    $('visionStatus').textContent = `${origins[value.analysis_origin] || 'Video request prepared'} · video SHA ${value.input_manifest.video_sha256.slice(0,16)}… · checks ${value.history.length} · new generations ${value.new_model_calls} / token-count requests ${value.new_preflight_requests||0}`;
    const cards = hypotheses.map(h => {
      const card = document.createElement('article');card.className = 'hypothesis-card';
      const heading = document.createElement('h3');heading.textContent = `${h.id} · ${h.claim}`;
      const evidence = document.createElement('p');evidence.textContent = `Evidence frames ${h.evidence_frames.join(', ')} · correction proposal ${h.correction_id}`;
      const reason = document.createElement('p');reason.textContent = h.rationale;
      const seek = document.createElement('button');seek.className='secondary';seek.textContent='View evidence frame';
      seek.addEventListener('click',()=>{const row=state.bundle?.samples.find(s=>s.frame===h.evidence_frames[0]);if(row)$('video').currentTime=row.t_s;});
      card.append(heading,evidence,reason,seek);return card;
    });
    $('hypothesisList').replaceChildren(...cards);
    $('visionLimitations').replaceChildren(...(value.analysis?.limitations||[]).map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
    $('trainingHypothesis').replaceChildren(...hypotheses.map(h=>{const o=document.createElement('option');o.value=h.id;o.textContent=`${h.id} · ${h.correction_id}`;return o;}));
    $('researchHistory').replaceChildren(...value.history.map(h=>{const li=document.createElement('li');li.textContent=`Round ${h.round} · ${h.selected_hypothesis_id}: ${h.result.verdict} — ${h.result.reason} (recorded-run evaluation)`;return li;}));
    $('visionStep').disabled=!hypotheses.length || value.history.length >= Math.min(3,hypotheses.length);
    $('visionReflect').disabled=!value.history.length || value.provider_imports>=3;
    $('visionExport').disabled=false;$('trainingPrepare').disabled=!hypotheses.length;
  }
  function closeSession(value){if(value?.session_id)api('/api/improvement/cancel',{session_id:value.session_id}).catch(()=>{});}
  function reset() {generation++;closeSession(research);research=null;$('hypothesisList').replaceChildren();$('visionLimitations').replaceChildren();$('researchHistory').replaceChildren();$('recoveryResult').hidden=true;for(const id of ['visionStep','visionReflect','visionExport','trainingPrepare','visionLive'])$(id).disabled=true;$('visionStatus').textContent='Inputs changed. Prepare a new video-analysis request.';}
  // 이전 입력으로 늦게 도착한 결과를 현재 편집에 적용하지 않는다.
  $('clip').addEventListener('change',reset);for(const id of fieldIds)$(id).addEventListener('input',reset);
  $('restoreExport').addEventListener('change',reset);
  async function action(fn) {if(pending)return;pending=true;error('');const version=state.revision,seq=generation;try{const apply=value=>{if(version!==state.revision||seq!==generation){closeSession(value);return false;}if(research?.session_id!==value.session_id)closeSession(research);render(value);return true;};await fn(apply);}catch(e){error(e.message);}finally{pending=false;}}
  function input(){if(!state.clip||!state.bundle)throw new Error('Select a run with frame-level state.');return {example_id:state.clip.id,instruction:$('instruction').value,observations:state.bundle};}
  async function prepare(apply,save=true) {const value=await api('/api/improvement/prepare',input());if(apply({...value,analysis:null,history:[],provider_imports:0,new_model_calls:0,analysis_origin:'prepared_video_request'})){$('visionLive').disabled=!(state.metaStatus?.video_calls_enabled&&state.metaStatus?.enabled);if(save)download('muse-spark-video-request.json',value);return value;}return null;}
  $('visionPrepare').addEventListener('click',()=>action(apply=>prepare(apply)));
  $('visionSeed').addEventListener('click',()=>action(async apply=>{apply(await api('/api/improvement/seed',{...input(),contract:spec()}));}));
  $('visionLive').addEventListener('click',()=>action(async apply=>{if(!research)throw new Error('Prepare a video request first.');const value=await api('/api/improvement/analyze',{session_id:research.session_id});if(apply(value)&&value.status)renderMetaStatus(value.status);}));
  $('visionResponse').addEventListener('change',event=>action(async apply=>{try{const file=event.target.files[0];if(!file)return;if(file.size>10*1024*1024)throw new Error('Select a response no larger than 10 MB.');if(!research)throw new Error('Save a video-analysis request first.');apply(await api('/api/improvement/import',{session_id:research.session_id,response:await file.text()}));}finally{event.target.value='';}}));
  $('visionStep').addEventListener('click',()=>action(async apply=>{if(research)apply(await api('/api/improvement/round',{session_id:research.session_id}));}));
  $('visionReflect').addEventListener('click',()=>action(async()=>{if(research)download('muse-spark-video-reflection-request.json',await api('/api/improvement/reflect',{session_id:research.session_id}));}));
  $('visionExport').addEventListener('click',()=>action(async()=>{if(research)download('mollet-research-session.json',await api('/api/improvement/export',{session_id:research.session_id}));}));
  $('trainingPrepare').addEventListener('click',()=>action(async()=>{if(!research)return;const value=await api('/api/improvement/training',{session_id:research.session_id,hypothesis_id:$('trainingHypothesis').value,change_id:$('trainingChange').value||null,recovery_mode:$('trainingMode').value});download('mollet-training-proposal.json',value);$('recoveryStatus').textContent='Saved the correction and retraining proposal. It still requires a matching case and the existing pending-approval process; no training was run.';}));
  $('recoveryResponse').addEventListener('change',event=>action(async apply=>{try{const file=event.target.files[0];if(!file)return;if(!research)throw new Error('Import analysis hypotheses first.');if(file.size>10*1024*1024)throw new Error('Select an evaluation no larger than 10 MB.');const comparison=await file.text();const value=await api('/api/improvement/recovery',{session_id:research.session_id,hypothesis_id:$('trainingHypothesis').value,comparison});if(!apply(value))return;const r=value.recovery_comparison.result;const names={recovered:'Recovery criterion met',not_recovered:'Recovery criterion not met',undetermined:'Undetermined',unknown:'Undetermined',unnecessary_intervention:'Unnecessary intervention',regression:'Regression'};$('recoveryResult').hidden=false;$('recoveryResult').textContent=`${names[r.outcome||r.status]||r.outcome||r.status} · ${value.recovery_comparison.intervention.correction_id}\nRecorded-evaluation comparison · no new training or causal-effect claim`;$('recoveryStatus').textContent='Compared with the existing recovery evaluator and attached the result to the next reflection input. Detailed evidence remains in the analysis history.';}finally{event.target.value='';}}));
})();
