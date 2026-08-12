# deeplearning/training

공용 GPU 서버에 SSH로 접속해 학습 데이터를 올리고, 팀원이 Jupyter 노트북을 셀 단위로
실행하며 **사람 탐지(Person Detection) 모델**을 학습하는 절차다.

> **범위**: 사람 탐지 모델의 수동 fine-tuning까지만 다룬다. 얼굴 탐지·인식 모델 학습,
> 자동 재학습 파이프라인, 데이터셋 라벨링 도구는 범위 밖이다
> ([결정 0012](../../docs/architecture/decisions.md#0012--deeplearning에-모델-학습용-jupyter-노트북-도구를-둔다)).
> 얼굴 데이터는 개인정보 합의 전까지 이 노트북에서도 다루지 않는다.

## 준비물

- 공용 GPU 서버 SSH 접속 정보(개인 계정). **저장소에 접속 정보를 남기지 않는다** —
  팀 내부 채널로 개별 공유한다.
- YOLO 포맷으로 라벨링된 학습 데이터셋(`images/`, `labels/`, `data.yaml`).
  데이터셋을 어떻게 확보·라벨링할지는 아직 `결정 필요`다 — 이 노트북은 이미 만들어진
  데이터셋이 있다고 가정한다.

## 사용법

1. **SSH로 공용 서버에 접속한다.**

   ```bash
   ssh <계정>@<공용-서버-주소>
   ```

2. **저장소를 받고 이 디렉터리로 이동한다.**

   ```bash
   git clone <저장소-주소>
   cd classroom_monitoring/deeplearning/training
   ```

   이미 clone된 저장소가 있다면 `git pull`로 최신화한다.

3. **가상환경을 만들고 패키지를 설치한다.**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   서버에 CUDA용 torch가 이미 설치돼 있다면 `pip install ultralytics --no-deps`처럼
   torch 재설치를 피하는 방법도 있다. `requirements.txt`의 주석을 참고한다.

4. **설정을 채운다.**

   ```bash
   cp .env.example .env
   ```

   `DATASET_DIR`, `DEVICE` 등을 실제 값으로 채운다. 값의 의미는 `.env.example`의
   주석에 있다.

5. **학습 데이터셋을 서버로 옮긴다.** 로컬 PC에서:

   ```bash
   rsync -avz ./내-데이터셋/ <계정>@<공용-서버-주소>:<DATASET_DIR>/
   ```

   `data.yaml`의 `train`·`val` 경로가 실제 이미지 위치를 가리키는지 확인한다.

6. **Jupyter Lab을 서버에서 실행한다.**

   ```bash
   jupyter lab --no-browser --port=8888
   ```

7. **로컬 PC에서 SSH 포트포워딩으로 접속한다.** 새 터미널에서:

   ```bash
   ssh -L 8888:localhost:8888 <계정>@<공용-서버-주소>
   ```

   브라우저에서 서버가 출력한 `http://localhost:8888/lab?token=...` 주소를 연다.

8. **`notebooks/01_person_detection_training.ipynb`을 열고 위에서부터 셀을 하나씩
   순서대로 실행한다.** 각 셀은 이전 셀의 결과에 의존한다.

## 주의할 점

- **공용 GPU를 여러 명이 나눠 쓴다.** 학습을 시작하기 전에 노트북의 GPU 확인 셀로
  `nvidia-smi` 결과를 보고, 이미 쓰는 사람이 있으면 팀 채널에 먼저 확인한다.
- **디스크 여유가 크지 않다.** 공용 서버의 가용 용량이 약 17~20 GB뿐이다
  ([결정 0011](../../docs/architecture/decisions.md#0011--영상-원본을-저장하지-않고-스냅샷만-남긴다)).
  학습이 끝나면 노트북의 정리 셀로 데이터셋과 `runs/`를 지운다. 큰 데이터셋을
  서버에 영구히 두지 않는다.
- **데이터셋·가중치·`runs/`는 커밋하지 않는다.** 저장소의 `.gitignore`가 이 디렉터리
  아래 `data/`, `runs/`, `weights/`와 모델 가중치 확장자를 막는다.
- **학습된 가중치를 운영 추론 환경(`worker/inference`)까지 어떻게 전달할지는 아직
  정해지지 않았다.** 지금은 각자 로컬에 내려받아 필요한 사람과 직접 공유한다.

## 파일 구성

```text
training/
├── README.md          이 문서
├── requirements.txt    학습 노트북 의존성
├── .env.example         설정값 이름(값은 비움)
└── notebooks/
    └── 01_person_detection_training.ipynb   사람 탐지 모델 학습 노트북
```

## 남은 일

- 학습 데이터셋 확보·라벨링 정책 확정.
- 학습 가중치를 `worker/inference`가 쓰는 실행 환경까지 전달하는 방식 확정
  (MinIO가 후보다).
- 얼굴 탐지·인식 모델 학습 노트북 추가 — 개인정보 합의 이후.
- 공용 서버에서 GPU 동시 사용을 조율하는 절차(예약, 알림) 마련.

## 관련 문서

- [deeplearning README](../README.md)
- [결정 0012](../../docs/architecture/decisions.md#0012--deeplearning에-모델-학습용-jupyter-노트북-도구를-둔다)
- [결정 0011](../../docs/architecture/decisions.md#0011--영상-원본을-저장하지-않고-스냅샷만-남긴다) — 공용 서버 용량 제약의 근거
- [환경변수 규칙](../../docs/conventions/environment-convention.md)
