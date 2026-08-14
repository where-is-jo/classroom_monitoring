// 강의실·좌석 관리 화면에서 폼을 JSON API에 제출한다.
// 폼의 data 속성으로 API 경로·메서드·성공 시 이동할 경로·확인 문구를 정한다.
// - data-api-url: 제출할 API 경로
// - data-api-method: POST / PUT / DELETE
// - data-success-url: 성공 시 이동할 화면 경로
// - data-confirm: 삭제처럼 전송 전 확인이 필요한 문구
// 입력 검증 오류·중복 코드 같은 API 오류는 error envelope의 message를 화면에 보여준다.

document.querySelectorAll("form[data-api-url]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = form.querySelector("[type='submit']");
    const errorBox = form.querySelector("[data-form-error]");

    if (errorBox) {
      errorBox.textContent = "";
      errorBox.hidden = true;
    }
    const showError = (message) => {
      if (!errorBox) return;
      errorBox.textContent = message;
      errorBox.hidden = false;
    };

    const payload = collectPayload(form);
    const method = (form.dataset.apiMethod || "POST").toUpperCase();
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      return;
    }

    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = true;
    }
    try {
      const response = await fetch(form.dataset.apiUrl, {
        method,
        headers: {"Content-Type": "application/json"},
        body: method === "GET" ? undefined : JSON.stringify(payload),
      });
      if (response.ok) {
        window.location.href = form.dataset.successUrl || "/classrooms";
        return;
      }
      let message = "요청을 처리하지 못했습니다.";
      try {
        const body = await response.json();
        if (body && typeof body.error === "object" && body.error !== null) {
          const apiMessage = body.error.message;
          if (typeof apiMessage === "string" && apiMessage) {
            message = apiMessage;
          }
        }
      } catch {
        // 응답이 JSON이 아니면 기본 메시지를 유지한다.
      }
      showError(message);
    } catch {
      showError("서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      if (submitButton instanceof HTMLButtonElement) {
        submitButton.disabled = false;
      }
    }
  });
});

// 폼의 name 속성으로 JSON payload를 만든다. row·column 같은 number 입력은
// 빈 값이면 null로, 값이 있으면 숫자로 변환한다.
function collectPayload(form) {
  const data = {};

  for (const field of form.elements) {
    if (!field.name || field.disabled) continue;
    const type = field.type;
    if (type === "submit" || type === "button") continue;

    if (type === "checkbox") {
      data[field.name] = field.checked;
      continue;
    }
    if (type === "radio") {
      if (field.checked) data[field.name] = field.value;
      continue;
    }
    if (type === "hidden") {
      data[field.name] = field.value;
      continue;
    }

    const value = field.value.trim();
    if (type === "number") {
      data[field.name] = value === "" ? null : Number(value);
      continue;
    }
    data[field.name] = value;
  }
  return data;
}

// ============================================================
// 좌석 격자 컨트롤러 (TASK-005, 클릭 우선·드래그 보조)
// ============================================================
// - 빈 칸: idle이면 "먼저 좌석을 선택" 안내만, source 선택 후에는 place/move PUT.
// - 배치된 칸(occupied): idle이면 source 선택, source 재선택은 cancel,
//   다른 occupied는 aria-disabled로 focusable 유지하되 handler가 block/announce.
// - tray 좌석: 항상 source로 선택/교체 가능.
// - unplace(배치 해제)는 격자 밖 sibling 버튼이며 확인 후 null pair PUT.
// - 저장 중에는 모든 action control을 native disabled로 만들고 selection을 보존한다.
// - add row/column은 browser-only라 reload 뒤에는 서버 데이터로 다시 계산된다.

(function seatGridController() {
  const grid = document.getElementById("seat-grid");
  if (!grid) return;

  const classroomId = grid.dataset.classroomId;
  if (!classroomId) return;

  const statusRegion = document.getElementById("seat-grid-status");
  const alertRegion = document.getElementById("seat-grid-alert");
  const addRow = document.getElementById("add-row");
  const addColumn = document.getElementById("add-column");
  const tray = document.getElementById("seat-tray");

  const state = {
    selectedSeatId: null,
    selectedButton: null,
    saving: false,
    maxRow: Number(grid.dataset.maxRow) || 1,
    maxColumn: Number(grid.dataset.maxColumn) || 1,
  };

  let dragSeatId = null;

  // 저장(busy) 중 native disabled로 만들 action control 모음.
  const actionControls = [
    ...grid.querySelectorAll("button"),
    ...Array.from(document.querySelectorAll("#seat-tray button")),
    ...Array.from(document.querySelectorAll(".seat-unplace [data-seat-id]")),
    addRow,
    addColumn,
  ].filter(Boolean);

  // --- 안내(live region) helpers -------------------------------------------

  function announce(message) {
    if (!statusRegion) return;
    statusRegion.textContent = "";
    // 같은 문구를 반복해도 스크린 리더가 다시 읽도록 reflow를 강제한다.
    void statusRegion.offsetWidth;
    statusRegion.textContent = message;
  }

  function showAlert(message) {
    if (!alertRegion) return;
    alertRegion.textContent = message;
    alertRegion.hidden = false;
  }

  function hideAlert() {
    if (!alertRegion) return;
    alertRegion.hidden = true;
  }

  function seatLabel(button) {
    const strong = button.querySelector("strong");
    if (strong && strong.textContent.trim()) return strong.textContent.trim();
    return button.textContent.trim();
  }

  // --- selection 관리 -------------------------------------------------------

  function selectSource(button, seatId, options = {}) {
    clearSelection();
    state.selectedSeatId = seatId;
    state.selectedButton = button;
    button.setAttribute("aria-pressed", "true");
    button.classList.add("is-selected-source");
    for (const cell of grid.querySelectorAll("button[data-seat-id]")) {
      if (cell.dataset.seatId !== seatId) {
        cell.setAttribute("aria-disabled", "true");
        cell.classList.add("is-blocked");
      }
    }
    hideAlert();
    if (!options.silent) {
      announce(
        `「${seatLabel(button)}」 좌석을 선택했습니다. 빈 칸을 선택하면 배치하고, 같은 좌석을 다시 선택하면 취소합니다.`
      );
    }
  }

  function clearSelection() {
    if (state.selectedButton) {
      state.selectedButton.removeAttribute("aria-pressed");
      state.selectedButton.classList.remove("is-selected-source");
    }
    for (const cell of grid.querySelectorAll("button[data-seat-id]")) {
      cell.removeAttribute("aria-disabled");
      cell.classList.remove("is-blocked");
    }
    state.selectedSeatId = null;
    state.selectedButton = null;
  }

  function focusSource() {
    if (state.selectedButton && typeof state.selectedButton.focus === "function") {
      state.selectedButton.focus();
    }
  }

  // --- busy(저장 중) --------------------------------------------------------

  function setBusy(busy) {
    state.saving = busy;
    grid.setAttribute("aria-busy", busy ? "true" : "false");
    for (const control of actionControls) {
      control.disabled = busy;
    }
  }

  // --- PUT ------------------------------------------------------------------

  // response는 최대 한 번만 읽을 수 있으므로 message 추출과 상태 판정을 함께 한다.
  async function startPut(seatId, target) {
    setBusy(true);
    hideAlert();
    announce("좌석을 저장하는 중입니다.");
    try {
      const response = await fetch(
        `/api/v1/classrooms/${encodeURIComponent(classroomId)}/seats/${encodeURIComponent(seatId)}`,
        {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(target),
        }
      );
      if (response.ok) {
        announce("저장되었습니다.");
        // 새로고침 뒤 new cell(place/move) 또는 tray source(unplace)에 focus를 복원한다.
        const focusId =
          target.row === null && target.column === null
            ? `tray-seat-${seatId}`
            : `seat-cell-${seatId}`;
        sessionStorage.setItem("seat-grid-focus", focusId);
        window.location.reload();
        return;
      }
      const message = await extractApiMessage(response);
      if (response.status === 409) {
        clearSelection();
        showAlert(message || "이미 다른 좌석이 배치된 위치입니다. 화면을 새로고침합니다.");
        window.location.reload();
        return;
      }
      if (response.status === 404) {
        clearSelection();
        showAlert(message || "좌석을 찾을 수 없습니다. 화면을 새로고침합니다.");
        window.location.reload();
        return;
      }
      if (response.status === 422) {
        showAlert(message || "입력값이 올바르지 않습니다. 좌석 선택을 유지합니다.");
        focusSource();
        return;
      }
      showAlert(message || "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.");
      focusSource();
    } catch {
      showAlert("서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
      focusSource();
    } finally {
      setBusy(false);
    }
  }

  async function extractApiMessage(response) {
    try {
      const body = await response.json();
      if (body && typeof body.error === "object" && body.error !== null) {
        const apiMessage = body.error.message;
        if (typeof apiMessage === "string" && apiMessage) return apiMessage;
      }
    } catch {
      // JSON이 아니면 기본 메시지를 사용한다.
    }
    return "";
  }

  // --- click / keyboard(Enter·Space는 native button) -------------------------

  grid.addEventListener("click", (event) => {
    if (state.saving) return;
    const button = event.target.closest("button[data-row]");
    if (!button) return;
    if (button.getAttribute("aria-disabled") === "true") {
      announce("이미 배치된 좌석입니다. 빈 칸을 선택해 주세요.");
      return;
    }
    const seatId = button.dataset.seatId || null;
    if (seatId) {
      // 배치된 칸: idle이면 source 선택, 같은 source는 cancel.
      if (!state.selectedSeatId) {
        selectSource(button, seatId);
        return;
      }
      if (state.selectedSeatId === seatId) {
        clearSelection();
        announce("좌석 선택을 취소했습니다.");
        return;
      }
      // 다른 occupied는 aria-disabled로 막혀 있지만 방어적으로 한 번 더 막는다.
      announce("이미 배치된 좌석입니다. 빈 칸을 선택해 주세요.");
      return;
    }
    // 빈 칸: idle이면 select-first 안내만, source 선택 후에는 place/move PUT.
    const row = Number(button.dataset.row);
    const column = Number(button.dataset.column);
    if (!state.selectedSeatId) {
      announce("먼저 배치 대기 좌석이나 배치된 좌석을 선택해 주세요.");
      return;
    }
    startPut(state.selectedSeatId, { row, column });
  });

  if (tray) {
    tray.addEventListener("click", (event) => {
      if (state.saving) return;
      const button = event.target.closest("button[data-seat-id]");
      if (!button) return;
      const seatId = button.dataset.seatId;
      if (state.selectedSeatId === seatId) {
        clearSelection();
        announce("좌석 선택을 취소했습니다.");
        return;
      }
      // 다른 tray 좌석은 source를 교체한다.
      selectSource(button, seatId);
    });
  }

  // --- Escape: selection을 clear하고 직전 source focus를 복원 -----------------

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!state.selectedSeatId) return;
    const source = state.selectedButton;
    clearSelection();
    if (source) source.focus();
    announce("좌석 선택을 취소했습니다.");
  });

  // --- unplace(배치 해제): 격자 밖 sibling 버튼, 확인 후 null pair PUT ----------

  document.querySelectorAll(".seat-unplace [data-seat-id]").forEach((button) => {
    button.addEventListener("click", () => {
      if (state.saving) return;
      const seatId = button.dataset.seatId;
      const label = button.textContent.replace(/^배치 해제\s*·\s*/, "").trim();
      if (!window.confirm(`「${label}」 좌석을 배치에서 해제할까요?`)) return;
      startPut(seatId, { row: null, column: null });
    });
  });

  // --- add row/column: browser-only (reload 뒤 서버 데이터로 재계산) ------------

  function createEmptyCell(row, column) {
    const button = document.createElement("button");
    button.type = "button";
    button.id = `seat-cell-${row}-${column}`;
    button.className = "seat-map__empty";
    button.dataset.row = String(row);
    button.dataset.column = String(column);
    button.style.gridRow = String(row);
    button.style.gridColumn = String(column);
    button.setAttribute("aria-label", `${row}행 ${column}열 · 빈 칸`);
    return button;
  }

  function updateGridStyle() {
    grid.style.gridTemplateColumns = `repeat(${state.maxColumn}, minmax(100px, 1fr))`;
    grid.style.gridTemplateRows = `repeat(${state.maxRow}, minmax(76px, 1fr))`;
  }

  addRow.addEventListener("click", () => {
    if (state.saving) return;
    state.maxRow += 1;
    for (let column = 1; column <= state.maxColumn; column += 1) {
      grid.appendChild(createEmptyCell(state.maxRow, column));
    }
    updateGridStyle();
    hideAlert();
    announce("1행을 추가했습니다. 새로고침하면 격자 크기는 원래대로 돌아갑니다.");
  });

  addColumn.addEventListener("click", () => {
    if (state.saving) return;
    state.maxColumn += 1;
    for (let row = 1; row <= state.maxRow; row += 1) {
      grid.appendChild(createEmptyCell(row, state.maxColumn));
    }
    updateGridStyle();
    hideAlert();
    announce("1열을 추가했습니다. 새로고침하면 격자 크기는 원래대로 돌아갑니다.");
  });

  // --- drag: idle positioned source에서만 시작, occupied/outside는 no PUT --------

  grid.addEventListener("dragstart", (event) => {
    if (state.saving) return;
    const button = event.target.closest("button[data-seat-id]");
    if (!button) return;
    if (state.selectedSeatId) {
      // source가 이미 선택된 상태에서는 drag를 시작하지 않는다 (idle만 허용).
      event.preventDefault();
      return;
    }
    dragSeatId = button.dataset.seatId;
    // 드래그 중에는 해당 좌석을 source로 취급한다 (occupied target 차단 상태).
    selectSource(button, dragSeatId, {silent: true});
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", dragSeatId);
  });

  grid.addEventListener("dragover", (event) => {
    if (state.saving || !dragSeatId) return;
    const button = event.target.closest("button[data-row]");
    if (!button) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  });

  grid.addEventListener("drop", (event) => {
    if (state.saving || !dragSeatId) return;
    event.preventDefault();
    const button = event.target.closest("button[data-row]");
    if (!button) return;
    if (button.dataset.seatId) {
      announce("이미 배치된 좌석입니다. 빈 칸에만 배치할 수 있습니다.");
      return;
    }
    const row = Number(button.dataset.row);
    const column = Number(button.dataset.column);
    startPut(dragSeatId, { row, column });
  });

  document.addEventListener("dragover", (event) => {
    if (state.saving || !dragSeatId) return;
    if (event.target.closest("#seat-grid")) return;
    // 격자 밖 drop 이벤트를 받기 위해 기본 동작(드롭 금지)을 막는다.
    event.preventDefault();
    event.dataTransfer.dropEffect = "none";
  });

  document.addEventListener("drop", (event) => {
    if (state.saving || !dragSeatId) return;
    if (event.target.closest("#seat-grid")) return;
    event.preventDefault();
    // outside-drop unplace는 없다. 안내만 하고 request를 보내지 않는다.
    announce("좌석은 격자의 빈 칸에만 배치할 수 있습니다.");
  });

  grid.addEventListener("dragend", () => {
    dragSeatId = null;
    if (!state.saving) {
      clearSelection();
    }
  });

  // --- 새로고침 후 focus 복원 (성공 시 sessionStorage에 기록한 대상) --------------

  const focusTarget = sessionStorage.getItem("seat-grid-focus");
  if (focusTarget) {
    sessionStorage.removeItem("seat-grid-focus");
    const target = document.getElementById(focusTarget);
    if (target) {
      target.focus();
      announce("좌석 저장이 완료되었습니다.");
    }
  }
})();


// ============================================================
// 통합 좌석 관리 컨트롤러 (T5)
// ============================================================
// - 빈 칸 클릭: 좌석 생성 (code, label 입력)
// - 좌석 클릭: 편집 패널 표시 (code, label, is_active, 학생 지정)
// - 편집 패널: 저장, 지정/해제, 삭제

(function seatEditController() {
  const grid = document.getElementById("seat-grid");
  const panel = document.getElementById("seat-edit-panel");
  const form = document.getElementById("seat-edit-form");
  if (!grid || !panel || !form) return;

  const classroomId = grid.dataset.classroomId;
  if (!classroomId) return;

  const statusRegion = document.getElementById("seat-grid-status");
  const alertRegion = document.getElementById("seat-grid-alert");

  const btnAssign = document.getElementById("btn-assign");
  const btnUnassign = document.getElementById("btn-unassign");
  const btnDelete = document.getElementById("btn-delete");

  let currentSeatId = null;
  let saving = false;

  // --- helpers ---------------------------------------------------------------

  function announce(message) {
    if (!statusRegion) return;
    statusRegion.textContent = "";
    void statusRegion.offsetWidth;
    statusRegion.textContent = message;
  }

  function showAlert(message) {
    if (!alertRegion) return;
    alertRegion.textContent = message;
    alertRegion.hidden = false;
  }

  function hideAlert() {
    if (!alertRegion) return;
    alertRegion.hidden = true;
  }

  function showFormError(message) {
    const errorBox = form.querySelector("[data-form-error]");
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function hideFormError() {
    const errorBox = form.querySelector("[data-form-error]");
    if (!errorBox) return;
    errorBox.textContent = "";
    errorBox.hidden = true;
  }

  async function extractApiMessage(response) {
    try {
      const body = await response.json();
      if (body && typeof body.error === "object" && body.error !== null) {
        const apiMessage = body.error.message;
        if (typeof apiMessage === "string" && apiMessage) return apiMessage;
      }
    } catch {
      // JSON이 아니면 기본 메시지를 사용한다.
    }
    return "";
  }

  function setBusy(busy) {
    saving = busy;
    const buttons = form.querySelectorAll("button");
    buttons.forEach((btn) => { btn.disabled = busy; });
  }

  // --- 편집 패널 열기/닫기 ---------------------------------------------------

  function openPanel(seatId, seatData) {
    currentSeatId = seatId;
    panel.dataset.seatId = seatId;
    panel.hidden = false;

    // 폼 필드 채우기
    form.elements.code.value = seatData.code || "";
    form.elements.label.value = seatData.label || "";
    form.elements.is_active.checked = seatData.is_active !== false;

    // 학생 지정 정보
    const studentSelect = form.elements.student_id;
    if (seatData.assignment_student_id) {
      studentSelect.value = seatData.assignment_student_id;
    } else {
      studentSelect.value = "";
    }

    hideFormError();
    announce("좌석 편집 패널을 열었습니다.");
  }

  function closePanel() {
    currentSeatId = null;
    panel.dataset.seatId = "";
    panel.hidden = true;
    hideFormError();
  }

  // --- 빈 칸 클릭 → 좌석 생성 ------------------------------------------------

  grid.addEventListener("click", async (event) => {
    if (saving) return;
    const button = event.target.closest("button.seat-map__empty");
    if (!button) return;

    const row = Number(button.dataset.row);
    const column = Number(button.dataset.column);

    // code와 label 입력 받기
    const code = prompt("좌석 코드를 입력하세요 (예: S01):");
    if (!code || !code.trim()) return;

    const label = prompt("좌석 이름을 입력하세요 (예: 좌석 1):");
    if (!label || !label.trim()) return;

    setBusy(true);
    hideAlert();
    announce("좌석을 생성하는 중입니다.");

    try {
      const response = await fetch(
        "/api/v1/classrooms/" + encodeURIComponent(classroomId) + "/seats",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            code: code.trim(),
            label: label.trim(),
            row: row,
            column: column,
          }),
        }
      );

      if (response.ok) {
        announce("좌석이 생성되었습니다.");
        sessionStorage.setItem("seat-grid-focus", "seat-cell-" + row + "-" + column);
        window.location.reload();
        return;
      }

      const message = await extractApiMessage(response);
      showAlert(message || "좌석 생성에 실패했습니다.");
    } catch {
      showAlert("서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  });

  // --- 좌석 클릭 → 편집 패널 표시 --------------------------------------------

  grid.addEventListener("click", (event) => {
    if (saving) return;
    const button = event.target.closest("button[data-seat-id]");
    if (!button) return;

    const seatId = button.dataset.seatId;
    if (!seatId) return;

    // 이미 선택된 좌석이면 패널 닫기
    if (currentSeatId === seatId) {
      closePanel();
      announce("좌석 편집 패널을 닫았습니다.");
      return;
    }

    // 좌석 데이터 추출
    const seatData = {
      code: button.querySelector("small")?.textContent?.trim() || "",
      label: button.querySelector("strong")?.textContent?.trim() || "",
      is_active: true, // 기본값, 실제로는 서버에서 조회해야 함
      assignment_student_id: button.dataset.assignmentStudentId || "",
      assignment_student_name: button.dataset.assignmentStudentName || "",
    };

    openPanel(seatId, seatData);
  });

  // --- 편집 패널 저장 --------------------------------------------------------

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!currentSeatId || saving) return;

    const payload = {};
    const code = form.elements.code.value.trim();
    const label = form.elements.label.value.trim();
    const is_active = form.elements.is_active.checked;

    if (code) payload.code = code;
    if (label) payload.label = label;
    payload.is_active = is_active;

    setBusy(true);
    hideFormError();
    announce("좌석을 저장하는 중입니다.");

    try {
      const response = await fetch(
        "/api/v1/classrooms/" + encodeURIComponent(classroomId) + "/seats/" + encodeURIComponent(currentSeatId),
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );

      if (response.ok) {
        announce("좌석이 저장되었습니다.");
        sessionStorage.setItem("seat-grid-focus", "seat-cell-" + currentSeatId);
        window.location.reload();
        return;
      }

      const message = await extractApiMessage(response);
      showFormError(message || "저장에 실패했습니다.");
    } catch {
      showFormError("서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  });

  // --- 학생 지정 -------------------------------------------------------------

  if (btnAssign) {
    btnAssign.addEventListener("click", async () => {
      if (!currentSeatId || saving) return;

      const studentId = form.elements.student_id.value;
      if (!studentId) {
        showFormError("학생을 선택해 주세요.");
        return;
      }

      setBusy(true);
      hideFormError();
      announce("학생을 지정하는 중입니다.");

      try {
        const response = await fetch(
          "/api/v1/classrooms/" + encodeURIComponent(classroomId) + "/seats/" + encodeURIComponent(currentSeatId) + "/assignment",
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ student_id: studentId }),
          }
        );

        if (response.ok) {
          announce("학생이 지정되었습니다.");
          sessionStorage.setItem("seat-grid-focus", "seat-cell-" + currentSeatId);
          window.location.reload();
          return;
        }

        const message = await extractApiMessage(response);
        showFormError(message || "지정에 실패했습니다.");
      } catch {
        showFormError("서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
      } finally {
        setBusy(false);
      }
    });
  }

  // --- 학생 해제 -------------------------------------------------------------

  if (btnUnassign) {
    btnUnassign.addEventListener("click", async () => {
      if (!currentSeatId || saving) return;

      if (!window.confirm("학생 지정을 해제할까요?")) return;

      setBusy(true);
      hideFormError();
      announce("지정을 해제하는 중입니다.");

      try {
        const response = await fetch(
          "/api/v1/classrooms/" + encodeURIComponent(classroomId) + "/seats/" + encodeURIComponent(currentSeatId) + "/assignment",
          {
            method: "DELETE",
          }
        );

        if (response.ok) {
          announce("지정이 해제되었습니다.");
          sessionStorage.setItem("seat-grid-focus", "seat-cell-" + currentSeatId);
          window.location.reload();
          return;
        }

        const message = await extractApiMessage(response);
        showFormError(message || "해제에 실패했습니다.");
      } catch {
        showFormError("서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
      } finally {
        setBusy(false);
      }
    });
  }

  // --- 좌석 삭제 -------------------------------------------------------------

  if (btnDelete) {
    btnDelete.addEventListener("click", async () => {
      if (!currentSeatId || saving) return;

      const label = form.elements.label.value.trim() || "이 좌석";
      if (!window.confirm("'" + label + "'을(를) 삭제할까요?")) return;

      setBusy(true);
      hideFormError();
      announce("좌석을 삭제하는 중입니다.");

      try {
        const response = await fetch(
          "/api/v1/classrooms/" + encodeURIComponent(classroomId) + "/seats/" + encodeURIComponent(currentSeatId),
          {
            method: "DELETE",
          }
        );

        if (response.ok) {
          announce("좌석이 삭제되었습니다.");
          closePanel();
          window.location.reload();
          return;
        }

        const message = await extractApiMessage(response);
        showFormError(message || "삭제에 실패했습니다.");
      } catch {
        showFormError("서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
      } finally {
        setBusy(false);
      }
    });
  }

  // --- Escape: 패널 닫기 -----------------------------------------------------

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!currentSeatId) return;
    closePanel();
    announce("좌석 편집 패널을 닫았습니다.");
  });
})();