// Build Daily Report 노드가 실행기에 보낼 context를 온전히 만드는지 확인한다.
//
// **이 경로는 하루에 한 번, 마지막 교시가 끝난 뒤에만 돈다.** 그래서 잘못돼도
// 그날 저녁이 되어서야 드러나고, 그때는 이미 일일 리포트가 빠진 뒤다. 교시 보고와
// 같은 이유로 여기서 미리 돌려 본다. 실행은 scripts/run_workflow_tests.py 가 한다.
//
// 확인하는 것은 두 가지다. 뒤 노드(Create Daily Workbook -> Upload Daily Report to
// Slack)가 노드 이름 참조 없이 자기 입력만으로 일할 수 있도록 context가 갖춰졌는지,
// 그리고 하루치 이벤트만 골라 담는지.

const fs = require('fs');

const workflowPath = process.argv[2];
if (!workflowPath) {
  console.error('사용법: node daily_report.test.js <study-status-report.n8n.json 경로>');
  process.exit(2);
}
const workflow = JSON.parse(fs.readFileSync(workflowPath, 'utf8'));
const node = workflow.nodes.find((item) => item.name === 'Build Daily Report');
if (!node) {
  console.error("워크플로에 'Build Daily Report' 노드가 없습니다.");
  process.exit(2);
}
const CODE = node.parameters.jsCode;

const CLASSROOM = 'cls-1';

// 이 노드는 KST 날짜를 직접 계산한다. 판정 기준을 고정하려고 시각을 고정한다.
function run({ nowUtc, staticData }) {
  const realNow = Date.now;
  Date.now = () => new Date(nowUtc).getTime();
  try {
    const $env = { CLASSROOM_ID: CLASSROOM, CLASSROOM_NAME: '4A 강의실' };
    const fn = new Function('$env', '$getWorkflowStaticData', 'Buffer', CODE);
    return fn($env, () => staticData, Buffer)[0].json;
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
const NOW = '2026-08-26T09:05:00Z'; // KST 18:05, 일일 리포트 트리거 시각
const staticData = {
  events: [
    { key: `${DAY}|${CLASSROOM}|1교시|s1|UNKNOWN|t1`, period: '1교시', student_id: 's1', student_state: 'UNKNOWN' },
    { key: `${DAY}|${CLASSROOM}|2교시|s2|ABSENT|t2`, period: '2교시', student_id: 's2', student_state: 'ABSENT' },
    // 지난 날짜와 다른 강의실은 오늘 리포트에 섞이면 안 된다.
    { key: `2026-08-20|${CLASSROOM}|1교시|s9|ABSENT|t9`, period: '1교시', student_id: 's9', student_state: 'ABSENT' },
    { key: `${DAY}|other-classroom|1교시|s8|ABSENT|t8`, period: '1교시', student_id: 's8', student_state: 'ABSENT' },
  ],
};

const result = run({ nowUtc: NOW, staticData });

check('KST 기준 날짜를 쓴다', result.today, DAY);
check('오늘·이 강의실 이벤트만 담는다', result.allEvents.map((e) => e.student_id), ['s1', 's2']);
check('워크북 경로가 날짜·강의실로 정해진다', result.workbookPath,
  `RPAs/study-status-report/reports/study_status_${DAY}_${CLASSROOM}.xlsx`);

const context = result.context || {};
check('context에 뒤 노드가 쓸 값이 모두 있다',
  [context.today, context.classroomId, context.periodName, Boolean(context.uploadTitle), Boolean(context.uploadComment)],
  [DAY, CLASSROOM, 'daily-report', true, true]);
check('eventsBase64가 allEvents와 일치한다',
  JSON.parse(Buffer.from(result.eventsBase64, 'base64').toString('utf8')).map((e) => e.student_id),
  ['s1', 's2']);

// 자정 직후에 UTC 날짜를 쓰면 전날로 만들어진다. KST 계산이 살아 있는지 본다.
const midnight = run({ nowUtc: '2026-08-25T15:30:00Z', staticData }); // KST 08-26 00:30
check('자정 직후에도 KST 날짜로 만든다', midnight.today, DAY);

console.log(failures === 0 ? '\nOK: 일일 리포트 경로 전부 통과' : `\n실패 ${failures}건`);
process.exit(failures === 0 ? 0 : 1);
