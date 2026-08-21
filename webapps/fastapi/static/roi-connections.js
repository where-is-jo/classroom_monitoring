(() => {
  const editor = document.querySelector("#roi-live-editor");
  if (!(editor instanceof HTMLElement)) return;
  const stage = document.querySelector("#roi-media-stage");
  const referenceImage = document.querySelector("#roi-reference-image");
  const placeholder = document.querySelector("#roi-media-placeholder");
  const polygon = document.querySelector("#roi-live-polygon");
  const pointGroup = document.querySelector("#roi-live-points");
  const savedShapes = document.querySelector("#roi-saved-shapes");
  const savedLabels = document.querySelector("#roi-saved-labels");
  const savedSummary = document.querySelector("#roi-saved-summary");
  const captureButton = document.querySelector("#roi-capture");
  const startButton = document.querySelector("#roi-start");
  const finishButton = document.querySelector("#roi-finish");
  const resetButton = document.querySelector("#roi-reset");
  const cancelButton = document.querySelector("#roi-cancel");
  const redrawButton = document.querySelector("#roi-redraw");
  const deleteButton = document.querySelector("#roi-delete");
  const classroomSelect = document.querySelector("#roi-classroom-select");
  const cameraSelect = document.querySelector("#roi-camera-select");
  const dialog = document.querySelector("#roi-student-dialog");
  const saveForm = document.querySelector("#roi-save-form");
  const seatSelect = document.querySelector("#roi-seat-select");
  const studentSelect = document.querySelector("#roi-student-select");
  const status = document.querySelector("#roi-live-status");
  const error = document.querySelector("#roi-save-error");
  const SVG_NS = "http://www.w3.org/2000/svg";

  const points = [];
  // 이 ROI가 어느 캡처 화면 위에서 그려졌는지를 가리킨다. 화면을 다시 캡처하면
  // 값이 올라가고, 서버는 값이 다른 ROI 저장을 거절한다.
  let referenceRevision = null;
  let savedConnections = [];
  let selectedSeatId = null;
  // 다시 그리기 중이면 그 좌석 id. 저장할 때 좌석을 다시 고르지 않게 한다.
  let redrawSeatId = null;

  const selectedClassroomId = () => classroomSelect?.value || editor.dataset.classroomId;
  const selectedCameraId = () => cameraSelect?.value || "";
  const selectedCameraOption = () => cameraSelect?.selectedOptions?.[0] || null;
  const selectedCameraLabel = () => selectedCameraOption()?.dataset.cameraLabel || selectedCameraId();
  const captureAvailable = () => selectedCameraOption()?.dataset.captureAvailable === "true";
  const isRegistering = () => stage.classList.contains("is-registering");
  const seatLabel = (seatId) => {
    const option = seatSelect?.querySelector(`option[value="${CSS.escape(seatId)}"]`);
    return option?.textContent?.trim() || seatId;
  };

  const roiPath = (seatId) =>
    `/api/v1/classrooms/${encodeURIComponent(selectedClassroomId())}` +
    `/seats/${encodeURIComponent(seatId)}/roi-connection`;

  const showPlaceholder = (title, message) => {
    referenceImage.hidden = true;
    placeholder.hidden = false;
    placeholder.querySelector("strong").textContent = title;
    placeholder.querySelector("span").textContent = message;
  };
  const renderPolygon = () => {
    polygon.setAttribute("points", points.map((point) => `${point.x},${point.y}`).join(" "));
    pointGroup.replaceChildren(...points.map((point) => {
      const circle = document.createElementNS(SVG_NS, "circle");
      circle.setAttribute("cx", String(point.x));
      circle.setAttribute("cy", String(point.y));
      circle.setAttribute("r", ".009");
      return circle;
    }));
  };

  const centerOf = (polygonPoints) => {
    const sum = polygonPoints.reduce(
      (acc, point) => ({x: acc.x + point.x, y: acc.y + point.y}),
      {x: 0, y: 0},
    );
    return {x: sum.x / polygonPoints.length, y: sum.y / polygonPoints.length};
  };

  // 저장된 ROI를 화면에 겹쳐 그린다. 이게 없으면 좌석이 스무 개일 때 어디를 이미
  // 그렸는지 알 수 없어 빠뜨리거나 두 번 그리게 된다.
  const renderSaved = () => {
    const shapes = [];
    const labels = [];
    for (const item of savedConnections) {
      // 다시 그리는 중인 좌석은 새로 찍는 좌표와 겹쳐 보이므로 잠시 숨긴다.
      if (redrawSeatId === item.seat_id) continue;
      const shape = document.createElementNS(SVG_NS, "polygon");
      shape.setAttribute("points", item.polygon.map((p) => `${p.x},${p.y}`).join(" "));
      shape.dataset.seatId = item.seat_id;
      if (item.needs_review) shape.classList.add("needs-review");
      if (selectedSeatId === item.seat_id) shape.classList.add("is-selected");
      shapes.push(shape);

      const center = centerOf(item.polygon);
      const label = document.createElement("span");
      label.className = "roi-saved-label";
      label.style.left = `${center.x * 100}%`;
      label.style.top = `${center.y * 100}%`;
      label.textContent = seatLabel(item.seat_id);
      if (item.needs_review) {
        const mark = document.createElement("span");
        mark.className = "review-mark";
        mark.textContent = "재검토";
        label.append(mark);
      }
      labels.push(label);
    }
    savedShapes.replaceChildren(...shapes);
    savedLabels.replaceChildren(...labels);

    const review = savedConnections.filter((item) => item.needs_review).length;
    if (savedConnections.length === 0) {
      savedSummary.textContent = "등록된 ROI가 없습니다.";
    } else if (review === 0) {
      savedSummary.textContent = `등록된 ROI ${savedConnections.length}개. 폴리곤을 클릭하면 다시 그리거나 지울 수 있습니다.`;
    } else {
      savedSummary.textContent =
        `등록된 ROI ${savedConnections.length}개 중 ${review}개가 재검토 대상입니다. ` +
        "기준 화면이 바뀌어 좌표를 믿을 수 없으므로 좌석 판정에서 빠집니다. 다시 그려 주세요.";
    }
    updateSelectionButtons();
  };

  const updateSelectionButtons = () => {
    const selectable = selectedSeatId !== null && !isRegistering();
    redrawButton.disabled = !(selectable && referenceRevision !== null);
    deleteButton.disabled = !selectable;
  };

  const loadConnections = async () => {
    const classroomId = selectedClassroomId();
    const cameraId = selectedCameraId();
    if (!classroomId || !cameraId) {
      savedConnections = [];
      renderSaved();
      return;
    }
    try {
      const response = await fetch(
        `/api/v1/classrooms/${encodeURIComponent(classroomId)}/roi-connections?camera_id=${encodeURIComponent(cameraId)}`,
      );
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || "등록된 ROI를 불러오지 못했습니다.");
      savedConnections = body.items || [];
    } catch (reason) {
      console.error("ROI 목록 조회 실패", reason);
      savedConnections = [];
      // "등록된 것이 없다"와 "불러오지 못했다"를 구분해서 알린다.
      savedSummary.textContent = "등록된 ROI를 불러오지 못했습니다. 화면을 새로고침해 주세요.";
      savedShapes.replaceChildren();
      savedLabels.replaceChildren();
      updateSelectionButtons();
      return;
    }
    selectedSeatId = null;
    renderSaved();
  };

  const finishRegistration = () => {
    points.length = 0;
    redrawSeatId = null;
    renderPolygon();
    stage.classList.remove("is-registering");
    startButton.disabled = referenceRevision === null;
    startButton.textContent = "등록 시작";
    startButton.classList.remove("is-registering");
    finishButton.disabled = true;
    resetButton.disabled = true;
    cancelButton.disabled = true;
    renderSaved();
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

  const beginRegistration = (message) => {
    points.length = 0;
    renderPolygon();
    startButton.disabled = true;
    startButton.textContent = "등록 중";
    startButton.classList.add("is-registering");
    finishButton.disabled = false;
    resetButton.disabled = false;
    cancelButton.disabled = false;
    stage.classList.add("is-registering");
    updateSelectionButtons();
    status.textContent = message;
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
    // 다시 캡처하면 기존 ROI가 모두 재검토 대상이 된다. 조용히 무효가 되면
    // 스무 개를 그려 놓고도 이유를 모른 채 판정이 비게 된다.
    const live = savedConnections.filter((item) => !item.needs_review).length;
    if (live > 0) {
      const proceed = window.confirm(
        `이미 등록된 ROI ${live}개가 재검토 대상이 되어 좌석 판정에서 빠집니다.\n` +
        "기준 화면이 바뀌면 같은 좌표가 다른 좌석을 가리킬 수 있기 때문입니다.\n\n" +
        "그래도 다시 캡처할까요?",
      );
      if (!proceed) {
        status.textContent = "캡처를 취소했습니다. 기존 ROI는 그대로 있습니다.";
        return;
      }
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
      await loadConnections();
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

  const deleteSelected = async () => {
    if (selectedSeatId === null) return;
    const seatId = selectedSeatId;
    const proceed = window.confirm(
      `${seatLabel(seatId)}의 ROI를 지울까요?\n\n` +
      "이 카메라는 그 좌석을 더 이상 관측하지 않게 됩니다.",
    );
    if (!proceed) return;
    deleteButton.disabled = true;
    try {
      const response = await fetch(
        `${roiPath(seatId)}?camera_id=${encodeURIComponent(selectedCameraId())}`,
        {method: "DELETE"},
      );
      if (!response.ok && response.status !== 204) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error?.message || "ROI를 지우지 못했습니다.");
      }
      await loadConnections();
      status.textContent = `${seatLabel(seatId)}의 ROI를 지웠습니다.`;
    } catch (reason) {
      console.error("ROI 삭제 실패", reason);
      status.textContent = reason instanceof Error ? reason.message : "ROI를 지우지 못했습니다.";
      updateSelectionButtons();
    }
  };

  const redrawSelected = () => {
    if (selectedSeatId === null || referenceRevision === null) return;
    redrawSeatId = selectedSeatId;
    beginRegistration(
      `${seatLabel(redrawSeatId)}의 ROI를 다시 그립니다. 화면 위를 클릭해 꼭짓점을 3개 이상 지정해 주세요.`,
    );
    renderSaved();
  };

  savedShapes?.addEventListener("click", (event) => {
    const shape = event.target.closest("polygon");
    if (!shape || isRegistering()) return;
    event.stopPropagation();
    const seatId = shape.dataset.seatId;
    selectedSeatId = selectedSeatId === seatId ? null : seatId;
    renderSaved();
    status.textContent = selectedSeatId
      ? `${seatLabel(selectedSeatId)}을 선택했습니다. 다시 그리거나 지울 수 있습니다.`
      : "선택을 해제했습니다.";
  });

  classroomSelect?.addEventListener("change", () => {
    const url = new URL(location.href);
    url.searchParams.set("classroom_id", classroomSelect.value);
    location.assign(url);
  });
  cameraSelect?.addEventListener("change", async () => {
    discardReference(
      "캡처된 화면이 없습니다",
      "카메라를 바꿨습니다. ‘현재 화면 캡처’를 눌러 기준 화면을 다시 가져오세요.",
    );
    status.textContent = "";
    await loadConnections();
  });
  captureButton?.addEventListener("click", captureFrame);
  redrawButton?.addEventListener("click", redrawSelected);
  deleteButton?.addEventListener("click", deleteSelected);
  startButton?.addEventListener("click", () => {
    if (referenceRevision === null) {
      status.textContent = "먼저 ‘현재 화면 캡처’로 기준 화면을 가져와 주세요.";
      return;
    }
    redrawSeatId = null;
    selectedSeatId = null;
    beginRegistration("캡처된 화면 위를 클릭해 ROI 꼭짓점을 3개 이상 지정해 주세요.");
    renderSaved();
  });
  stage?.addEventListener("click", (event) => {
    if (!isRegistering() || event.target === placeholder) return;
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
    if (!isRegistering()) return;
    points.length = 0;
    renderPolygon();
    console.log("ROI 좌표 초기화");
    status.textContent = "좌표를 초기화했습니다. ROI 꼭짓점을 다시 3개 이상 지정해 주세요.";
  });
  const cancelRegistration = () => {
    if (!isRegistering()) return;
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
    const existing = savedConnections.find((item) => item.seat_id === redrawSeatId);
    seatSelect.value = redrawSeatId || "";
    studentSelect.value = existing?.student_id || "";
    // 다시 그리기는 대상 좌석이 이미 정해져 있다. 여기서 바꾸면 다른 좌석을 덮어쓴다.
    seatSelect.disabled = redrawSeatId !== null;
    error.hidden = true;
    dialog.showModal();
    document.body.classList.add("roi-modal-open");
  });
  const closeDialog = () => {
    if (dialog.open) dialog.close();
    seatSelect.disabled = false;
    document.body.classList.remove("roi-modal-open");
  };
  document.querySelector("#roi-dialog-close")?.addEventListener("click", closeDialog);
  dialog?.addEventListener("click", (event) => { if (event.target === dialog) closeDialog(); });
  dialog?.addEventListener("close", () => {
    seatSelect.disabled = false;
    document.body.classList.remove("roi-modal-open");
  });
  saveForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    // disabled 상태의 select는 reportValidity를 통과하므로 값 자체를 본다.
    if (!seatSelect.value) {
      error.textContent = "좌석을 선택해 주세요.";
      error.hidden = false;
      return;
    }
    if (!studentSelect.reportValidity()) return;
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
      const response = await fetch(roiPath(seatSelect.value), {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
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
      await loadConnections();
      status.textContent = "ROI와 학생 좌석 연결을 저장했습니다.";
    } catch (reason) {
      error.textContent = reason instanceof Error ? reason.message : "ROI를 저장하지 못했습니다.";
      error.hidden = false;
    } finally {
      saveButton.disabled = false;
    }
  });

  loadConnections();
})();
