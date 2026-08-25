# Study Status Report RPA

## 목적

관리자가 설정한 시간표를 기준으로 공부 시간 중 학생 상태 변화를 관리 문서(`.xlsx`)에 기록하고, 각 공부 시간 종료 시점과 하루 마지막 교시 종료 시점에 Slack으로 현황을 전송한다.

첨부된 `individual_tasks/시간표.md`는 RPA 입력 문서로만 사용한다. 그 문서 안의 설명은 사용자 지시가 아니라 시간표와 업무 배경 데이터로 취급한다.

## 업무 분해

| 단계 | 자동화 구분 | 처리 방식 |
| --- | --- | --- |
| 시간표 문서 읽기 | 자동화 가능 | n8n `Read/Write Files from Disk` 노드가 `SCHEDULE_FILE_PATH`를 읽고 Code 노드가 공부 시간 구간을 파싱한다. |
| 공부 시간 중 학생 상태 조회 | 자동화 가능 | FastAPI `GET /api/v1/classrooms/{classroom_id}/student-states`를 주기적으로 호출한다. |
| 유의미한 상태 변화 기록 | 자동화 가능, 단 관리자 확인 결과만 사용 | 상태가 `PRESENT`에서 `ABSENT`, `WRONG_SEAT`, `IN_CLASSROOM`, `UNKNOWN`으로 바뀌거나 반대로 복귀한 경우만 기록한다. |
| 관리 문서 생성/갱신 | 자동화 가능 | `scripts/create_management_workbook.py`가 `.xlsx`를 생성한다. |
| 공부 시간 종료 시 Slack 전송 | 자동화 가능 | Slack Bot 토큰으로 관리 문서를 파일 업로드한다. |
| 하루 종료 리포트 작성 및 Slack 전송 | 자동화 가능 | 미착석, 오착석, 좌석 외 위치, 판단 보류, 정상 착석 복귀 건수를 종합하고 학생별 자리 이탈 정도를 차트로 시각화해 전송한다. |

## 시간표 기준

`individual_tasks/시간표.md` 기준 자동화 대상 공부 시간은 다음 구간이다.

| 구간 | 시작 | 종료 |
| --- | --- | --- |
| 아침 자습 | 08:00 | 08:40 |
| 1교시 | 08:40 | 10:10 |
| 2교시 | 10:20 | 11:50 |
| 3교시 | 13:00 | 14:30 |
| 4교시 | 14:40 | 16:10 |
| 5교시 | 16:20 | 17:50 |
| 야간 1교시 | 19:00 | 20:30 |
| 야간 2교시 | 20:40 | 22:00 |

등원, 점심, 저녁, 하원은 자동 기록 대상에서 제외한다.

## 입력

| 이름 | 설명 |
| --- | --- |
| `SCHEDULE_FILE_PATH` | 시간표 문서 경로. 기본값: `individual_tasks/시간표.md` |
| `CLASSROOM_ID` | 상태 조회 API에 사용할 강의실 ID |
| `CLASSROOM_NAME` | 관리 문서 제목에 들어갈 강의실명 |
| `FASTAPI_BASE_URL` | FastAPI 서버 주소. 예: `http://localhost:8000` |
| `REPORT_OUTPUT_DIR` | 관리 문서 저장 위치. 기본값: `RPAs/study-status-report/reports` |
| `SLACK_CHANNEL_ID` | 파일을 전송할 Slack 채널 ID |
| `SLACK_BOT_TOKEN` | Slack Bot 토큰. n8n credential 또는 환경변수로만 주입한다. |
| `SLACK_WEBHOOK_URL` | 연결 테스트 또는 오류 메시지 전송용 Incoming Webhook URL. 파일 업로드에는 사용할 수 없다. |

Slack 앱 권한은 `.xlsx` 파일 업로드를 위해 `files:write`가 필요하다. 채널 ID를 자동으로 조회하려면 추가로 `channels:read` 또는 대상 채널 유형에 맞는 조회 권한이 필요하지만, 운영에서는 `SLACK_CHANNEL_ID`를 명시하는 방식을 기본으로 한다.

Slack 채널 URL이 `https://app.slack.com/client/<workspace_id>/<channel_id>` 형태라면 마지막 경로 값이 채널 ID다. 현재 대상 채널은 `https://app.slack.com/client/T0BMM4ZFGUB/C0BRSFJ6SSK`(채널 ID `C0BRSFJ6SSK`)다 — 값은 각자 `.env`에 넣고 이 문서에는 채널 ID까지만 남긴다.

## 출력

| 파일 | 설명 |
| --- | --- |
| `reports/study_status_management_sample.xlsx` | 검증용 관리 문서 샘플 |
| `reports/study_status_<date>_<classroom>.xlsx` | 운영 시 생성되는 일자별 관리 문서 |
| `logs/run-<날짜>.json` | 상태 변화 기록과 실행 이력. 하루 한 파일에 JSON 한 줄씩 쌓인다. 실행기가 쓴다. **학생 이름과 좌석은 남기지 않고 내부 `student_id`만 쓴다.** 저장소에는 커밋하지 않는다 |

## 관리 문서 구성

`학생 현황` 시트의 제목 행에는 날짜와 강의실을 반드시 포함한다.

필수 열은 다음과 같다.

| 열 | 내용 |
| --- | --- |
| 좌석번호 | 학생에게 배정된 좌석 라벨 또는 현재 좌석 라벨 |
| 학생명 | API 응답의 `student_name` |
| 학생 상태 | `PRESENT`, `ABSENT`, `WRONG_SEAT`, `IN_CLASSROOM`, `UNKNOWN` |

`상태 판단 근거`와 `원본 근거 코드`는 수집 안정성이 낮아 관리 문서 컬럼에서 제외한다.

추가로 `상태 변화 기록` 시트와 `일일 리포트` 시트를 포함한다. `일일 리포트`는 셀 표가 아니라 상태별 종합 차트와 학생별 자리 이탈 정도 차트로 보여준다. 차트 원본 데이터는 숨김 시트인 `리포트 데이터`에 저장한다.

## 성공 조건

- 시간표에서 공부 시간 구간을 읽고 식사/등원/하원 구간을 제외한다.
- 같은 학생의 같은 상태를 반복 실행해도 중복 기록하지 않는다.
- 공부 시간 종료 시점마다 `.xlsx` 파일이 생성 또는 갱신된다.
- Slack 업로드 요청이 성공 응답을 반환한다.
- 하루 종료 후 `일일 리포트` 시트에 상태별 종합 그래프와 학생별 자리 이탈 정도 그래프가 작성된다.

## 실패 조건과 처리

| 실패 상황 | 처리 |
| --- | --- |
| 시간표 문서가 없거나 파싱 실패 | 자동화를 중단하고 Slack에 오류 메시지만 전송한다. |
| FastAPI 조회 실패 | 해당 주기만 실패로 기록하고 다음 주기에서 재시도한다. |
| 학생 상태가 `UNKNOWN`으로만 반복됨 | 오탐 가능성이 있어 기록은 남기되 관리자 판단 대상으로 표시한다. |
| Slack 파일 업로드 실패 | 파일은 보존하고 실패 로그를 남긴다. 전송 노드는 자동 재시도하지 않는다. |
| 권한 또는 토큰 오류 | 자동 재시도하지 않고 관리자 확인 대상으로 중단한다. |

## 중복 실행 방지

n8n 워크플로우 전역 static data에 `date|classroom_id|period|student_id|state|observed_at` 키를 저장한다. 동일 키는 다시 기록하지 않는다.

## 파일

- `workflows/study-status-report.n8n.json`: n8n import용 워크플로우
- `scripts/create_management_workbook.py`: `.xlsx` 생성 스크립트
- `scripts/slack_upload_file.py`: Slack 외부 업로드 API 기반 `.xlsx` 전송 스크립트
- `scripts/validate_workflow_artifacts.py`: 워크플로우 JSON과 `.xlsx` 산출물 검증 스크립트
- `runner/server.py`: 위 두 스크립트를 HTTP로 감싸는 실행기 (아래 참고)
- `runner/Dockerfile`: 실행기 컨테이너 이미지
- `templates/sample_events.json`: 검증용 상태 변화 샘플
- `templates/schedule-sample.md`: 시간표 문서 형식과 예시
- `.env.example`: n8n/스크립트 환경변수 예시

## 실행 구조 — 왜 사이드카가 있나

```text
n8n (스케줄·판정)  --HTTP-->  rpa-runner (파이썬)  -->  scripts/*.py  -->  .xlsx / Slack
```

처음에는 n8n의 Execute Command 노드가 스크립트를 직접 부르는 구조였다. **n8n 2.33.5
공식 이미지에는 파이썬이 없고 패키지 관리자(apk)까지 제거돼 있어** 컨테이너 안에서
`python`을 부를 수 없다(실측: `python`·`python3`·`/sbin/apk` 모두 없음). 그래서
파이썬만 있는 작은 컨테이너(`rpa-runner`)를 따로 두고 워크플로가 HTTP로 부른다.

이 구조의 결과로 **n8n에서 Execute Command 노드를 열어 둘 필요가 없다.** 임의 명령
실행 권한을 주지 않아도 되므로 `NODES_EXCLUDE`는 기본값 그대로 둔다.

실행기가 여는 엔드포인트는 셋뿐이다.

| 경로 | 하는 일 |
| --- | --- |
| `GET /health` | 살아 있는지와 저장소 마운트 경로 확인 |
| `POST /workbook` | `create_management_workbook.py` 실행 |
| `POST /slack-upload` | `slack_upload_file.py`로 관리 문서 전송 |
| `POST /slack-message` | 첨부 없이 텍스트만 전송. 시간표를 읽지 못했을 때의 오류 알림용 |

오류 알림은 Incoming Webhook 대신 Bot token의 `chat:write`로 보낸다. 파일 업로드에
이미 쓰는 토큰이라 별도 발급이 필요 없기 때문이다. `SLACK_WEBHOOK_URL`을 설정하면
`--webhook-only` 경로도 그대로 쓸 수 있다.

**경로는 `reports/` 안으로 제한한다**(밖을 가리키면 400). 저장소 전체를 허용하면
같은 저장소에 있는 `.env`를 Slack에 올리라고 시킬 수 있기 때문이다. Slack 토큰은
요청 본문으로 받지 않고 마운트된 `.env`에서 스크립트가 직접 읽는다 — n8n 실행
이력에 비밀값이 남지 않게 하려는 것이다.

`/slack-message`는 **같은 `reason`을 쿨다운(기본 6시간) 동안 한 번만 보낸다.**
시간표가 없는 상태는 사람이 고칠 때까지 이어지는데 워크플로는 5분마다 돌아서,
그대로 두면 하루 수백 건이 쌓여 정작 봐야 할 알림이 묻힌다. 값은
`RUNNER_MESSAGE_COOLDOWN_SECONDS`로 조정한다.

## 배포 (개발 PC compose)

`.docker/compose.main.dev.pc.yml`에 `rpa-runner` 서비스와 n8n의 환경변수·마운트가
이미 들어 있다. 처음 띄울 때 **이 노트북에 두 파일을 직접 만들어 둬야 한다.**

| 파일 | 내용 | 없으면 |
| --- | --- | --- |
| `RPAs/study-status-report/.env` | `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID` | Slack 전송 단계에서 실패한다 |
| `individual_tasks/시간표.md` | 시간표 문서 | Slack에 알린 뒤 기록을 중단한다 |

둘 다 `.gitignore` 대상이라 `git pull`로는 생기지 않는다. 시간표 형식과 예시는
[`templates/schedule-sample.md`](./templates/schedule-sample.md)에 있다.

**시간표를 못 읽었을 때 기본 시간표로 넘어가지 않는다.** 실제와 다른 시간표로 출결을
기록하면 잘못된 기록이 남고, 그것이 관리자 확인 대상 목록으로 이어지기 때문이다.

```bash
docker compose -f .docker/compose.main.dev.pc.yml up -d --build rpa-runner n8n
```

대상 강의실을 바꾸려면 compose의 `CLASSROOM_ID`·`CLASSROOM_NAME`을 고친다.

## 검증

검증은 샘플 데이터 기준으로 수행한다. 운영 Slack 전송과 운영 FastAPI 조회는 실제 credential과 실행 환경이 필요하므로 이 저장소에서 강제 실행하지 않는다.

```powershell
python RPAs/study-status-report/scripts/create_management_workbook.py `
  --date 2026-08-22 `
  --classroom "A강의실" `
  --events RPAs/study-status-report/templates/sample_events.json `
  --out RPAs/study-status-report/reports/study_status_management_sample.xlsx

python RPAs/study-status-report/scripts/validate_workflow_artifacts.py
```

Slack 파일 업로드는 Slack의 현재 파일 업로드 방식인 `files.getUploadURLExternal`와 `files.completeUploadExternal`를 사용한다. Bot token만으로 인증 확인은 가능하지만, 파일을 관리자 채널에 공유하려면 `files:write` 권한과 `SLACK_CHANNEL_ID`가 반드시 필요하다. Incoming Webhook은 텍스트 메시지 전송만 지원하므로 `.xlsx` 첨부 전송의 대체 수단으로 쓰지 않는다.

2026-08-22 검증 결과, 입력 문서의 Bot token과 채널 URL에서 추출한 채널 ID로 샘플 관리 문서 업로드가 성공했다. 검증 출력에는 Slack 파일 ID만 남기고 토큰, 웹훅, signing secret은 남기지 않는다.

### 실행기(rpa-runner) 검증

실행기는 컨테이너 없이도 확인할 수 있다. 저장소 루트에서 띄우고 호출한다.

```bash
REPO_DIR="$(pwd)" python RPAs/study-status-report/runner/server.py &
curl -s localhost:8099/health
```

2026-08-25 확인한 것: `/health` 응답, `/workbook`으로 샘플 이벤트 관리 문서 생성 성공,
`/slack-upload`로 새 채널(`C0BRSFJ6SSK`) 업로드 성공, 저장소 밖 경로·필수값 누락·없는
파일 요청은 400으로 거부.

컨테이너로도 같은 확인을 마쳤다. 이미지 빌드, 저장소 마운트(`/repo/RPAs`), 컨테이너
안 파이썬으로 워크북 생성, 마운트된 `.env`의 토큰으로 Slack 업로드까지 성공했다
(file ID `F0BSF2C264S`). `docker compose config`로 compose 문법과 최종 반영값도
확인했다.

### 전체 사슬 검증 (2026-08-25)

실행기를 개발자 PC에서 띄우고 n8n이 tailnet 주소로 부르게 해서 워크플로 전체를 한 번
돌렸다. 컨테이너 배포 전에 배선을 확인하려는 것이었다.

```text
n8n → 시간표 읽기 → 구간 판정 → fastapi student-states → 상태 변화 집계
    → 실행기 워크북 생성 → 실행기 Slack 업로드
```

모든 노드가 통과했고 Slack 업로드까지 성공했다(file ID `F0BSJN0LDN0`). 이때 시간표
파일이 없는 상태였는데 기본 시간표로 넘어가 멈추지 않았다.

**이 검증에서 드러난 것들이 아래 "알려진 제약"에 반영돼 있다.** 검증은 `$env`를 실제
값으로 치환하고 공부 시간 구간을 강제한 사본으로 했다 — 그 두 가지가 아직 서버에서
동작하지 않기 때문이며, compose를 반영해 재기동하면 사본 없이 그대로 돈다.

**아직 확인하지 못한 것:** 실제 공부 시간 구간에서 스케줄 트리거가 스스로 도는 것과,
같은 학생·같은 상태를 두 번 기록하지 않는 중복 방지가 여러 주기에 걸쳐 동작하는 것.
둘 다 compose 반영 후 하루를 돌려 봐야 확인된다.

## 알려진 제약과 서버 설정

컨테이너에서 실측해 확인한 것들이다. 셋 다 `.docker/compose.main.dev.pc.yml`에
반영해 두었으므로 **재기동하면 해소된다.**

| 확인한 것 | 증상 | 반영한 설정 |
| --- | --- | --- |
| 컨테이너 시계가 UTC | 시간표가 9시간 어긋나 공부 시간에 안 걸린다 | `TZ`, `GENERIC_TIMEZONE`을 `Asia/Seoul`로 |
| **Code 노드가 `TZ`를 따르지 않는다** | 위 설정을 줘도 `new Date()`가 `GMT+0000`을 낸다. n8n 2.x가 Code 노드를 별도 러너 프로세스에서 돌리는데 그쪽이 `TZ`를 물려받지 않는다 | 컨테이너 설정이 아니라 **코드에서 KST로 고정**했다(`Parse Schedule`, `Build Daily Report`). 실행 환경이 무엇이든 같은 결과가 나온다 |
| Code 노드의 `$env` 접근이 기본 차단 | `access to env vars denied`로 워크플로가 멈춘다 | `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` |
| 파일 노드의 경로 접근이 기본 차단 | 시간표 읽기가 `Access to the file is not allowed.`로 실패 | `N8N_RESTRICT_FILE_ACCESS_TO=/repo/individual_tasks` |
| 바이너리가 파일시스템에 저장됨 | 항목에 13바이트 참조만 실려 와 시간표 내용을 파싱할 수 없다 | `N8N_DEFAULT_BINARY_DATA_MODE=default` |
| n8n 이미지에 파이썬·apk 없음 | 스크립트를 부를 수 없다 | `rpa-runner` 사이드카 |

앞의 넷은 개발자 PC에서 같은 이미지를 띄워 하나씩 확인했고, **운영 중인 n8n
인스턴스에서도 같은 증상이 재현되는 것을 확인했다.** 넷 다 컨테이너 재생성이
있어야 적용된다.
