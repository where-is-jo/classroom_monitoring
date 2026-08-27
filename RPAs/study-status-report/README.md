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

50분 수업 + 10분 휴식. 오전 4교시, 점심 13:10~14:10, 오후 4교시, 18:00 종료다.

| 구간 | 시작 | 종료 | | 구간 | 시작 | 종료 |
| --- | --- | --- | --- | --- | --- | --- |
| 1교시 | 09:20 | 10:10 | | 5교시 | 14:10 | 15:00 |
| 2교시 | 10:20 | 11:10 | | 6교시 | 15:10 | 16:00 |
| 3교시 | 11:20 | 12:10 | | 7교시 | 16:10 | 17:00 |
| 4교시 | 12:20 | 13:10 | | 8교시 | 17:10 | 18:00 |

쉬는시간과 점심은 자동 기록 대상에서 제외한다. 구간 이름에 `자습`·`교시`가 들어간
줄만 읽으므로, 제외하려는 구간은 그 낱말을 쓰지 않으면 된다.

**이 표는 참고용이고 실제 기준은 `individual_tasks/시간표.md`다.** 그 파일은
저장소에 커밋되지 않으므로, 시간표가 바뀌면 파일만 고치면 된다 — 재기동도
워크플로 수정도 필요 없다.

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
| Slack 파일 업로드 실패 | 파일은 보존하고 실패 로그를 남긴다. 전송 노드 자체는 재시도하지 않지만, **교시 보고는 원장에 적히지 않아 다음 5분 주기가 다시 시도한다**(아래 "교시 종료 보고를 놓치지 않는 법"). |
| 권한 또는 토큰 오류 | 자동 재시도하지 않고 관리자 확인 대상으로 중단한다. |

## 중복 실행 방지

n8n 워크플로우 전역 static data에 `date|classroom_id|period|student_id|state|observed_at` 키를 저장한다. 동일 키는 다시 기록하지 않는다.

## 교시 종료 보고를 놓치지 않는 법

교시가 끝나면 그 시점의 관리 문서를 Slack에 올린다. **어느 교시를 올릴지는 시간 창이
아니라 원장(ledger)으로 정한다.**

예전에는 "종료 후 5분 안"에 든 교시만 보고했다. 그런데 스케줄 트리거 주기가 똑같이
5분이라, n8n 재시작 등으로 틱이 한 번만 밀리면 그 교시가 창을 통째로 지나쳐 **보고서가
조용히 사라졌다.** 사라진 것을 알 방법도 없었다.

지금은 이렇게 돈다.

1. `Parse Schedule`이 "이미 끝났는데 아직 보고하지 않은 교시"를 찾는다. 판단 근거는
   워크플로 정적 데이터의 `reportedPeriods`(원장)다.
2. 그 교시의 관리 문서를 만들어 Slack에 올린다.
3. **전송이 끝난 뒤에야** `Mark Period Reported`가 원장에 적는다.

그래서 틱이 밀려도, FastAPI 조회나 Slack 전송이 실패해도, 원장에 남지 않았으니 다음
주기가 같은 교시를 다시 집는다. 반대로 한 번 올린 교시는 원장에 있어 다시 올라가지
않는다.

**따라잡기 창(`PERIOD_REPORT_CATCH_UP_MINUTES`, 기본 60분).** 종료 후 이 시간을 넘기면
포기한다. 여기까지 밀렸다면 다음 교시가 이미 한참 지났고, 늦은 보고서는 어느 교시 것인지
오히려 헷갈리기 때문이다. **포기할 때는 조용히 넘어가지 않고 Slack으로 알린다**
(`Notify Missed Period`). 알림은 교시당 한 번만 나간다.

낮에 워크플로를 처음 켜는 경우는 놓친 것이 아니다. 그날 처음 돈 시각(`watchingSince`)
보다 먼저 끝난 교시는 애초에 관측 대상이 아니었으므로 알리지 않는다. 시간표를 읽지
못한 주기에는 원장을 건드리지 않는다 — 그때 쓰이는 것은 기본 시간표라, 기록하면 실제와
다른 교시 이름이 원장에 남기 때문이다.

이 판정은 [`tests/period_report.test.js`](./tests/period_report.test.js)가 실제 Node
런타임에서 검증한다(아래 "검증").

## 주간 보고서와 메일

금요일 18:10에 그 주 월요일부터 당일까지를 집계해 **Slack에 올리고 메일로도 보낸다.**
첨부는 관리 문서와 같은 `.xlsx`이고 시트는 다섯이다 — 주간 요약, 일자별, 학생별,
교시별, 상태 변화 기록.

**Slack이 먼저고 메일이 나중이다.** 메일은 SMTP 자격 증명이 있어야 나가는데, 그것이
없거나 틀렸다고 해서 그 주 보고서가 통째로 사라지면 안 된다. Slack은 이미 검증된
경로라 먼저 태우고, 메일은 더해지는 통로로 뒀다. 메일 단계가 실패해도 워크플로를
멈추지 않는다(`onError: continueRegularOutput`) — 실패는 실행 이력에 남는다.

**원천은 FastAPI가 아니라 일일 산출물이다.** 보고서가 말하는 "상태 변화"는 이 RPA가
5분마다 폴링해 직접 diff한 파생 계열이다. FastAPI에는 원시 탐지 이벤트만 있어서
거기서 주간을 다시 만들면 의미가 다른 두 번째 계열이 생기고, **주간 숫자가 이미 보낸
일일 보고서와 어긋난다.** 게다가 `detection_event_retention_days` 기본값이 7일이라
월요일에 지난주를 조회하면 가장 오래된 날이 막 만료되는 시점이다.

그래서 `/workbook`이 관리 문서를 만들 때 **그 이벤트를 그대로** `data/`에 남기고
(`events_<날짜>_<강의실>.json`), 주간은 그 파일들만 읽는다. 이탈률 식도
`create_management_workbook.py`에서 그대로 가져와 일일과 어긋나지 않게 했다.

| 디렉터리 | 담는 것 | 학생 이름 |
| --- | --- | --- |
| `reports/` | 사람이 받는 `.xlsx` | **있다** |
| `data/` | 주간이 읽는 이벤트 원본 | **있다** |
| `logs/` | 실행 이력 | 없다 |

셋 다 `.gitignore` 대상이다. `logs/`만 이름이 없는 것은 의도된 구분이다 — 실행 이력은
문제를 쫓을 때 자주 열어 보게 되므로 개인정보를 담지 않는다.

**기록이 없는 날을 0으로 세지 않는다.** RPA가 멈춰 있던 날과 이탈이 없던 날은 다른
이야기인데 둘 다 0으로 적으면 구분이 사라진다. 없는 날은 "기록 없음"으로 적고,
한 날도 없으면 아예 만들지 않는다(받는 사람이 "이탈이 없었다"로 읽을 수 있다).

**메일은 Slack 비공개 채널과 다르다.** 전달되고, 개인 사서함에 남고, 보존 기간을
통제할 수 없다. 첨부에 실명과 좌석이 들어 있으므로 `REPORT_EMAIL_TO`를 늘릴 때
그 점을 함께 판단한다. SMTP 자격 증명은 Slack 토큰과 같이 `.env`에서 스크립트가
직접 읽는다.

## 노드 이름 참조를 늘리지 않는다

n8n 표현식은 다른 노드를 **이름 문자열로만** 부를 수 있다(`$('Parse Schedule')`).
편집기에서 노드 이름을 바꾸면 이 참조는 경고 없이 끊기고, `undefined`가 아래로 흘러
빈 보고서가 올라가거나 조건이 늘 거짓이 된다.

HTTP Request 노드의 응답은 항목의 `json`을 통째로 덮어써서, 원래는 뒤 노드마다 앞
노드를 이름으로 거슬러 올라가야 했다. 지금은 **필요한 값을 `context`에 담아 실행기에
같이 보내고, 실행기가 그대로 돌려준다**(`runner/server.py`의 `_echo_context`). 뒤 노드는
자기 입력만 보고 일을 마친다.

그 결과 이름 참조는 `Build Change Events` 한 곳만 남았고, 이 노드는 실행기를 거치지
않아 다른 방법이 없다. 대신 참조가 끊기면 즉시 예외를 던지고,
`scripts/validate_workflow_artifacts.py`가 **참조가 실제 노드를 가리키는지, 새 참조가
늘지 않았는지** 검사한다.

## 파일

- `workflows/study-status-report.n8n.json`: n8n import용 워크플로우
- `scripts/create_management_workbook.py`: `.xlsx` 생성 스크립트
- `scripts/slack_upload_file.py`: Slack 외부 업로드 API 기반 `.xlsx` 전송 스크립트
- `scripts/validate_workflow_artifacts.py`: 워크플로우 JSON과 `.xlsx` 산출물 검증 스크립트
- `scripts/create_weekly_workbook.py`: 주간 보고서 `.xlsx` 생성 스크립트
- `scripts/send_email.py`: 보고서 메일 전송 스크립트(표준 라이브러리 `smtplib`)
- `scripts/deploy_workflow.py`: 워크플로를 n8n에 반영 (**비활성화 → 갱신 → 활성화**)
- `scripts/run_workflow_tests.py`: `tests/`의 Code 노드 테스트를 n8n 컨테이너의 Node로 실행
- `tests/period_report.test.js`: 교시 종료 판정(원장·따라잡기 창) 검증
- `tests/daily_report.test.js`: 일일 리포트가 담을 이벤트와 context 검증
- `tests/weekly_report.test.js`: 주간 보고서 기간 계산 검증
- `runner/server.py`: 위 두 스크립트를 HTTP로 감싸는 실행기 (아래 참고)
- `runner/Dockerfile`: 실행기 컨테이너 이미지
- `templates/sample_events.json`: 검증용 상태 변화 샘플
- `scripts/toggle_workflow.py`: 워크플로 on/off 스크립트
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
| `GET /health` | 살아 있는지, 저장소 마운트 경로, 드라이런 여부, **`logs/`·`reports/`에 실제로 쓸 수 있는지** 확인. 쓰지 못하면 `503`과 원인을 돌려준다 |
| `POST /workbook` | `create_management_workbook.py` 실행 |
| `POST /weekly-workbook` | `create_weekly_workbook.py` 실행. 이벤트를 본문으로 받지 않고 `data/`를 읽는다 |
| `POST /slack-upload` | `slack_upload_file.py`로 관리 문서 전송 |
| `POST /email` | `send_email.py`로 보고서 메일 전송. 첨부는 `reports/` 안으로 제한한다 |
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

## 개발 중 켜고 끄기

개발 단계에서는 워크플로를 멈추거나 Slack만 조용히 시켜야 할 때가 잦다. 두 가지
수단이 있고 **끄는 범위가 다르다.**

| 하고 싶은 것 | 방법 | 멈추는 것 |
| --- | --- | --- |
| 전부 멈추기 | `toggle_workflow.py --off` | 수집·문서·전송 전부 |
| Slack만 조용히 | 실행기에 `RPA_DRY_RUN=true` | Slack 전송만. 상태 수집·관리 문서·실행 이력은 그대로 남는다 |

```bash
python RPAs/study-status-report/scripts/toggle_workflow.py --status
python RPAs/study-status-report/scripts/toggle_workflow.py --off
python RPAs/study-status-report/scripts/toggle_workflow.py --on
```

`N8N_BASE_URL`과 `N8N_API_KEY`가 필요하다(`.env`). API 키는 n8n 편집기의
**Settings > n8n API**에서 발급한다. 같은 상태로 다시 호출하면 아무것도 하지 않는다.

드라이런은 실행기 컨테이너의 환경변수라 compose에서 켠다. 켜 둔 것을 잊고 "왜 Slack이
안 오지" 하는 일이 없도록 `GET /health`가 현재 값을 함께 돌려준다.

```bash
curl -s localhost:8099/health   # {"ok": true, "repo_dir": "/repo", "dry_run": true}
```

## 배포 (개발 PC compose)

`.docker/compose.main.dev.pc.yml`에 `rpa-runner` 서비스와 n8n의 환경변수·마운트가
이미 들어 있다. 처음 띄울 때 **이 노트북에 두 파일을 직접 만들어 둬야 한다.**

| 파일 | 내용 | 없으면 |
| --- | --- | --- |
| `RPAs/study-status-report/.env` | Slack 토큰·채널, SMTP 자격 증명 | 전송 단계에서 실패한다 |

**`.env`만 직접 만들면 된다.** `.gitignore` 대상이라 `git pull`로는 생기지 않는다.

**시간표는 저장소가 관리한다**(`individual_tasks/시간표.md`). 예전에는 이 파일도
gitignore 대상이라 호스트마다 직접 두어야 했는데, **없으면 RPA가 기록을 중단하므로
다른 호스트에 배포하는 순간 그대로 실패한다.** 개인 노트가 아니라 운영 설정이라
저장소로 옮겼다. 형식과 예시는
[`templates/schedule-sample.md`](./templates/schedule-sample.md)에 있다.

**시간표를 못 읽었을 때 기본 시간표로 넘어가지 않는다.** 실제와 다른 시간표로 출결을
기록하면 잘못된 기록이 남고, 그것이 관리자 확인 대상 목록으로 이어지기 때문이다.

```bash
docker compose -f .docker/compose.main.dev.pc.yml up -d --build rpa-runner n8n
```

대상 강의실을 바꾸려면 compose의 `CLASSROOM_ID`·`CLASSROOM_NAME`을 고친다.

### 지금 도는 것은 이 compose가 아니다 (2026-08-26 확인)

**위 명령은 현재 PM PC에서 그대로 실패한다.** `.docker/env/`가 비어 있어 compose가
`fastapi.dev.env`를 찾다가 멈춘다(`n8n.dev.env`도 없다). 그리고 돌고 있는 `n8n`·
`rpa-runner` 컨테이너에는 **compose 라벨이 아예 없다** — `docker run`으로 손수 만든
것들이고, 네트워크도 compose의 `backend`가 아니라 `classroom-rpa`다.

즉 이 절의 compose 정의는 **가야 할 곳이지 지금 있는 곳이 아니다.** 실행기를 다시
띄워야 하면 지금 구성에서는 이렇게 한다.

```bash
docker build -t rpa-runner:local RPAs/study-status-report/runner
docker rm -f rpa-runner
docker run -d --name rpa-runner --network classroom-rpa --restart unless-stopped   -e REPO_DIR=/repo -v "<저장소 절대경로>/RPAs:/repo/RPAs" rpa-runner:local
```

컨테이너 이름은 `rpa-runner`여야 한다 — n8n이 `http://rpa-runner:8099`로 부르고,
사용자 정의 네트워크에서는 컨테이너 이름이 곧 DNS 이름이다.

**compose로 정리하려면 없어진 두 env 파일이 필요하다.** 그 값은 이 저장소에 없고
처음 띄운 사람만 갖고 있다. CTO 노트북으로 옮기기 전에 해결해야 할 항목이다.

### 워크플로를 반영할 때는 비활성화 → 갱신 → 활성화

```bash
python RPAs/study-status-report/scripts/deploy_workflow.py --dry-run   # 차이만 본다
python RPAs/study-status-report/scripts/deploy_workflow.py
```

**단순 PUT만 하면 cron 트리거가 그날 몫을 놓친다.** 2026-08-26에 실측했다 — 8/25에는
18:05 일일 리포트가 정상 발화했는데, 8/26에 API로 PUT을 두 번 하고 나니 **5분 간격
트리거는 계속 도는데 일일 리포트만 발화하지 않았다.** 비활성화 후 다시 활성화하니
곧바로 발화했다. 간격 트리거는 재등록되는데 cron은 그렇지 않은 것으로 보인다.

`deploy_workflow.py`가 그 순서를 지키고, **정적 데이터를 그대로 넘긴다** — 거기에
그날 이벤트와 이미 보고한 교시 원장이 들어 있어서, 빠뜨리면 그날 기록이 사라지고
이미 올린 교시가 다시 올라간다.

### 실행기 UID (다른 호스트로 옮길 때)

실행기는 root로 돌지 않으면서 마운트된 `logs/`·`reports/`에 쓴다. 기본 UID/GID는
**65534(`nobody`)** 로, 지금 이 PC에서 도는 값이다.

"Docker Desktop은 바인드 마운트 권한을 무시하니 UID는 아무래도 된다"는 말이 돌았는데
**사실이 아니다.** 실측하면 WSL2의 9p 마운트에 `metadata` 옵션이 붙어 리눅스 권한이
그대로 적용되고, `logs/`는 `drwxr-xr-x 65534:65534`다 — 지금 써지는 이유는 `nobody`가
그 디렉터리를 만들어 소유하고 있어서다.

그래서 **다른 호스트(특히 리눅스 서버)로 옮길 때는 둘 중 하나를 해야 한다.**

```bash
# (a) 컨테이너 UID를 호스트 소유자에 맞춘다
RPA_RUNNER_UID=$(id -u) RPA_RUNNER_GID=$(id -g)   docker compose -f .docker/compose.main.dev.pc.yml up -d --build rpa-runner

# (b) 또는 기존 디렉터리 소유자를 컨테이너 UID에 맞춘다
sudo chown -R 65534:65534 RPAs/study-status-report/logs RPAs/study-status-report/reports
```

맞지 않으면 실행기가 기동 로그에 오류를 남기고 `GET /health`가 `503`으로 답한다.
예전에는 첫 보고 시점까지 아무 표시 없이 조용히 실패했다.

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

워크플로 Code 노드의 판정 로직은 **n8n 컨테이너의 Node로 직접 돌려서** 확인한다.
그 코드는 워크플로 JSON 안의 문자열이라 평소에는 n8n이 실행할 때만 돌고, 틀려도 그날
보고서가 빠질 뿐 아무 표시가 나지 않는다. 돌고 있는 워크플로에는 손대지 않는다 —
컨테이너 `/tmp`에 파일을 넣고 부를 뿐이다.

```bash
python RPAs/study-status-report/scripts/run_workflow_tests.py
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
