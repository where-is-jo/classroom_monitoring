"""워크플로 Code 노드의 판정 로직을 n8n 컨테이너의 Node로 돌린다.

**왜 이런 방식인가.** Code 노드의 코드는 워크플로 JSON 안의 문자열이라 평소에는 n8n이
실행할 때만 돈다. 판정이 틀려도 그날 보고서가 빠질 뿐 아무 표시도 나지 않는다. 그렇다고
개발자 PC에 Node를 따로 깔 필요는 없다 — n8n 컨테이너 안에 이미 있고, 워크플로가 실제로
도는 것도 그 런타임이다. 그래서 테스트 파일과 워크플로 JSON을 컨테이너에 넣고 거기서
돌린다.

**돌고 있는 워크플로에는 손대지 않는다.** 컨테이너의 /tmp에 파일을 두고 node로 부를
뿐이라, n8n에 등록된 워크플로나 실행 이력에는 아무 영향이 없다.

    python RPAs/study-status-report/scripts/run_workflow_tests.py

n8n 컨테이너 이름이 다르면 ``--container``로 준다.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "workflows" / "study-status-report.n8n.json"
TESTS = sorted((ROOT / "tests").glob("*.test.js"))
CONTAINER_DIR = "/tmp/rpa-workflow-tests"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    # **utf-8을 못으로 박는다.** 컨테이너는 한글을 utf-8로 내보내는데, 윈도우
    # 파이썬은 콘솔 코드페이지(cp949)로 읽으려다 UnicodeDecodeError로 죽는다.
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="n8n", help="n8n 컨테이너 이름 (기본: n8n)")
    args = parser.parse_args()

    # 출력도 같은 이유로 utf-8로 맞춘다. 콘솔이 cp949면 테스트 이름의 한글에서
    # print가 실패한다.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    if not TESTS:
        print(f"테스트 파일이 없습니다: {ROOT / 'tests'}")
        return 1

    probe = run(["docker", "exec", args.container, "node", "--version"])
    if probe.returncode != 0:
        print(
            f"n8n 컨테이너('{args.container}')에서 node를 부르지 못했습니다. "
            "컨테이너가 떠 있는지 확인하세요(docker ps).\n"
            f"{probe.stderr.strip()}"
        )
        return 1
    print(f"n8n 컨테이너 node {probe.stdout.strip()}")

    # 매번 새로 만든다. 지난 실행에서 남은 파일로 도는 일이 없게 한다.
    run(["docker", "exec", args.container, "rm", "-rf", CONTAINER_DIR])
    made = run(["docker", "exec", args.container, "mkdir", "-p", CONTAINER_DIR])
    if made.returncode != 0:
        print(f"작업 디렉터리를 만들지 못했습니다: {made.stderr.strip()}")
        return 1

    for source in [WORKFLOW, *TESTS]:
        copied = run(["docker", "cp", str(source), f"{args.container}:{CONTAINER_DIR}/{source.name}"])
        if copied.returncode != 0:
            print(f"{source.name}을(를) 컨테이너에 넣지 못했습니다: {copied.stderr.strip()}")
            return 1

    failed = 0
    for test in TESTS:
        print(f"\n--- {test.name} ---")
        result = run(
            [
                "docker",
                "exec",
                args.container,
                "node",
                f"{CONTAINER_DIR}/{test.name}",
                f"{CONTAINER_DIR}/{WORKFLOW.name}",
            ]
        )
        print(result.stdout.rstrip())
        if result.stderr.strip():
            print(result.stderr.rstrip(), file=sys.stderr)
        if result.returncode != 0:
            failed += 1

    run(["docker", "exec", args.container, "rm", "-rf", CONTAINER_DIR])
    if failed:
        print(f"\n실패한 테스트 파일 {failed}개")
        return 1
    print("\nOK: 워크플로 Code 노드 테스트 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
