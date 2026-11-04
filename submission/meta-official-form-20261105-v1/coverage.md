# Mollet 공식 폼 답변 범위표

| 요구사항 | 포함 필드 | 증거 수준 | 답변에 반영한 범위 |
|---|---|---|---|
| 문제와 사용자 | Project Description | 제품 동작 | 로봇 개발자가 다음 학습이나 회복 시도 전에 영상과 텔레메트리로 행동 조건을 반복 검사하는 흐름 |
| 6개 계단 클립 | Project Description, Why Meta | 기존 기록 | Isaac Lab 시뮬레이션의 H1 이족·Go2 사족 6개 중 시계열 4개, 요약 전용 2개로 구분 |
| 조건 작성·수정·검토 | Project Description, Meta products | 구현 및 브라우저 검증 | 조건을 고치면 이전 승인을 초기화하고, JSON 재불러오기에서도 값 보존과 재검토 요구 유지 |
| 시간·영역 진입 trigger | Project Description, Meta products | 코드 구현 | 시간 조건과 SAM bbox 중심의 경계 통과 조건 구현. 추적 단절·관측 공백·짧은 영상은 UNKNOWN. 실제 Meta H1 검증은 시간 조건만 사용 |
| Spark 실제 호출 | Meta products, Why Meta | 실제 API 검증 | 자연어와 상태 JSON 입력 1회. 원본 영상 분석으로 서술하지 않음 |
| SAM 실제 호출 | Meta products, Why Meta | 실제 API 검증 | 원본 H1 영상 1회, 상태 625개 중 박스 624개. 정확도가 아니라 응답 범위 |
| H1·Go2 판정 | 세 필드 | 실제 기록의 결정론적 검사 | H1 0.5초 조건 PASS, 사용자 8초 조건 PASS, 수동 Go2 12.48초 조건 FAIL |
| 재현 가능한 근거 | Project Description, Meta products | 실제 브라우저·파일 검증 | 원본 값, 해시, 증거 등급, 승인 초기화, 저장 응답 재현을 분리해 기록 |
| 영상·상태 확장 단계 | Meta products, Why Meta | 코드 구현·합성 응답 검사 | 등록 원본 MP4와 동기 상태에서 원본에 존재하는 프레임을 인용한 가설 1~4개 생성. 미검사 조건을 최대 3개 골라 같은 저장 관측에 각각 검사한 뒤 verdict·interval·trigger·해시·불확실성을 reflection 요청에 포함 |
| 교정 제안 연결 | Meta products, Why Meta | 코드 구현·미실행 | Meta reflection은 confirmed probe를 만들지 않음. 기존 triage loop에서 별도로 확인된 probe의 case와 registered correction이 일치할 때만 ledger에 `pending_human_approval`로 기록. 실행 권한·GPU·학습 없음 |
| 확장 실행 결과 | Meta products, Why Meta | 미실행 | 새 영상 추론, 실제 reflection, 보상 변경, 재학습, GPU 작업, 회복 성능 측정은 0 |
| 반복 검사의 해석 | Meta products, Why Meta | 범위 제한 | 선택된 조건 최대 3개를 같은 저장 관측에 검사하는 proxy이며 새 기록 3개, 새 실험, 인과 확인, 성능 향상 근거가 아님 |
| 회복 판정 gate | Meta products, Why Meta | 코드 구현·미실행 | 별도 실행의 기존 fixed recovery oracle과 triplet 비교가 회복 주장만 판정. 실행 승인을 부여하지 않음 |
| 자동·브라우저 검사 | Meta products | 실제 로컬 검증 | CPU 테스트 142개, Chrome 검사 10개 |
| 기존 자산과 대회 기여 | Project Description, limitations | 범위 구분 | RL 진단 기반과 계단 기록은 선행 자산. Meta 조건 작성부터 검토·재현·확장 흐름까지를 대회 기여로 구분 |
| 공개 자료 | Project Description | 공개 저장소 | `https://github.com/mmporong/mollet-meta` 한 번만 표기. 180초 영상은 본문에 긴 URL을 반복하지 않음 |

## 제외한 주장

- 모델 우월성, SAM 추적 정확도, 일반화 정확도를 주장하지 않았다.
- 시간 절감, 비용 절감, 구매 효과, 성능 향상 수치를 만들지 않았다.
- 모델 출력에 판정 권한, 승인 권한, 보상 변경 권한을 부여하지 않았다.
- 개인정보, API 키, 사용자 설문값을 넣지 않았다.

모든 로봇 영상·상태는 기록된 시뮬레이션 근거로 설명한다. 실물 주행 검증으로 확대하지 않는다.
