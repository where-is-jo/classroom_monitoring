/* 자연어 탐지 검색 결과의 쪽 나누기와 상세 모달.
 *
 * 여기서 하는 일은 **이미 그려진 것을 보이고 감추는 것뿐이다.** 문장을 만들거나
 * 값을 해석하지 않는다 — 시각 표기·강의실 이름·식별 문구는 서버가 템플릿에서
 * 끝냈고, 이 스크립트는 그 조각(template 요소)을 모달로 옮기기만 한다. 여기서
 * 문자열을 조립하기 시작하면 같은 표기 규칙이 Jinja2와 JS 두 벌이 된다.
 *
 * 스크립트가 실행되지 않아도 화면은 쓸 수 있다. 격자는 서버가 받아 온 건수를 그대로
 * 그려 두었고 쪽 이동 막대는 hidden인 채로 남는다. 다만 칸을 눌러도 아무 일도
 * 일어나지 않는다.
 */
(() => {
  const grid = document.querySelector("#shot-grid");
  if (!(grid instanceof HTMLElement)) return;

  const items = Array.from(grid.querySelectorAll(".shot-grid__item"));
  if (items.length === 0) return;

  /* ── 쪽 나누기 ───────────────────────────────────────── */
  const pager = document.querySelector("#shot-pager");
  const parsedSize = Number.parseInt(grid.dataset.pageSize ?? "", 10);
  // data-page-size가 없거나 이상하면 한 쪽에 다 놓는다. 0이나 음수를 그대로 쓰면
  // 쪽 수가 무한이 되거나 나눗셈이 깨진다.
  const pageSize = Number.isFinite(parsedSize) && parsedSize > 0 ? parsedSize : items.length;
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  let currentPage = 1;

  const prevButton = pager?.querySelector('[data-pager="prev"]');
  const nextButton = pager?.querySelector('[data-pager="next"]');
  const currentLabel = pager?.querySelector('[data-pager="current"]');
  const totalLabel = pager?.querySelector('[data-pager="total"]');

  const showPage = (page) => {
    currentPage = Math.min(Math.max(page, 1), totalPages);
    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    items.forEach((item, index) => {
      item.hidden = index < start || index >= end;
    });
    if (currentLabel instanceof HTMLElement) currentLabel.textContent = String(currentPage);
    if (prevButton instanceof HTMLButtonElement) prevButton.disabled = currentPage === 1;
    if (nextButton instanceof HTMLButtonElement) nextButton.disabled = currentPage === totalPages;
  };

  if (totalPages > 1 && pager instanceof HTMLElement) {
    if (totalLabel instanceof HTMLElement) totalLabel.textContent = String(totalPages);
    pager.hidden = false;
    prevButton?.addEventListener("click", () => {
      showPage(currentPage - 1);
      // 쪽을 넘기면 첫 칸으로 눈이 돌아가야 한다. 아래에 머무르면 새 쪽의 마지막
      // 줄만 보이고 위쪽 칸을 못 본 채 다시 넘기게 된다.
      grid.scrollIntoView({ block: "start", behavior: "smooth" });
    });
    nextButton?.addEventListener("click", () => {
      showPage(currentPage + 1);
      grid.scrollIntoView({ block: "start", behavior: "smooth" });
    });
  }

  // 쪽이 하나뿐이어도 실행한다. 버튼 상태를 맞추는 것 말고도 hidden을 명시적으로
  // 지워 두어야, 이전 상태가 남은 채로 되돌아오는 경우가 없다.
  showPage(1);

  /* ── 상세 모달 ───────────────────────────────────────── */
  const modal = document.querySelector("#shot-modal");
  const body = modal?.querySelector("[data-shot-body]");
  // showModal이 없는 브라우저에서 열면 모달이 아니라 그냥 문서 위에 얹힌 상자가
  // 되어 뒤 화면을 가리지도 못하고 닫을 수도 없다. 그럴 바에는 칸을 눌러도 아무
  // 일이 없는 편이 낫다 — 정보는 어차피 격자에 시각과 인원으로 남아 있다.
  if (!(modal instanceof HTMLDialogElement) || typeof modal.showModal !== "function") return;
  if (!(body instanceof HTMLElement)) return;

  const closeModal = () => {
    if (modal.open) modal.close();
  };

  grid.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const trigger = event.target.closest("[data-shot-open]");
    if (!(trigger instanceof HTMLElement)) return;
    const detail = trigger.parentElement?.querySelector("[data-shot-detail]");
    if (!(detail instanceof HTMLTemplateElement)) return;

    body.replaceChildren(detail.content.cloneNode(true));
    modal.showModal();
  });

  modal.querySelector("[data-shot-close]")?.addEventListener("click", closeModal);

  // 바깥을 눌러 닫는다. dialog는 backdrop을 눌러도 dialog 자신이 대상이 되므로
  // 대상이 modal 그 자체일 때만 닫는다. 안쪽을 눌렀을 때는 자식 요소가 대상이다.
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal();
  });

  // 닫을 때 비운다. 큰 이미지를 100장 열어 두면 그만큼 메모리에 남는다.
  // ESC로 닫는 경우까지 덮으려면 click이 아니라 close를 들어야 한다.
  modal.addEventListener("close", () => {
    body.replaceChildren();
  });
})();
