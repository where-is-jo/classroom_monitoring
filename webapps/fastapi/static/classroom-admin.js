// 강의실 생성·수정 화면(create.html/edit.html)에서 폼을 JSON API에 제출한다.
// 폼의 data 속성으로 API 경로·메서드·성공 시 이동할 경로·확인 문구를 정한다.
// - data-api-url: 제출할 API 경로
// - data-api-method: POST / PUT / DELETE
// - data-success-url: 성공 시 이동할 화면 경로
// - data-confirm: 삭제처럼 전송 전 확인이 필요한 문구
// 입력 검증 오류·중복 코드 같은 API 오류는 error envelope의 message를 화면에 보여준다.
// 통합 좌석 관리 화면(seats.html)에는 이 속성을 가진 폼이 없으므로 아래 좌석
// 컨트롤러와 겹치지 않는다.

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
        headers: { "Content-Type": "application/json" },
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

// 통합 좌석 관리 컨트롤러 (TASK-004)
// - 빈 칸 클릭: POST /seats/auto 자동 생성 (code 입력 없음, row/column만 전달)
// - 좌석 클릭: 편집 패널 표시 (이름 수정, 학생 지정/해제, 삭제)
// - 파괴적 동작(해제·삭제)은 확인 대화상자(영구 삭제·해제 명시) 후 수행한다.
// - 브라우저는 요청을 재시도하지 않는다. 성공·오류는 aria-live region으로 안내한다.
// - 저장(busy) 중에는 모든 인터랙티브 요소를 native disabled로 만든다.

(function seatAdminController() {
  const grid = document.getElementById("seat-grid");
  const panel = document.getElementById("seat-edit-panel");
  const form = document.getElementById("seat-edit-form");
  if (!grid || !panel || !form) return;

  const classroomId = grid.dataset.classroomId;
  if (!classroomId) return;

  const statusRegion = document.getElementById("seat-grid-status");
  const alertRegion = document.getElementById("seat-grid-alert");
  const addRow = document.getElementById("add-row");
  const addColumn = document.getElementById("add-column");
  const btnAssign = document.getElementById("btn-assign");
  const btnUnassign = document.getElementById("btn-unassign");
  const btnDelete = document.getElementById("btn-delete");

  let currentSeatId = null;
  let saving = false;
  let maxRow = Number(grid.dataset.maxRow) || 1;
  let maxColumn = Number(grid.dataset.maxColumn) || 1;
  // 패널을 연 시점의 실제 지정 학생 id(서버 상태). select 값은 저장 전에
  // 자유롭게 바뀔 수 있으므로 해제 버튼 활성화 판정에는 이 값을 쓴다.
  let assignedStudentId = "";
  let currentSeatAssignable = false;

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

  // --- busy(저장 중) ---------------------------------------------------------

  function setBusy(busy) {
    saving = busy;
    grid.setAttribute("aria-busy", busy ? "true" : "false");
    const controls = [
      ...grid.querySelectorAll("button"),
      ...panel.querySelectorAll("button"),
      ...form.querySelectorAll("input, select"),
      addRow,
      addColumn,
    ].filter(Boolean);
    for (const control of controls) {
      control.disabled = busy;
    }
    refreshPanelControls();
  }

  // 해제 버튼은 패널을 연 시점에 실제로 지정된 학생이 없으면 비활성화한다
  // (busy 중이면 함께 disabled). select에서 학생을 새로 고르기만 해도(저장
  // 전) 서버 상태는 그대로이므로 select 값이 아니라 assignedStudentId로 판정한다.
  function refreshPanelControls() {
    if (btnAssign) btnAssign.disabled = saving || !currentSeatAssignable;
    if (btnUnassign) btnUnassign.disabled = saving || !assignedStudentId;
  }

  function storeFocusTarget(id) {
    try {
      sessionStorage.setItem("seat-grid-focus", id);
    } catch {
      // 저장소를 쓸 수 없는 환경(새로고침 시 포커스 복원 불가)은 조용히 건너뛴다.
    }
  }

  // --- 편집 패널 열기/닫기 ---------------------------------------------------

  function openPanel(seatId, seatData) {
    currentSeatId = seatId;
    panel.dataset.seatId = seatId;
    panel.hidden = false;
    form.elements.label.value = seatData.label || "";
    form.elements.student_id.value = seatData.assignment_student_id || "";
    assignedStudentId = seatData.assignment_student_id || "";
    currentSeatAssignable = seatData.seat_assignable !== false;
    hideFormError();
    refreshPanelControls();
    announce("좌석 편집 패널을 열었습니다.");
  }

  function closePanel() {
    currentSeatId = null;
    panel.dataset.seatId = "";
    panel.hidden = true;
    assignedStudentId = "";
    currentSeatAssignable = false;
    hideFormError();
    refreshPanelControls();
  }

  function seatLabel() {
    return form.elements.label.value.trim() || "이 좌석";
  }

  // --- 빈 칸 클릭 → POST /seats/auto (재시도 없음) ---------------------------

  async function postAutoSeat(row, column) {
    setBusy(true);
    hideAlert();
    announce("좌석을 생성하는 중입니다.");
    try {
      const response = await fetch(
        `/api/v1/classrooms/${encodeURIComponent(classroomId)}/seats/auto`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ row, column }),
        }
      );
      if (response.ok) {
        let newSeatId = null;
        try {
          const body = await response.json();
          if (body && typeof body.id === "string") newSeatId = body.id;
        } catch {
          // id를 읽지 못하면 좌표 기반 셀 ID로 폴백한다.
        }
        announce("좌석이 생성되었습니다.");
        storeFocusTarget(newSeatId ? `seat-cell-${newSeatId}` : `seat-cell-${row}-${column}`);
        window.location.reload();
        return;
      }
      const message = await extractApiMessage(response);
      if (response.status === 409) {
        // 좌표·코드 충돌: 화면이 서버 상태와 다르므로 새로고침으로 동기화한다 (재시도 없음).
        showAlert(message || "이미 차지된 위치입니다. 화면을 새로고침합니다.");
        window.location.reload();
        return;
      }
      showAlert(message || "좌석 생성에 실패했습니다.");
    } catch {
      showAlert("서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  // --- click / keyboard(Enter·Space는 native button) -------------------------

  grid.addEventListener("click", (event) => {
    if (saving) return;
    const emptyCell = event.target.closest("button.seat-map__empty");
    if (emptyCell) {
      const row = Number(emptyCell.dataset.row);
      const column = Number(emptyCell.dataset.column);
      void postAutoSeat(row, column);
      return;
    }
    const seatButton = event.target.closest("button[data-seat-id]");
    if (!seatButton) return;
    const seatId = seatButton.dataset.seatId;
    if (!seatId) return;
    if (currentSeatId === seatId) {
      closePanel();
      announce("좌석 편집 패널을 닫았습니다.");
      return;
    }
    openPanel(seatId, {
      label: seatButton.querySelector("strong")?.textContent?.trim() || "",
      assignment_student_id: seatButton.dataset.assignmentStudentId || "",
      assignment_student_name: seatButton.dataset.assignmentStudentName || "",
      seat_assignable: seatButton.dataset.seatAssignable !== "false",
    });
  });

  // --- Escape: 패널 닫기 ------------------------------------------------------

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!currentSeatId) return;
    closePanel();
    announce("좌석 편집 패널을 닫았습니다.");
  });

  // --- 편집 패널 저장 (label만 전송) -------------------------------------------

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!currentSeatId || saving) return;

    const label = form.elements.label.value.trim();
    if (!label) {
      showFormError("좌석 이름을 입력해 주세요.");
      return;
    }

    setBusy(true);
    hideFormError();
    announce("좌석을 저장하는 중입니다.");

    try {
      const response = await fetch(
        `/api/v1/classrooms/${encodeURIComponent(classroomId)}/seats/${encodeURIComponent(currentSeatId)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ label }),
        }
      );
      if (response.ok) {
        announce("좌석이 저장되었습니다.");
        storeFocusTarget(`seat-cell-${currentSeatId}`);
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

  // --- 학생 지정 --------------------------------------------------------------

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
          `/api/v1/classrooms/${encodeURIComponent(classroomId)}/seats/${encodeURIComponent(currentSeatId)}/assignment`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ student_id: studentId }),
          }
        );
        if (response.ok) {
          announce("학생이 지정되었습니다.");
          storeFocusTarget(`seat-cell-${currentSeatId}`);
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

  // --- 학생 해제 (danger, 확인 후 DELETE, 재시도 없음) ---------------------------

  if (btnUnassign) {
    btnUnassign.addEventListener("click", async () => {
      if (!currentSeatId || saving) return;
      if (!window.confirm(`「${seatLabel()}」 좌석의 학생 지정을 영구 해제할까요?`)) return;

      setBusy(true);
      hideFormError();
      announce("지정을 해제하는 중입니다.");

      try {
        const response = await fetch(
          `/api/v1/classrooms/${encodeURIComponent(classroomId)}/seats/${encodeURIComponent(currentSeatId)}/assignment`,
          { method: "DELETE" }
        );
        if (response.ok) {
          announce("지정이 해제되었습니다.");
          storeFocusTarget(`seat-cell-${currentSeatId}`);
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

  // --- 좌석 삭제 (danger, 영구 삭제 확인 후 DELETE, 재시도 없음) -------------------

  if (btnDelete) {
    btnDelete.addEventListener("click", async () => {
      if (!currentSeatId || saving) return;
      if (
        !window.confirm(
          `「${seatLabel()}」 좌석과 학생 지정을 영구 삭제할까요? 이 작업은 되돌릴 수 없습니다.`
        )
      ) {
        return;
      }

      setBusy(true);
      hideFormError();
      announce("좌석을 삭제하는 중입니다.");

      try {
        const response = await fetch(
          `/api/v1/classrooms/${encodeURIComponent(classroomId)}/seats/${encodeURIComponent(currentSeatId)}`,
          { method: "DELETE" }
        );
        if (response.ok) {
          announce("좌석이 영구 삭제되었습니다.");
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

  // --- add row/column: browser-only (reload 뒤 서버 데이터로 재계산) --------------

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
    grid.style.gridTemplateColumns = `repeat(${maxColumn}, minmax(100px, 1fr))`;
    grid.style.gridTemplateRows = `repeat(${maxRow}, minmax(76px, 1fr))`;
  }

  if (addRow) {
    addRow.addEventListener("click", () => {
      if (saving) return;
      maxRow += 1;
      for (let column = 1; column <= maxColumn; column += 1) {
        grid.appendChild(createEmptyCell(maxRow, column));
      }
      updateGridStyle();
      hideAlert();
      announce("1행을 추가했습니다. 새로고침하면 격자 크기는 원래대로 돌아갑니다.");
    });
  }

  if (addColumn) {
    addColumn.addEventListener("click", () => {
      if (saving) return;
      maxColumn += 1;
      for (let row = 1; row <= maxRow; row += 1) {
        grid.appendChild(createEmptyCell(row, maxColumn));
      }
      updateGridStyle();
      hideAlert();
      announce("1열을 추가했습니다. 새로고침하면 격자 크기는 원래대로 돌아갑니다.");
    });
  }

  // --- 새로고침 후 focus 복원 (성공 시 sessionStorage에 기록한 대상) --------------

  let focusTarget = null;
  try {
    focusTarget = sessionStorage.getItem("seat-grid-focus");
  } catch {
    // sessionStorage를 쓸 수 없는 환경에서는 포커스 복원을 건너뛴다.
  }
  if (focusTarget) {
    try {
      sessionStorage.removeItem("seat-grid-focus");
    } catch {
      // 제거 실패는 무시한다 (다음 방문 시 복원 시도가 다시 일어날 뿐).
    }
    const target = document.getElementById(focusTarget);
    if (target) {
      target.focus();
      announce("좌석 저장이 완료되었습니다.");
    }
  }
})();
