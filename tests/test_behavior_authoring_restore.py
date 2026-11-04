"""저장한 조건·근거를 원본 기록에 안전하게 다시 결합하는 UI 회귀 검사."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rl_triage.behavior_authoring.datasets import ROOT, load_example_view
from rl_triage.behavior_authoring.evaluate import evaluate_contract
from rl_triage.behavior_authoring.schema import validate_contract, validate_observations


APP = ROOT / "src/rl_triage/behavior_authoring/web/app.js"
INDEX = ROOT / "src/rl_triage/behavior_authoring/web/index.html"
SAVED = ROOT / "evals/results/meta_live_provider_20261104_v1/mollet-behavior-contract.json"


def test_restore_control_is_present():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="restoreExport"' in html
    assert "Import saved contract and evidence" in html


def test_actual_export_fits_closed_schema_after_authority_is_downgraded():
    saved = json.loads(SAVED.read_text(encoding="utf-8"))
    canonical = load_example_view("h1-up")["observations"]
    observations = {
        **canonical,
        "provenance": {
            **canonical["provenance"],
            "restored_export": {
                "provider_authentication": "unverified_saved_export",
                "model_called": False,
            },
        },
        "samples": [
            {**original, "tracks": imported["tracks"]}
            for original, imported in zip(canonical["samples"], saved["observations"]["samples"], strict=True)
        ],
    }
    contract = {
        **saved["contract"],
        "source": {
            "kind": "imported_spark_response",
            "response_sha256": saved["contract"]["source"]["response_sha256"],
            "provider_authentication": "unverified_saved_response",
            "model_called": False,
        },
    }
    normalized_observations = validate_observations(observations)
    normalized_contract = validate_contract(contract)
    result = evaluate_contract(normalized_contract, normalized_observations, approval=None)
    assert result["stage"] == "draft"
    assert normalized_observations["provenance"]["capture_approved"] is False
    assert normalized_observations["provenance"]["model_ready"] is False
    assert normalized_contract["source"]["model_called"] is False


def test_actual_export_restores_only_verified_evidence_and_resets_authority(tmp_path: Path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js가 없어 브라우저 JavaScript 회귀 검사를 실행할 수 없습니다.")

    canonical_path = tmp_path / "canonical.json"
    canonical_path.write_text(json.dumps(load_example_view("h1-up"), ensure_ascii=False), encoding="utf-8")
    harness_path = tmp_path / "restore-harness.cjs"
    harness_path.write_text(
        r"""
const assert=require('assert');
const fs=require('fs');
const vm=require('vm');
const [appPath,indexPath,savedPath,canonicalPath]=process.argv.slice(2);
const canonical=JSON.parse(fs.readFileSync(canonicalPath,'utf8'));
const saved=JSON.parse(fs.readFileSync(savedPath,'utf8'));
const listeners={};
class Element {
  constructor(id){this.id=id;this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.checked=false;this.required=false;this.currentTime=0;this.width=960;this.height=190;this.dataset={};this.files=[];this.children=[];this.attributes={};this.classList={toggle(){},add(){},remove(){}};}
  addEventListener(name,fn){(listeners[this.id]??={})[name]=fn;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name]??(name==='src'?this.src:null);}
  removeAttribute(name){delete this.attributes[name];if(name==='src')delete this.src;}
  replaceChildren(...children){this.children=children;}
  closest(){return this;}
  load(){}
  focus(){}
  click(){}
  getBoundingClientRect(){return {left:0,top:0,width:this.width,height:this.height};}
  setPointerCapture(){}
  getContext(){return {clearRect(){},fillRect(){},strokeRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},setLineDash(){},fillStyle:'',strokeStyle:'',lineWidth:1,font:'',textAlign:''};}
}
const elements={};
for(const match of fs.readFileSync(indexPath,'utf8').matchAll(/id="([^"]+)"/g))elements[match[1]]=new Element(match[1]);
const resultElement=new Element('result');
const document={getElementById:id=>elements[id],querySelector:selector=>selector==='.result'?resultElement:null,createElement:tag=>new Element(tag)};
const calls=[];
let evaluatedBody=null;
const metaStatus={enabled:false,key_configured:false,reserved_usd:0,available_budget_usd:0,live_requests_started:2,blockers:[]};
function response(value,ok=true){return {ok,status:ok?200:400,json:async()=>value};}
async function fetch(path,options={}){
  calls.push(path);
  if(path==='/api/examples')return response({examples:[{id:'h1-up',name:'H1',available:true}],default_example:'h1-up',default_contract:canonical.default_contract});
  if(path==='/api/meta/status')return response({status:metaStatus});
  if(path.startsWith('/api/example?id='))return response(structuredClone(canonical));
  if(path==='/api/validate')return response({contract:JSON.parse(options.body).contract});
  if(path==='/api/meta/run/spark'){
    const contract=structuredClone(canonical.default_contract);contract.title='Later live Spark condition';contract.source={kind:'live_meta_spark_response',model_called:true};
    return response({status:metaStatus,receipt:null,contract});
  }
  if(path==='/api/evaluate'){
    evaluatedBody=JSON.parse(options.body);
    assert.strictEqual(evaluatedBody.approval,null);
    assert.strictEqual(evaluatedBody.observations.provenance.capture_approved,false);
    assert.strictEqual(evaluatedBody.observations.provenance.model_ready,false);
    return response({result:{stage:'draft',approval_status:'missing',verdict:'UNKNOWN',reason:'Recalculated',trigger_t_s:null,interval:null,examined_samples:625,warnings:[]}});
  }
  throw new Error('Unexpected API call: '+path);
}
const context={console,document,fetch,URL:{createObjectURL(){return 'blob:test';},revokeObjectURL(){}},Blob,setTimeout,clearTimeout,structuredClone};
context.globalThis=context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(appPath,'utf8')+'\nglobalThis.__state=state;globalThis.__spec=spec;globalThis.__restoreExport=restoreExport;globalThis.__runLive=runLive;',context,{filename:appPath});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function waitFor(predicate){for(let i=0;i<100;i++){if(predicate())return;await tick();}throw new Error('Timed out waiting for the initial UI load');}
const file=value=>({size:Buffer.byteLength(JSON.stringify(value)),text:async()=>JSON.stringify(value)});
async function rejects(value,pattern){let caught=null;try{await context.__restoreExport(file(value));}catch(error){caught=error;}assert(caught,pattern);assert.match(caught.message,pattern);}
(async()=>{
  await waitFor(()=>context.__state.clip?.id==='h1-up');
  context.__state.approval={actor:'forged'};context.__state.result={stage:'reviewed'};elements.reviewed.checked=true;
  await context.__restoreExport(file(saved));
  const trackedIndex=saved.observations.samples.findIndex(sample=>sample.tracks.length);
  assert(trackedIndex>=0);assert.strictEqual(JSON.stringify(context.__state.bundle.samples[trackedIndex].tracks),JSON.stringify(saved.observations.samples[trackedIndex].tracks));
  assert.strictEqual(context.__state.bundle.provenance.capture_approved,canonical.observations.provenance.capture_approved);
  assert.strictEqual(context.__state.bundle.provenance.model_ready,canonical.observations.provenance.model_ready);
  assert.strictEqual(context.__state.bundle.provenance.restored_export.provider_authentication,'unverified_saved_export');
  assert.strictEqual(context.__state.source.provider_authentication,'unverified_saved_response');
  assert.strictEqual(context.__state.source.model_called,false);
  assert.strictEqual(context.__state.approval,null);
  assert.strictEqual(elements.reviewed.checked,false);
  assert.strictEqual(context.__state.result.stage,'draft');
  assert.strictEqual(evaluatedBody.approval,null);
  assert.strictEqual(elements.title.value,'Maintain H1 posture while ascending stairs');
  assert.strictEqual(context.__spec().title,saved.contract.title);
  assert.strictEqual(context.__spec().instruction,saved.contract.instruction);
  assert.match(elements.importStatus.textContent,/approval, and the prior verdict were not imported/);

  const forgedApproval=structuredClone(saved);forgedApproval.approval={actor:'forged',scope:'administrator'};forgedApproval.result={stage:'reviewed',verdict:'PASS'};
  context.__state.approval={actor:'old'};elements.reviewed.checked=true;
  await context.__restoreExport(file(forgedApproval));
  assert.strictEqual(context.__state.approval,null);assert.strictEqual(elements.reviewed.checked,false);assert.strictEqual(context.__state.result.stage,'draft');

  const changedSha=structuredClone(saved);changedSha.clip.video_sha256='0'.repeat(64);
  await rejects(changedSha,/video SHA-256/);
  const changedState=structuredClone(saved);changedState.observations.samples[0].values.base_height_m+=0.01;
  await rejects(changedState,/Source frame, time, or state values differ/);
  await assert.rejects(()=>context.__restoreExport({size:10*1024*1024+1,text:async()=>''}),/no larger than 10 MB/);

  let releaseText;const delayed={size:100,text:()=>new Promise(resolve=>{releaseText=resolve;})};
  const pending=context.__restoreExport(delayed);elements.title.value='User-edited condition';listeners.title.input();const editedResult=context.__state.result;
  releaseText(JSON.stringify(saved));await pending;
  assert.strictEqual(elements.title.value,'User-edited condition');assert.strictEqual(context.__state.result,editedResult);assert.strictEqual(editedResult,null);

  let releaseLiveRace;const liveRaceFile={size:100,text:()=>new Promise(resolve=>{releaseLiveRace=resolve;})};
  const staleRestore=context.__restoreExport(liveRaceFile);const live=context.__runLive('spark');await live;releaseLiveRace(JSON.stringify(saved));await staleRestore;
  assert.strictEqual(elements.title.value,'Later live Spark condition');
  assert(calls.every(path=>path.startsWith('/api/example')||path==='/api/examples'||path==='/api/meta/status'||path==='/api/validate'||path==='/api/evaluate'||path==='/api/meta/run/spark'));
})().catch(error=>{console.error(error);process.exitCode=1;});
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node, str(harness_path), str(APP), str(INDEX), str(SAVED), str(canonical_path)],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
