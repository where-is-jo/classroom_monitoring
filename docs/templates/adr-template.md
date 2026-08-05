# ADR 템플릿

ADR 템플릿은 이 디렉터리가 아니라 ADR이 실제로 쌓이는 곳에 있다.

**→ [docs/architecture/decisions/ADR-0001-template.md](../architecture/decisions/ADR-0001-template.md)**

## 왜 여기 두지 않았나

ADR은 `docs/architecture/decisions/` 안에서 번호 순으로 관리된다.
템플릿을 이 디렉터리에도 복사해 두면 두 파일이 갈라지고, 어느 쪽이 최신인지
알 수 없게 된다. 그래서 정본은 한 곳에만 두고 여기서는 가리키기만 한다.

## 쓰는 법

1. [`ADR-0001-template.md`](../architecture/decisions/ADR-0001-template.md)를 같은 디렉터리에 복사한다.
2. `ADR-NNNN-<결정-요약>.md` 형식으로 이름을 바꾼다.
3. 안내 문구를 지우고 채운다.

언제 ADR을 쓰는지, 번호와 상태를 어떻게 관리하는지는
[decisions/README.md](../architecture/decisions/README.md)에 있다.
