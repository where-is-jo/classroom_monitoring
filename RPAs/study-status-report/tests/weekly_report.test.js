// Build Weekly Range 노드가 담을 기간을 제대로 고르는지 확인한다.
//
// **틀려도 소리가 나지 않는 계산이다.** 주 시작을 하루 밀리면 엉뚱한 주가 메일로 나가고,
// 받는 사람은 숫자가 조금 다르다는 것만 느낀다. 그래서 요일별로 못을 박아 둔다.
// 실행은 scripts/run_workflow_tests.py 가 한다.

const fs = require('fs');

const workflowPath = process.argv[2];
if (!workflowPath) {
  console.error('사용법: node weekly_report.test.js <study-status-report.n8n.json 경로>');
  process.exit(2);
}
const workflow = JSON.parse(fs.readFileSync(workflowPath, 'utf8'));
const node = workflow.nodes.find((item) => item.name === 'Build Weekly Range');
if (!node) {
  console.error("워크플로에 'Build Weekly Range' 노드가 없습니다.");
  process.exit(2);
}
const CODE = node.parameters.jsCode;
const CLASSROOM = 'cls-1';

function run(nowUtc) {
  const realNow = Date.now;
  Date.now = () => new Date(nowUtc).getTime();
  try {
    const $env = { CLASSROOM_ID: CLASSROOM, CLASSROOM_NAME: '4A 강의실' };
    return new Function('$env', CODE)($env)[0].json;
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

// 2026-08-24(월) ~ 2026-08-30(일) 이 한 주다.
// 금요일 18:10 KST = 같은 날 09:10 UTC — 실제 트리거 시각이다.
let result = run('2026-08-28T09:10:00Z');
check('금요일 정규 발화 → 그 주 월~금', [result.from, result.to], ['2026-08-24', '2026-08-28']);

// 주 시작. 하루치만 담긴다.
result = run('2026-08-24T09:10:00Z');
check('월요일에 돌리면 그날 하루', [result.from, result.to], ['2026-08-24', '2026-08-24']);

// 일요일은 그 주의 끝이다. 월요일로 6일을 거슬러야 한다.
result = run('2026-08-30T09:10:00Z');
check('일요일에 돌려도 같은 주 월요일부터', [result.from, result.to], ['2026-08-24', '2026-08-30']);

// 다음 주로 넘어가면 기준도 넘어간다.
result = run('2026-08-31T09:10:00Z');
check('다음 주 월요일은 새 주로 잡는다', [result.from, result.to], ['2026-08-31', '2026-08-31']);

// **KST 경계.** UTC로 계산하면 여기서 전날로 밀린다.
// 2026-08-29(토) 00:30 KST = 2026-08-28(금) 15:30 UTC
result = run('2026-08-28T15:30:00Z');
check('자정 직후에도 KST 날짜로 센다', [result.from, result.to], ['2026-08-24', '2026-08-29']);

// 메일 노드가 자기 입력만 보고 일할 수 있어야 한다.
result = run('2026-08-28T09:10:00Z');
const context = result.context || {};
check(
  'context에 메일이 쓸 값이 모두 있다',
  [context.from, context.to, context.periodName, Boolean(context.subject), Boolean(context.body)],
  ['2026-08-24', '2026-08-28', 'weekly-report', true, true],
);
check('제목에 기간이 들어간다', context.subject.includes('2026-08-24 ~ 2026-08-28'), true);
check(
  '워크북 경로가 기간과 강의실로 정해진다',
  result.workbookPath,
  `RPAs/study-status-report/reports/weekly_study_status_2026-08-24_2026-08-28_${CLASSROOM}.xlsx`,
);

console.log(failures === 0 ? '\nOK: 주간 기간 계산 전부 통과' : `\n실패 ${failures}건`);
process.exit(failures === 0 ? 0 : 1);
