(() => {
  const editor = document.querySelector("#roi-live-editor");
  if (!(editor instanceof HTMLElement)) return;
  const stage = document.querySelector("#roi-media-stage");
  const referenceImage = document.querySelector("#roi-reference-image");
  const placeholder = document.querySelector("#roi-media-placeholder");
  const polygon = document.querySelector("#roi-live-polygon");
  const pointGroup = document.querySelector("#roi-live-points");
  const captureButton = document.querySelector("#roi-capture");
  const startButton = document.querySelector("#roi-start");
  const finishButton = document.querySelector("#roi-finish");
  const resetButton = document.querySelector("#roi-reset");
  const cancelButton = document.querySelector("#roi-cancel");
  const classroomSelect = document.querySelector("#roi-classroom-select");
  const cameraSelect = document.querySelector("#roi-camera-select");
  const dialog = document.querySelector("#roi-student-dialog");
  const saveForm = document.querySelector("#roi-save-form");
  const seatSelect = document.querySelector("#roi-seat-select");
  const studentSelect = document.querySelector("#roi-student-select");
  const status = document.querySelector("#roi-live-status");
  const error = document.querySelector("#roi-save-error");
  const points = [];
  // 이 ROI가 어느 캡처 화면 위에서 그려졌는지를 가리킨다. 화면을 다시 캡처하면
  // 값이 올라가고, 서버는 값이 다른 ROI 저장을 거절한다.
  let referenceRevision = null;

  const selectedClassroomId = () => classroomSelect?.value || editor.dataset.classroomId;
  const selectedCameraId = () => cameraSelect?.value || "";
  const selectedCameraOption = () => cameraSelect?.selectedOptions?.[0] || null;
  const selectedCameraLabel = () => selectedCameraOption()?.dataset.cameraLabel || selectedCameraId();
  const captureAvailable = () => selectedCameraOption()?.dataset.captureAvailable === "true";

  const finishRegistration = () => {
    points.length = 0;
    renderPolygon();
    stage.classList.remove("is-registering");
    startButton.disabled = referenceRevision === null;
    startButton.textContent = "등록 시작";
    startButton.classList.remove("is-registering");
    finishButton.disabled = true;
    resetButton.disabled = true;
    cancelButton.disabled = true;
  };
  const showPlaceholder = (title, message) => {
    referenceImage.hidden = true;
    placeholder.hidden = false;
    placeholder.querySelector("strong").textContent = title;
    placeholder.querySelector("span").textContent = message;
  };
  const renderPolygon = () => {
    polygon.setAttribute("points", points.map((point) => `${point.x},${point.y}`).join(" "));
    pointGroup.replaceChildren(...points.map((point) => {
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", String(point.x));
      circle.setAttribute("cy", String(point.y));
      circle.setAttribute("r", ".009");
      return circle;
    }));
  };
  // 캡처 화면을 버리면 그 위에 그린 좌표도 의미를 잃는다. 좌표만 남겨 두면 다른
  // 화각의 ROI를 저장하게 되므로 함께 지운다.
  const discardReference = (title, message) => {
    referenceRevision = null;
    finishRegistration();
    startButton.disabled = true;
    stage.dataset.state = "empty";
    showPlaceholder(title, message);
  };

  const captureFrame = async () => {
    const classroomId = selectedClassroomId();
    const cameraId = selectedCameraId();
    if (!classroomId || !cameraId) {
      status.textContent = "강의실과 카메라를 먼저 선택해 주세요.";
      return;
    }
    if (!captureAvailable()) {
      status.textContent = `${selectedCameraLabel()}의 접속 정보가 설정되어 있지 않아 화면을 가져올 수 없습니다.`;
      return;
    }
    captureButton.disabled = true;
    captureButton.textContent = "캡처 중";
    status.textContent = "카메라에서 현재 화면을 가져오는 중입니다. 몇 초 걸립니다.";
    try {
      const response = await fetch(
        `/api/v1/classrooms/${encodeURIComponent(classroomId)}/roi-reference-image/capture?camera_id=${encodeURIComponent(cameraId)}`,
        {method: "POST"},
      );
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || "현재 화면을 가져오지 못했습니다.");
      // revision을 query에 붙여 이전 캡처가 캐시에서 다시 보이지 않게 한다.
      referenceImage.src = `${body.image_url}&revision=${body.revision}`;
      referenceImage.hidden = false;
      placeholder.hidden = true;
      stage.dataset.state = "captured";
      referenceRevision = body.revision;
      finishRegistration();
      status.textContent = `${selectedCameraLabel()}의 현재 화면을 캡처했습니다. ‘등록 시작’을 눌러 ROI를 그리세요.`;
    } catch (reason) {
      console.error("ROI 기준 화면 캡처 실패", reason);
      discardReference(
        "캡처 실패",
        reason instanceof Error ? reason.message : "현재 화면을 가져오지 못했습니다.",
      );
      status.textContent = "현재 화면을 가져오지 못해 ROI를 그릴 수 없습니다.";
    } finally {
      captureButton.disabled = false;
      captureButton.textContent = "현재 화면 캡처";
    }
  };

  classroomSelect?.addEventListener("change", () => {
    const url = new URL(location.href);
    url.searchParams.set("classroom_id", classroomSelect.value);
    location.assign(url);
  });
  cameraSelect?.addEventListener("change", () => {
    discardReference(
      "캡처된 화면이 없습니다",
      "카메라를 바꿨습니다. ‘현재 화면 캡처’를 눌러 기준 화면을 다시 가져오세요.",
    );
    status.textContent = "";
  });
  captureButton?.addEventListener("click", captureFrame);
  startButton?.addEventListener("click", () => {
    if (referenceRevision === null) {
      status.textContent = "먼저 ‘현재 화면 캡처’로 기준 화면을 가져와 주세요.";
      return;
    }
    points.length = 0;
    renderPolygon();
    startButton.disabled = true;
    startButton.textContent = "등록 중";
    startButton.classList.add("is-registering");
    finishButton.disabled = false;
    resetButton.disabled = false;
    cancelButton.disabled = false;
    stage.classList.add("is-registering");
    status.textContent = "캡처된 화면 위를 클릭해 ROI 꼭짓점을 3개 이상 지정해 주세요.";
  });
  stage?.addEventListener("click", (event) => {
    if (!stage.classList.contains("is-registering") || event.target === placeholder) return;
    // 좌표는 캡처 화면의 상대 위치다. stage가 이미지와 같은 크기여야 값이 맞으므로
    // CSS에서 이미지가 stage의 크기를 정하게 해 두었다.
    const rect = stage.getBoundingClientRect();
    const point = {
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    };
    points.push(point);
    renderPolygon();
    console.log("ROI 좌표 선택", {index: points.length, ...point});
  });
  resetButton?.addEventListener("click", () => {
    if (!stage.classList.contains("is-registering")) return;
    points.length = 0;
    renderPolygon();
    console.log("ROI 좌표 초기화");
    status.textContent = "좌표를 초기화했습니다. ROI 꼭짓점을 다시 3개 이상 지정해 주세요.";
  });
  const cancelRegistration = () => {
    if (!stage.classList.contains("is-registering")) return;
    if (dialog.open) dialog.close();
    document.body.classList.remove("roi-modal-open");
    finishRegistration();
    console.log("ROI 등록 취소");
    status.textContent = "ROI 등록을 취소했습니다.";
  };
  cancelButton?.addEventListener("click", cancelRegistration);
  finishButton?.addEventListener("click", () => {
    if (points.length < 3) {
      status.textContent = "ROI 꼭짓점을 3개 이상 선택해 주세요.";
      return;
    }
    console.log("ROI 선택 완료", {
      classroom_id: selectedClassroomId(),
      camera_id: selectedCameraId(),
      polygon: points,
    });
    seatSelect.value = "";
    studentSelect.value = "";
    error.hidden = true;
    dialog.showModal();
    document.body.classList.add("roi-modal-open");
  });
  const closeDialog = () => {
    if (dialog.open) dialog.close();
    document.body.classList.remove("roi-modal-open");
  };
  document.querySelector("#roi-dialog-close")?.addEventListener("click", closeDialog);
  dialog?.addEventListener("click", (event) => { if (event.target === dialog) closeDialog(); });
  dialog?.addEventListener("close", () => document.body.classList.remove("roi-modal-open"));
  saveForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!seatSelect.reportValidity() || !studentSelect.reportValidity()) return;
    if (referenceRevision === null) {
      error.textContent = "기준 화면이 없습니다. 현재 화면을 다시 캡처해 주세요.";
      error.hidden = false;
      return;
    }
    const saveButton = document.querySelector("#roi-dialog-save");
    saveButton.disabled = true;
    error.hidden = true;
    const payload = {
      camera_id: selectedCameraId(),
      student_id: studentSelect.value,
      polygon: points,
      reference_image_revision: referenceRevision,
    };
    try {
      const response = await fetch(
        `/api/v1/classrooms/${encodeURIComponent(selectedClassroomId())}/seats/${encodeURIComponent(seatSelect.value)}/roi-connection`,
        {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)},
      );
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || "ROI를 저장하지 못했습니다.");
      console.log("ROI 연결 저장", {
        classroom_id: body.classroom_id,
        camera_id: body.camera_id,
        seat_id: body.seat_id,
        student_id: body.student_id,
        polygon: body.polygon,
      });
      closeDialog();
      finishRegistration();
      status.textContent = "ROI와 학생 좌석 연결을 저장했습니다.";
    } catch (reason) {
      error.textContent = reason instanceof Error ? reason.message : "ROI를 저장하지 못했습니다.";
      error.hidden = false;
    } finally {
      saveButton.disabled = false;
    }
  });
})();
