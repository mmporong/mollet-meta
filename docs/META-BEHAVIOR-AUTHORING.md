# Mollet 행동 조건 편집기

## 작업 계약

RL 개발자가 저장 영상과 상태 기록을 보면서 원하는 행동 조건을 작성하고, 조건을 검토한 뒤 위반 구간을 찾는다. 기존 진단·교정·26조건 회복 평가는 바꾸지 않는다.

- 기준: `94fe8740277c9ecc556096fa4d00df0ad0bb7749`에서 분기한 `feat/meta-behavior-authoring` worktree.
- 구현: 제한된 조건 스키마, 시간 구간 판정, SAM/Spark 요청 생성·저장 응답 가져오기·설정 후 HTTPS 호출, 로컬 영상 편집 화면, 결과 내보내기.
- 완료: 실제 저장 RL 수치에서 예제 조건의 충족·위반을 계산하고, 결측·추적 단절·관측창 부족을 UNKNOWN으로 보존한다. 웹과 CLI의 같은 조건은 같은 결과를 낸다.
- 제외: 미확인 과금 호출·새 GPU 작업·정책 수정·재학습·원본 main 편집·push·신청·제출.
- Meta 상태: 실제 HTTPS 호출에서 Spark 조건 생성과 SAM 객체 위치 가져오기를 각각 한 번 확인했다. H1 자세 조건은 0~0.5초 PASS이며 저장 JSON의 재판정도 일치한다. [실제 실행 근거](../evals/results/meta_live_provider_20261104_v1/verification.json)를 보존했다. 화면 조작 검증은 별도 상태다.
- 영상 상태: 기본 화면은 H1 이족 계단 상·하행과 Go2 사족 계단 상·하행의 명령 속도 비교, 총 6개 원본을 사용한다. H1 2개와 Go2 0.25 m/s 2개는 프레임별 상태를 연결한다. Go2 0.75 m/s 2개는 공개 요약만 표시한다. 모델 입력 승인은 부여하지 않는다.

## 첫 조건

관측 시작 시각 이후 정해진 시간 안에 지표가 임계값을 만족하고 일정 시간 유지하는지 검사한다. H1 기본 조건은 영상 시작부터 8초간 수직축 정렬 0.8 이상 유지다. 지표는 세계 좌표 수평 속도, 명령 수평 속도, 몸체 기준점의 세계 z, 수직축 정렬이다. 세계 z는 지면 대비 높이가 아니다. 영상 영역 진입을 트리거로 쓸 때에는 가져온 객체 bbox 중심을 사용하며, pixel mask 면적·세계 좌표·접촉력 판정으로 설명하지 않는다.

H1 영상은 625프레임이고 상태 기록은 초기 행을 포함한 626행이다. 녹화기의 step 이후 캡처 순서에 따라 영상 프레임 0(PTS 0초)을 상태 행 1(sim 0.02초)에 연결한다. 마지막 영상 시각은 12.48초다. 자세 quaternion은 정규화한 뒤 세계 z 방향의 몸체 수직축을 계산한다.

Go2는 0.25/0.75 m/s 명령의 계단 상·하행 4개 원본을 제공한다. 0.25 m/s 두 기록은 공개 contrast 결과의 같은 행에 묶인 영상 SHA와 상태 SHA를 검증한 뒤, 상태 SHA가 일치하는 `stairs_motion_integrity_20261101_v1`의 원문을 읽는다. 옛 감사 manifest의 영상 SHA를 현재 영상의 근거로 쓰지 않는다. 상태 수치에서 다시 계산한 x/z 변화와 최저 자세도 공개 요약과 대조한다.

Go2 기본 자세 조건은 영상의 상태 구간 전체인 12.48초 동안 수직축 정렬 0.8 이상 유지다. 상행은 자세 PASS지만 진행·높이 원판정은 불수용이고, 하행은 말미 뒤집힘으로 자세 FAIL이다. 세계 좌표 z가 음수인 상행 구간은 값을 보존하며 지면 대비 높이로 해석하지 않는다. 0.75 m/s 두 기록은 공개 상태 원문을 찾지 못해 그래프와 시간 조건 판정을 만들지 않는다. 네 기록은 동일 정책의 명령 비교이며 새 학습·교정·회복 결과가 아니다.

처음부터 영역 안에 있거나 가림·누락 뒤 갑자기 안에 나타나면 진입 시각을 추측하지 않는다. 관측창 부족, 결측, 큰 시간 간격도 UNKNOWN이다. 조건을 만족한 충분한 연속 구간은 PASS, 전체 판정 창을 관측했지만 만족하지 못한 경우만 FAIL이다.

조건 검토는 로컬 사용자의 의미 확인 기록이다. 사용자 인증이나 실험 실행 권한을 만들지 않는다. 개발 예제의 자동 검토 기록은 `automated_test`로 구분한다. 모델이 승인·실행 필드를 작성할 수 없다.

## 실행

Python 3.11~3.13에서 새 패키지 설치 없이 실행한다. 저장소 checkout에 포함된 영상·기록을 사용한다.

```powershell
cd "$HOME/rl-triage-meta"
$env:PYTHONPATH = "$PWD/src"
python -m rl_triage.behavior_authoring serve --port 8784
```

브라우저에서 `http://127.0.0.1:8784`를 연다. 서버는 loopback에만 바인딩하며 종료는 실행 터미널의 Ctrl+C다.

기존 실제 응답을 추가 호출 없이 확인하려면 상단의 `저장한 조건·근거 불러오기`에서 `evals/results/meta_live_provider_20261104_v1/mollet-behavior-contract.json`을 선택한다. 원본 H1 영상과 상태 값이 일치하는지 대조한 뒤 조건과 SAM bbox를 복구하고 미검토 판정을 새로 계산한다. 파일에 저장된 승인이나 공급자 인증은 가져오지 않는다. 조건 의미를 확인한 뒤 체크하고 검사하면 새 로컬 검토 기록을 남길 수 있다. 현재 원본 미리보기 서버는 `http://127.0.0.1:7705`에서 같은 기능을 제공한다.

CLI만 확인할 경우 새 결과 폴더를 지정한다. 예제의 자동 의미 확인은 사람의 검토가 아닌 `automated_test`로 남는다.

```powershell
python -m rl_triage.behavior_authoring demo --out "$HOME/meta-behavior-demo"
python -m pytest -q -s tests/test_behavior_authoring_engine.py tests/test_behavior_authoring_adapters.py tests/test_behavior_authoring_app.py
```

이미 존재하는 출력 파일·폴더는 덮어쓰지 않는다. 예제 자료는 이미 본 개발 기록이므로 여기서 나온 충족·위반은 정확도·일반화·모델 효과의 실측으로 사용하지 않는다.

## 외부 모델 경로

`muse-spark-1.3` 요청은 Responses API의 `text.format` JSON schema와 출력 토큰 상한을 사용한다. `sam-3.1` 요청은 선택한 원본 영상의 data URL과 객체 개념을 입력으로 구성한다. 실제 송신 주소는 `https://api.meta.ai/v1/responses`로 고정한다. 저장 파일 가져오기는 공급자 인증이 미확인인 경로로 유지하고, 실제 HTTPS 응답과 구분한다.

SAM `<Nf>`는 프레임 번호이므로 원본 프레임→시각 표를 사용한다. bbox의 끝 좌표는 inclusive이므로 정규화할 때 +1을 반영한다. 압축 mask는 공식 codec 없이 임의 복호화하지 않는다. 이번 범위는 bbox 중심 영역 조건이다. mask 면적/IoU가 필요한 후속 기능은 공식 parser와 라이선스를 검토해야 한다.

## 11월 3일 마감 안내 확인

11월 3일 주최 측 알림 원문에서 다음을 확인했다.

- 제출 마감: 11월 4일 오후 9시 ET = 한국 11월 5일 오전 11시.
- 3분 데모 영상에 시간을 투자하라는 안내.
- GitHub 오픈소스가 모든 프로젝트의 필수 조건이라는 안내.
- 제출 양식에 질문이 많으므로 일찍 작성·검토하라는 안내.

공식 공개 행사 페이지: <https://dev.meta.ai/events/global-hackathon>

기반 저장소 <https://github.com/mmporong/rl-triage-agent>는 공개이며 Apache-2.0 라이선스가 있다. 이 기능은 로컬 브랜치에만 있으므로 새 기능까지 GitHub에 공개됐다고 표시하지 않는다. 11월 4일 주최 메일로 확인한 제출 주소는 <https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit>이다. 공개 웹 조회에서는 행사 소개만 반환하므로 로그인 폼의 문항 수·필수 필드·업로드 성공을 추정하지 않는다. 실제 전송은 수행하지 않는다.

## 3분 데모 동선 초안

1. 0:00–0:25: 기존 RL 실패 영상과 “보상만으로 원하는 행동을 정의하기 어렵다”는 문제.
2. 0:25–1:05: 행동 조건의 지표·시작 시각·유예 시간·유지 시간을 작성하고 의미 확인.
3. 1:05–1:45: H1 계단의 조건·근거 구간을 확인하고, Go2의 명령별 수용·불수용 원기록을 비교. Go2 요약을 새 시간 판정으로 표현하지 않음.
4. 1:45–2:20: Spark 요청·저장 응답 가져오기, SAM 객체 영역 결합의 역할 설명. 실제 호출·응답이 없으면 준비된 인터페이스라고 표시.
5. 2:20–2:45: 관측 부족이나 영역 추적 부재에서 UNKNOWN을 보이는 장면.
6. 2:45–3:00: 내보낸 평가식·근거·결과와 공개 코드 경로.

실제 모델 결과가 없는 상태에서 API 시연을 촬영했다고 표현하지 않는다. 위 내용은 동선 초안이며 최종 영상 파일이 아니다.

## 이번 로컬 검증 결과

구현 커밋 `014cfcc44712c5ba9f485500770242958089d1e6`을 고정한 뒤 공개 보관 상태 기록 두 개에서 같은 예제 조건을 계산했다. 기준 기록은 PASS, 결함 기록은 FAIL이다. 개발용 자동 의미 확인 기록이며 사람의 검토나 새 보류 세트의 정확도 측정이 아니다. 원본 영상의 승인 보류는 그대로 남았다.

[저장 결과](../evals/results/meta_behavior_authoring_dev_20261103_v1/summary.json)와 [검증 범위](../evals/results/meta_behavior_authoring_dev_20261103_v1/validation.json)를 확인할 수 있다. 관련 테스트 44개, Python 컴파일, JavaScript 구문 검사가 통과했다. 독립 코드 검토의 열린 지적은 없으며 판정은 COMMENT다. 환경에 LSP 도구와 브라우저 호스트가 없어 LSP 진단·실제 렌더링·브라우저 조작 검증은 수행하지 못했다.

## 계단 예제 교체 검증

이전 UI는 렌더링 보류 자료를 기본 예제로 선택해 화살표만 이동하는 듯한 재생을 노출했다. 해당 자료는 기존 CLI 회귀용으로 보존하고 화면 목록에서 제외했다. [H1 원본](media/stairs_h1_s172_20261030_v1/source_manifest.json)과 [Go2 원기록](../evals/results/go2_stairs_command_contrast_s200_20261101_v1/cases.json)의 영상 해시를 대조한다.

관련 테스트 52개가 통과했다. H1 초기 행 제외·625프레임 결합, Go2 상태 시계열 미생성, 수정 영상 해시 거부, 기본 선택·6개 영상의 HTTP Range 원본 바이트 일치를 확인했다. 영상 6개 모두 CPU에서 끝까지 디코딩됐고 1280×720·50fps·625프레임·12.5초다. H1 4/8초 프레임과 Go2 하행 말미의 전도 프레임을 열어 확인했다. JavaScript 구문 검사가 통과했으며 브라우저 화면 조작 검증은 호스트 연결 부재로 수행하지 못했다.

미리보기는 `http://127.0.0.1:7705`에서 제공한다. 프로세스 PID·종료 명령은 저장소 밖 `$HOME/hackathon-specialist/tmp/meta-authoring-preview-20261103.json`에 기록한다.

## Go2 상태 연결과 3분 영상

Go2 0.25 m/s 두 기록의 상태 SHA 연결, 0.75 m/s 두 기록의 요약 전용 경계, 변조된 상태 원문 거부를 포함해 관련 테스트 53개가 통과했다. Python 컴파일·JavaScript 구문·diff 검사를 수행했다. 브라우저 접근 호스트와 설치된 Windows Computer Use의 native pipe는 모두 연결되지 않았다. 따라서 화면 선택·재생·다운로드 조작 검증은 미완료이며 HTTP 수준의 확인과 구분한다.

[실행 중 서버의 HTTP 검증](../evals/results/meta_behavior_stairs_flow_20261103_v1/verification.json)은 영상 6개의 Range 바이트 일치, 상태가 있는 4개의 조건 판정, 예제 6개의 내보내기 JSON 재검증을 통과했다. 내보낸 파일은 같은 결과 폴더에 원본 해시와 함께 보존한다. 자동 검사는 `automated_test`이며 사람의 조건 승인으로 표시하지 않는다.

[3분 데모 영상](../submission/meta-behavior-20261103-v1/mollet-meta-behavior-demo-3min.mp4), [한국어 자막](../submission/meta-behavior-20261103-v1/mollet-meta-behavior-demo-ko.srt), [장면 대본](../submission/meta-behavior-20261103-v1/SCRIPT-ko.md), [원본·판정·영상 검증 manifest](../submission/meta-behavior-20261103-v1/manifest.json)를 생성했다. 저장 계단 원본과 계산 결과를 편집한 시연이며 브라우저 촬영이 아니다. 길이는 180.000초, 1280×720 H.264 영상과 오프라인 한국어 음성이다. 영상의 15초 재생 장면은 12.5초 원본 뒤 마지막 프레임을 2.5초 유지한다. 전체 디코딩과 대표 프레임 6장을 확인했다.

H1 영상 하나에 대한 Spark 조건 생성과 SAM 추적의 [기존 요청 계획](../evals/results/meta_tools_prepared_20261103_v1/plan.json)은 미전송 기록으로 보존한다. [Meta 공식 가격 안내](https://dev.meta.ai/docs/pricing-rate-limits)는 종량제다. 모델 응답이나 조건 검토가 영상 입력 승인·실험 실행 권한을 만들지 않는다.

## 실제 Meta API 사용 설정

10월 26일 주최자 이메일과 그 메일이 연결한 [크레딧 신청 폼](https://docs.google.com/forms/d/e/1FAIpQLScam78z7tHsNYibiFyJQVFzEgr0coyBTiY568SxxNbsPa3jiw/viewform)에서 승인 참가자의 US$150 지원을 확인했다. 신청 폼은 Team ID를 요구하고 영업일 2일 안의 지급을 안내한다. 지원 안내와 현재 계정의 잔액·결제수단·기존 키 확인은 별개다. Billing의 [Spend Limit](https://dev.meta.ai/help/billing/spend-limit-alert)은 알림이며 과금 차단 기능으로 간주하지 않는다.

1. 로컬 화면의 **Meta Model API 연결**에서 기존 키를 입력한다. `MODEL_API_KEY` 환경변수 또는 저장소 밖의 기존 키 파일도 지원한다. 입력한 키는 서버 메모리에만 두며 응답·로그·결과 파일에 저장하지 않는다. 서버를 재시작하면 화면에서 입력한 키와 크레딧 확인은 초기화된다.
2. Meta 콘솔의 해당 Team Billing에서 지급 잔액과 결제수단을 확인하고, 앱에 확인한 잔액과 US$1 이하의 예산을 입력한다. 확인 체크는 자동 선택하지 않는다.
3. **실제 Spark 요청**은 행동 조건 초안을 폼에 넣고 의미 확인을 초기화한다. **실제 SAM 요청**은 원본 영상에서 얻은 bbox를 상태 시각에 붙인다. 두 버튼은 키·크레딧·예산·상태 기록이 준비된 경우에만 켜진다.
4. 요청·원문 응답·결합 결과·해시는 저장소 밖 `%LOCALAPPDATA%/Mollet/meta-model-api/runs/<run_id>/`에 보존한다. SQLite 예약 원장은 재시작과 동시 요청에도 유지되며 네트워크 실패의 예약은 자동 환급하지 않는다. 자동 재시도는 없다.

앱은 [가격 설정](../src/rl_triage/behavior_authoring/meta-pricing.json)의 표준 단가와 보수 입력 한도로 예약액을 계산한다. 예약액은 실제 청구액 측정값이 아니다. Contributor 모델·임의 API 주소·별도 파일 업로드·다른 로컬 파일 전송은 지원하지 않는다. SAM의 0개 탐지 응답은 완료된 빈 결과로 처리하고, 오류·미완료 응답은 결과로 적용하지 않는다.

기존 키 파일을 사용할 때는 다음과 같이 실행한다. 키 파일에는 비밀 키 한 개 또는 `MODEL_API_KEY=...` 한 줄을 두며 저장소에 넣지 않는다. 아래 명령은 설정 화면을 열고 실제 요청을 자동 실행하지 않는다.

```powershell
cd "$HOME/rl-triage-meta"
$env:PYTHONPATH = "$PWD/src"
$env:PYTHONUTF8 = "1"
python -m rl_triage.behavior_authoring serve --port 7705 --meta-key-file "$HOME/.config/mollet/meta-key.txt"
```

`python -m rl_triage.behavior_authoring meta-status`는 키 값을 출력하지 않고 로컬 설정을 확인한다. `call --help`에는 CLI 단일 요청 옵션이 있다. 새 키 발급·카드 등록·크레딧 신청·실제 계정 잔액 조회는 이 앱에서 자동화하지 않는다. 기존 3분 편집 영상은 11월 3일 미호출 상태의 기록으로 보존하며, 새 API 설정 화면의 촬영본으로 표시하지 않는다.

실제 전송 계층·서비스·웹 결합을 포함한 관련 테스트 80개가 통과했다. 검사에는 동시 요청·재시작의 예약 누적, HTTP 오류의 무재시도, 키 교체 중 비밀값 가림, SAM 완료·빈 탐지·스트림 오류 처리가 포함된다. 이 결과는 모의 전송 검증이며 실제 Meta 응답 품질을 뜻하지 않는다. 현재 로컬 설정은 키 없음·크레딧 미확인·실제 요청 0회다.

[API 연결 CPU 검증 기록](../evals/results/meta_api_integration_cpu_20261104_v1/verification.json)은 구현 커밋, 관련 80개 검사, 자격증명 검토 지적의 해소, 실행 서버의 설정 화면·호출 잠금·비용 예약 견적을 보존한다. 실제 공급자 요청은 실행하지 않았고 브라우저 조작 검증은 연결 문제로 미실행이다.
