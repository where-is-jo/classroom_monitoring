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
    if (payload === null) {
      showError("배치도 위치는 4개 값을 모두 입력하거나 모두 비워야 합니다.");
      return;
    }
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

// 폼의 name 속성으로 JSON payload를 만든다. geometry_x처럼 접두사가 붙은 입력은
// geometry 객체로 묶고, 4개가 모두 채워졌을 때만 포함한다.
function collectPayload(form) {
  const data = {};
  const geometry = {};
  let filledGeometryCount = 0;

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
    if (field.name.startsWith("geometry_")) {
      geometry[field.name.slice("geometry_".length)] = value;
      if (value !== "") filledGeometryCount += 1;
      continue;
    }
    data[field.name] = value;
  }

  if (filledGeometryCount > 0) {
    if (filledGeometryCount !== 4) return null;
    data.geometry = {
      x: Number(geometry.x),
      y: Number(geometry.y),
      width: Number(geometry.width),
      height: Number(geometry.height),
    };
  }
  return data;
}
