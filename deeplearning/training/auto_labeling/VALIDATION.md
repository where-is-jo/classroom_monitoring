# YOLO 반자동 라벨링 V1 검증 기록

이 문서는 실제 인물 영상, 로컬 절대경로와 검수자 ID를 남기지 않고 재현 가능한 실행
환경·파일 해시·결과 수치만 기록한다. 모델, 합성 입력, 프레임, 라벨과 발행 데이터셋은
`deeplearning/training/data/` 아래의 Git 제외 산출물이다.

## 2026-08-18 로컬 smoke

**현재 상태**: 자동 검사, 실제 YOLO 후보 생성, labelImg GUI 저장·재열기와
`review-complete → publish → validate` 전체 흐름을 통과했다.

### 환경

| 항목 | 값 |
| --- | --- |
| OS | Windows 10.0.19045 |
| Python | 3.12.13 |
| OpenCV | 5.0.0 |
| PyTorch | 2.13.0+cpu |
| Ultralytics | 8.4.121 |
| Device | CPU |
| 모델 | `yolov8n.pt` |
| 모델 SHA-256 | `f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36` |
| labelImg 실행 파일 SHA-256 | `a288a77ed0d69afe9f9128ec02760702beb8e59665001afcd92ff308423cab83` |

### 자동 검사

- Ruff lint: 통과
- Ruff format check: 통과
- mypy: 18개 소스 파일, 오류 없음
- pytest: 57개 테스트 통과
- 실제 Ultralytics를 쓰지 않는 테스트에서는 가짜 모델로 `person` 선택, 다른 클래스 제외,
  confidence 전달, bbox clipping·YOLO 변환, 빈 결과와 런타임 오류 변환을 확인했다.
- CLI 7개 명령의 인자 계약, dispatch, 성공 JSON과 안정된 오류 종료 코드 `2`를 확인했다.

### 실제 YOLO 결과

| 항목 | 결과 |
| --- | --- |
| 입력 유형 | ImageGen으로 만든 합성 성인 장면을 MP4로 변환 |
| 합성 이미지 SHA-256 | `f932b6a972a37fa790addabf7871936338c7904852bd20f518c2955ef88c9801` |
| MP4 SHA-256 | `7e238ab382c3f1c809df23c72a558c78dbac0c4f6daf2bd7b6f375b396a30f89` |
| 추출 프레임 | 3 |
| 후보 생성 프레임 | 3 |
| 프레임별 후보 | `person` 1개 |
| confidence 범위 | 0.9213~0.9218 |
| 검수 대상 | 3 |
| 자동 승인 | 0 |

세 후보 라벨은 모두 class `0`이고 정규화 bbox 값이 `0~1` 범위 안에 있었다. 실행한
단계는 `prepare → prelabel --device cpu → prepare-review`이며 모든 명령이 종료 코드
`0`과 JSON 성공 상태를 반환했다.

### 합성 이미지 생성 프롬프트

```text
Use case: photorealistic-natural
Asset type: synthetic YOLO person-detection smoke-test frame
Primary request: create a clearly synthetic but photorealistic classroom scene containing exactly one fictional consenting adult standing upright, with the entire body visible from head to shoes
Scene/backdrop: modern empty classroom with desks pushed toward the edges, uncluttered neutral wall behind the person
Subject: one fictional adult in plain everyday clothing, natural anatomy, arms slightly away from the torso, no occlusion, no resemblance to a real or famous person
Style/medium: photorealistic synthetic image with realistic fabric and room textures
Composition/framing: landscape 16:9, eye-level camera, single person centered and occupying roughly 65 percent of image height, generous margin around the full body
Lighting/mood: bright even daylight, high contrast between subject and background, natural colors
Constraints: exactly one adult; full body fully visible; no children; no other people; no text; no logos; no watermark; no face close-up; no personal identifiers
Avoid: cropped limbs, seated pose, occlusion, crowds, mirrors, posters containing people, motion blur, dramatic shadows
```

### 연결된 웹캠 전체 검증

사용자 동의를 받은 성인 1명을 카메라 index `0`, DirectShow, 640×480, 요청 fps `30`으로
무음 촬영했다. MP4에는 444프레임, 30fps, 14.8초가 기록되었고 영상 SHA-256은
`d24f77da23fb37e9e03adc0e042eeb523d419416b625e2f2cca48a49be95a2c3`이다.
촬영물의 보존 만료일은 2026-09-17이다.

| 항목 | 결과 |
| --- | --- |
| 실행 단계 | `prepare → prelabel → prepare-review → review-complete → publish → validate` |
| 추출·검수 프레임 | 8 |
| YOLO `person` 후보가 있는 프레임 | 5 |
| 빈 라벨 프레임 | 3 |
| 자동 승인 | 0 |
| 검수 방식 | 전체 8프레임 labelImg 수동 검수, 저장·재열기 확인 |
| 품질 gate | 통과 (`full-or-required-review`, error rate 0) |
| 발행 버전·상태 | `person-v0001`, `pilot` |
| split | train 8, val 0, test 0 (단일 세션 유지) |
| 중복 제거 | 입력 8, 유지 8, 제거 0, 중복 그룹 0 |
| class | `0: person` |

검수 영수증에는 labelImg 실행 파일과 검수 파일의 SHA-256, GUI smoke 확인 사실이
기록되었다. 발행된 모든 이미지·라벨 해시와 split을 다시 검증했으며 `validate`가
`valid`를 반환했다. 영상, 얼굴 프레임, 라벨, 모델 및 데이터셋은 Git 제외 로컬 데이터로
유지한다.
