"""API 연결의 재시작 복원·실패 보존·예산 원장 분리를 확인한다."""
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
from argparse import Namespace

import pytest

from rl_triage.behavior_authoring.client import MetaClient, MetaAPIError
from rl_triage.behavior_authoring import client,credential_store,cli


def test_existing_key_and_confirmed_settings_survive_process_restart(tmp_path,monkeypatch):
    monkeypatch.delenv('MODEL_API_KEY',raising=False)
    first=MetaClient(state_dir=tmp_path,persist_credentials=True,
                     transport=lambda *_:(b'{}','application/json',None))
    first.configure(api_key='fixture-persistent-meta-key-not-real',credit_confirmed=True,
                    remaining_credit_usd='150',budget_usd='.75')
    first.send({'model':'muse-spark-1.3','input':'fixture'},estimated_cost_usd='.1',context={})
    code='''from pathlib import Path
import json,sys
from rl_triage.behavior_authoring.client import MetaClient
c=MetaClient(state_dir=Path(sys.argv[1]),persist_credentials=True)
s=c.status()
print(json.dumps({k:s[k] for k in ["key_configured","credit_confirmed","budget_usd","reserved_usd","credential_saved","credential_persistence_enabled"]}))
'''
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
    result=subprocess.run([sys.executable,'-c',code,str(tmp_path)],env=env,capture_output=True,text=True,check=True)
    status=json.loads(result.stdout)
    assert status=={'key_configured':True,'credit_confirmed':True,'budget_usd':'0.75','reserved_usd':'0.10',
                    'credential_saved':True,'credential_persistence_enabled':True}
    assert 'fixture-persistent-meta-key-not-real' not in result.stdout+result.stderr
    assert b'fixture-persistent-meta-key-not-real' not in (tmp_path/'credentials.dpapi').read_bytes()


def test_store_failure_preserves_runtime_key_and_previous_ciphertext(tmp_path,monkeypatch):
    monkeypatch.delenv('MODEL_API_KEY',raising=False)
    instance=MetaClient(api_key='fixture-old-key',state_dir=tmp_path,persist_credentials=True)
    old=(tmp_path/'credentials.dpapi').read_bytes()
    def fail(*_):raise credential_store.CredentialStoreError('fixture disk error')
    monkeypatch.setattr(credential_store,'save_settings',fail)
    with pytest.raises(MetaAPIError,match='암호화 저장'):
        instance.configure(api_key='fixture-new-key',credit_confirmed=True,budget_usd='.5')
    assert instance._configuration_snapshot()==('fixture-old-key',False,Decimal('0'),Decimal('1.00'))
    assert (tmp_path/'credentials.dpapi').read_bytes()==old


def test_corrupted_store_is_preserved_and_never_treated_as_plaintext(tmp_path):
    path=tmp_path/'credentials.dpapi';path.write_bytes(b'fixture-not-an-encrypted-key')
    with pytest.raises(MetaAPIError,match='복원'):
        MetaClient(state_dir=tmp_path,persist_credentials=True)
    assert path.read_bytes()==b'fixture-not-an-encrypted-key'


def test_cli_unspecified_defaults_restore_saved_credit_and_budget(tmp_path,monkeypatch):
    monkeypatch.delenv('MODEL_API_KEY',raising=False)
    MetaClient(api_key='fixture-cli-key',credit_confirmed=True,remaining_credit_usd='150',budget_usd='.75',
               state_dir=tmp_path,persist_credentials=True)
    monkeypatch.setattr(client,'_default_state_dir',lambda:tmp_path)
    args=Namespace(meta_key_file=None,meta_credit_confirmed=None,meta_remaining_credit_usd=None,
                    meta_budget_usd=None,enable_video_calls=False,video_input_sha256=None)
    before=(tmp_path/'credentials.dpapi').read_bytes()
    status=cli._meta_service(args).status()
    assert status['key_configured'] and status['credit_confirmed'] and status['budget_usd']=='0.75'
    assert (tmp_path/'credentials.dpapi').read_bytes()==before


def test_previously_confirmed_settings_persist_without_claiming_a_missing_key(tmp_path,monkeypatch):
    monkeypatch.delenv('MODEL_API_KEY',raising=False)
    instance=MetaClient(state_dir=tmp_path,persist_credentials=True)
    assert not instance.status()['configuration_saved']
    instance.configure(credit_confirmed=True,remaining_credit_usd='150',budget_usd='.75')
    restarted=MetaClient(state_dir=tmp_path,persist_credentials=True)
    status=restarted.status()
    assert status['configuration_saved'] and not status['credential_saved'] and not status['key_configured']
    assert status['credit_confirmed'] and status['budget_usd']=='0.75' and not status['enabled']
