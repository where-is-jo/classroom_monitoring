// Parse Schedule 노드의 **교시 종료 판정**을 실제 Node 런타임에서 돌려 확인한다.
//
// **왜 컨테이너에서 돌리나.** 이 코드는 워크플로 JSON 안의 문자열이라 평소에는 n8n이
// 실행할 때만 돈다. 판정이 틀려도 그날 보고서가 빠질 뿐 아무 데도 표시가 안 난다.
// 그래서 코드만 떼어 n8n 컨테이너의 Node로 직접 돌린다 — 워크플로에는 손대지 않는다.
// 실행은 scripts/run_workflow_tests.py 가 대신해 준다.
//
// 판정의 핵심은 "끝났는데 아직 보고하지 않은 교시"를 원장(reportedPeriods)으로 찾고,
// Slack 전송이 끝난 뒤에야 원장에 적는다는 것이다. 예전에는 "종료 후 5분" 시간 창으로
// 판정했는데 트리거 주기와 창이 같아서, 틱이 한 번만 밀려도 그 교시 보고서가 조용히
// 사라졌다. 아래 D가 바로 그 경우다.

const fs = require('fs');

const workflowPath = process.argv[2];
if (!workflowPath) {
  console.error('사용법: node period_report.test.js <study-status-report.n8n.json 경로>');
  process.exit(2);
}
const workflow = JSON.parse(fs.readFileSync(workflowPath, 'utf8'));
const parseSchedule = workflow.nodes.find((node) => node.name === 'Parse Schedule');
if (!parseSchedule) {
  console.error("워크플로에 'Parse Schedule' 노드가 없습니다.");
  process.exit(2);
}
const CODE = parseSchedule.parameters.jsCode;

// templates/schedule-sample.md 와 같은 형식. 등원·점심은 기록 대상이 아니다.
const SCHEDULE = [
  '| **07:40 ~ 08:00** | 등원 |',
  '| **08:00 ~ 08:40** | 아침 자습 |',
  '| **08:40 ~ 10:10** | 1교시 |',
  '| **10:20 ~ 11:50** | 2교시 |',
  '| **11:50 ~ 13:00** | 점심 |',
  '| **13:00 ~ 14:30** | 3교시 |',
  '| **14:40 ~ 16:10** | 4교시 |',
].join('\n');

// n8n이 Code 노드에 넣어 주는 것들을 대신 만들어 준다. 시각은 Date.now만 바꾸면
// 되는데, 노드가 `new Date(Date.now() + 9시간)`으로 KST를 직접 계산하기 때문이다.
function run({ nowUtc, staticData, scheduleText = SCHEDULE, env = {} }) {
  const realNow = Date.now;
  Date.now = () => new Date(nowUtc).getTime();
  try {
    const $env = Object.assign({ CLASSROOM_ID: 'cls-1', CLASSROOM_NAME: '4A 강의실' }, env);
    const $getWorkflowStaticData = () => staticData;
    const binary = scheduleText === null
      ? undefined
      : { data: { data: Buffer.from(scheduleText, 'utf8').toString('base64') } };
    const $input = { first: () => ({ binary }) };
    const fn = new Function('$env', '$getWorkflowStaticData', '$input', 'Buffer', CODE);
    return fn($env, $getWorkflowStaticData, $input, Buffer)[0].json;
  } finally {
    Date.now = realNow;
  }
}

let failures = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  const ok = a === e;
  if (!ok) failures += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}`);
  if (!ok) console.log(`        기대 ${e}\n        실제 ${a}`);
}

const DAY = '2026-08-26';
const endedName = (result) => (result.endedPeriod ? result.endedPeriod.name : null);
// 하루 시작부터 지켜보고 있었고, 아침 자습은 이미 보고를 마친 상태.
const watching = () => ({
  watchingSince: { [DAY]: DAY + 'T00:00:00.000Z' },
  reportedPeriods: { [DAY + '|아침 자습']: '보고함' },
});

// A. 1교시(08:40~10:10) 종료 2분 뒤 첫 틱 — 보고 대상으로 잡힌다.
let staticData = watching();
let result = run({ nowUtc: '2026-08-26T01:12:00Z', staticData });
check('A 종료 2분 뒤 → 1교시를 보고 대상으로 잡는다', [endedName(result), result.missedPeriods], ['1교시', []]);

// B. 전송이 실패해 원장에 적히지 않은 채 다음 틱 — 같은 교시를 다시 잡는다.
//    전송 실패가 곧 보고서 유실이 되지 않게 하려는 것이다.
result = run({ nowUtc: '2026-08-26T01:17:00Z', staticData });
check('B 원장에 없으면 다음 틱이 다시 시도한다', [endedName(result), result.missedPeriods], ['1교시', []]);

// C. Mark Period Reported가 적은 뒤 — 더는 잡지 않는다(같은 파일 반복 업로드 방지).
staticData.reportedPeriods[DAY + '|1교시'] = '2026-08-26T01:17:30.000Z';
result = run({ nowUtc: '2026-08-26T01:22:00Z', staticData });
check('C 원장에 적힌 교시는 다시 올리지 않는다', [endedName(result), result.missedPeriods], [null, []]);

// D. **회귀 방지의 핵심.** 틱이 20분 밀린 경우. 옛 '종료 후 5분' 창에서는 여기서
//    보고서가 조용히 사라졌다.
staticData = watching();
result = run({ nowUtc: '2026-08-26T01:30:00Z', staticData });
check('D 틱이 20분 밀려도 보고한다', [endedName(result), result.missedPeriods], ['1교시', []]);

// E. 따라잡기 창(기본 60분)을 넘기면 포기한다. 대신 놓쳤다고 알린다.
staticData = watching();
result = run({ nowUtc: '2026-08-26T02:45:00Z', staticData });
check('E 따라잡기 창을 넘기면 포기하고 알린다', [endedName(result), result.missedPeriods], [null, ['1교시']]);

// F. 한 번 알린 뒤에는 반복하지 않는다. 5분마다 같은 알림이 쌓이면 묻힌다.
result = run({ nowUtc: '2026-08-26T02:47:00Z', staticData });
check('F 놓침 알림은 교시당 한 번뿐이다', [endedName(result), result.missedPeriods], [null, []]);

// G. 낮에 워크플로를 처음 켠 경우. 관측한 적 없는 오전 교시를 놓쳤다고 하면 안 된다.
staticData = {};
result = run({ nowUtc: '2026-08-26T06:00:00Z', staticData });
check('G 낮에 처음 켜도 오전 교시를 놓쳤다고 하지 않는다', [endedName(result), result.missedPeriods], [null, []]);
check('G 그때 원장은 비어 있다', Object.keys(staticData.reportedPeriods), []);

// H. 시간표를 읽지 못하면 원장에 손대지 않는다. 이때 periods는 기본 시간표라,
//    기록하면 실제와 다른 교시 이름이 원장에 남는다.
staticData = {};
result = run({ nowUtc: '2026-08-26T02:45:00Z', staticData, scheduleText: null });
check(
  'H 시간표를 못 읽으면 원장을 건드리지 않는다',
  [result.scheduleError !== '', staticData.reportedPeriods === undefined, endedName(result), result.missedPeriods],
  [true, true, null, []],
);

// I. 따라잡기 창은 PERIOD_REPORT_CATCH_UP_MINUTES로 조정된다.
staticData = watching();
result = run({ nowUtc: '2026-08-26T01:30:00Z', staticData, env: { PERIOD_REPORT_CATCH_UP_MINUTES: '10' } });
check('I 창을 10분으로 줄이면 20분 밀림은 포기한다', [endedName(result), result.missedPeriods], [null, ['1교시']]);

// J. 지난 날짜 기록은 정리된다. 워크플로 정적 데이터는 DB에 통째로 저장돼서
//    날짜별로 쌓이면 계속 자란다.
staticData = {
  reportedPeriods: { '2026-08-20|1교시': '보고함', [DAY + '|아침 자습']: '보고함' },
  watchingSince: { '2026-08-20': '옛날', [DAY]: DAY + 'T00:00:00.000Z' },
};
run({ nowUtc: '2026-08-26T01:12:00Z', staticData });
check(
  'J 지난 날짜 기록을 버린다',
  [Object.keys(staticData.reportedPeriods).sort(), Object.keys(staticData.watchingSince)],
  [[DAY + '|아침 자습'], [DAY]],
);

// K. 수업이 진행 중일 때는 종료 보고가 잡히지 않는다.
staticData = watching();
result = run({ nowUtc: '2026-08-26T00:30:00Z', staticData });
check(
  'K 수업 중에는 종료 보고가 잡히지 않는다',
  [result.activePeriod.name, endedName(result), result.missedPeriods],
  ['1교시', null, []],
);

console.log(failures === 0 ? '\nOK: 교시 종료 판정 전부 통과' : `\n실패 ${failures}건`);
process.exit(failures === 0 ? 0 : 1);
