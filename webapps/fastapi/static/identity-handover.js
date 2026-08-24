(() => {
  const root = document.querySelector("#handover-editor");
  if (!root) return;

  const classroomSelect = document.querySelector("#handover-classroom");
  const entrySelect = document.querySelector("#handover-entry-camera");
  const cameraSelect = document.querySelector("#handover-classroom-camera");
  const captureButton = document.querySelector("#handover-capture");
  const drawButton = document.querySelector("#handover-draw");
  const saveButton = document.querySelector("#handover-save");
  const deleteButton = document.querySelector("#handover-delete");
  const stage = document.querySelector("#handover-stage");
  const image = document.querySelector("#handover-reference-image");
  const placeholder = document.querySelector("#handover-placeholder");
  const overlay = document.querySelector("#handover-overlay");
  const savedRect = document.querySelector("#handover-saved-zone");
  const draftRect = document.querySelector("#handover-draft-zone");
  const zoneLabel = document.querySelector("#handover-zone-label");
  const status = document.querySelector("#handover-status");
  const coordinates = document.querySelector("#handover-coordinates");
  const updatedAt = document.querySelector("#handover-updated-at");
  const workerValue = document.querySelector("#handover-worker-value");

  let routes = [];
  let referenceRevision = null;
  let draftZone = null;
  let dragStart = null;

  const classroomId = () => root.dataset.classroomId;
  const cameraId = () => cameraSelect?.value || "";
  const entryCameraId = () => entrySelect?.value || "";
  const selectedRoute = () => routes.find((item) => item.classroom_camera_id === cameraId());
  const captureAvailable = () => cameraSelect?.selectedOptions[0]?.dataset.captureAvailable === "true";
  const routePath = () =>
    `/api/v1/classrooms/${encodeURIComponent(classroomId())}/identity-handover-routes/${encodeURIComponent(cameraId())}`;

  const clamp = (value) => Math.max(0, Math.min(1, value));
  const percent = (value) => `${(value * 100).toFixed(2)}%`;

  const setRect = (element, zone) => {
    if (!zone) {
      element.setAttribute("hidden", "");
      return;
    }
    element.removeAttribute("hidden");
    element.setAttribute("x", zone.left);
    element.setAttribute("y", zone.top);
    element.setAttribute("width", zone.right - zone.left);
    element.setAttribute("height", zone.bottom - zone.top);
  };

  const render = () => {
    const route = selectedRoute();
    setRect(savedRect, route?.classroom_entry_zone);
    setRect(draftRect, draftZone);
    const shown = draftZone || route?.classroom_entry_zone || null;
    if (shown) {
      zoneLabel.removeAttribute("hidden");
      zoneLabel.setAttribute("x", shown.left + 0.008);
      zoneLabel.setAttribute("y", Math.max(0.04, shown.top + 0.04));
    } else {
      zoneLabel.setAttribute("hidden", "");
    }
    const values = shown
      ? [shown.left, shown.top, shown.right, shown.bottom].map(percent)
      : ["—", "—", "—", "—"];
    [...coordinates.querySelectorAll("dd")].forEach((item, index) => {
      item.textContent = values[index];
    });
    updatedAt.textContent = route
      ? `마지막 저장: ${new Date(route.updated_at).toLocaleString("ko-KR")}`
      : "저장된 설정이 없습니다.";
    workerValue.textContent = route?.worker_environment_value || "설정 없음";
    drawButton.disabled = referenceRevision === null;
    saveButton.disabled = referenceRevision === null || draftZone === null || !entryCameraId();
    deleteButton.disabled = !route;
    captureButton.disabled = !cameraId() || !captureAvailable();
  };

  const loadRoutes = async () => {
    if (!classroomId()) return;
    try {
      const response = await fetch(
        `/api/v1/classrooms/${encodeURIComponent(classroomId())}/identity-handover-routes`,
      );
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || "인계 설정을 불러오지 못했습니다.");
      routes = body.items || [];
      draftZone = null;
      render();
      status.textContent = selectedRoute()
        ? "저장된 인계 영역입니다. CCTV 화면을 캡처하면 실제 위치를 겹쳐 확인할 수 있습니다."
        : "저장된 인계 영역이 없습니다. CCTV 화면을 캡처한 뒤 문 바닥 경계를 그려 주세요.";
    } catch (reason) {
      console.error("신원 인계 설정 조회 실패", reason);
      status.textContent = reason instanceof Error ? reason.message : "인계 설정을 불러오지 못했습니다.";
    }
  };

  const capture = async () => {
    if (!cameraId() || !captureAvailable()) {
      status.textContent = "선택한 CCTV의 RTSP 캡처 연결이 설정되어 있지 않습니다.";
      return;
    }
    captureButton.disabled = true;
    captureButton.textContent = "캡처 중";
    status.textContent = "CCTV 현재 화면을 가져오는 중입니다.";
    try {
      const response = await fetch(
        `/api/v1/classrooms/${encodeURIComponent(classroomId())}/identity-handover-reference-image/capture?camera_id=${encodeURIComponent(cameraId())}`,
        {method: "POST"},
      );
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || "CCTV 화면을 캡처하지 못했습니다.");
      referenceRevision = body.revision;
      image.src = `${body.image_url}&revision=${body.revision}`;
      image.hidden = false;
      placeholder.hidden = true;
      stage.dataset.state = "captured";
      draftZone = null;
      render();
      status.textContent = selectedRoute()
        ? "현재 CCTV 화면에 저장된 인계 영역을 표시했습니다. 위치가 맞는지 확인하세요."
        : "‘영역 그리기’를 누른 뒤 문 바닥 경계를 드래그하세요.";
    } catch (reason) {
      console.error("인계 기준 화면 캡처 실패", reason);
      status.textContent = reason instanceof Error ? reason.message : "CCTV 화면을 캡처하지 못했습니다.";
    } finally {
      captureButton.textContent = "CCTV 현재 화면 캡처";
      render();
    }
  };

  const beginDrawing = () => {
    if (referenceRevision === null) return;
    draftZone = null;
    dragStart = null;
    stage.classList.add("is-drawing");
    drawButton.textContent = "드래그해서 영역 지정";
    status.textContent = "CCTV 화면에서 사람이 들어오는 문 바닥 경계를 사각형으로 드래그하세요.";
    render();
  };

  const pointFromEvent = (event) => {
    const rect = overlay.getBoundingClientRect();
    return {
      x: clamp((event.clientX - rect.left) / rect.width),
      y: clamp((event.clientY - rect.top) / rect.height),
    };
  };

  overlay.addEventListener("pointerdown", (event) => {
    if (!stage.classList.contains("is-drawing")) return;
    dragStart = pointFromEvent(event);
    overlay.setPointerCapture(event.pointerId);
    draftZone = {left: dragStart.x, top: dragStart.y, right: dragStart.x, bottom: dragStart.y};
    render();
  });
  overlay.addEventListener("pointermove", (event) => {
    if (!dragStart || !stage.classList.contains("is-drawing")) return;
    const point = pointFromEvent(event);
    draftZone = {
      left: Math.min(dragStart.x, point.x),
      top: Math.min(dragStart.y, point.y),
      right: Math.max(dragStart.x, point.x),
      bottom: Math.max(dragStart.y, point.y),
    };
    render();
  });
  overlay.addEventListener("pointerup", (event) => {
    if (!dragStart) return;
    overlay.releasePointerCapture(event.pointerId);
    dragStart = null;
    if (!draftZone || draftZone.right - draftZone.left < 0.005 || draftZone.bottom - draftZone.top < 0.005) {
      draftZone = null;
      status.textContent = "영역이 너무 작습니다. 문 바닥 경계를 다시 드래그하세요.";
    } else {
      stage.classList.remove("is-drawing");
      drawButton.textContent = "영역 다시 그리기";
      status.textContent = "선택한 위치를 확인하고 ‘설정 저장’을 누르세요.";
    }
    render();
  });

  const save = async () => {
    if (!draftZone || referenceRevision === null) return;
    saveButton.disabled = true;
    try {
      const response = await fetch(routePath(), {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          entry_camera_id: entryCameraId(),
          classroom_entry_zone: draftZone,
          reference_image_revision: referenceRevision,
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || "인계 설정을 저장하지 못했습니다.");
      routes = routes.filter((item) => item.classroom_camera_id !== body.classroom_camera_id);
      routes.push(body);
      draftZone = null;
      render();
      status.textContent = "인계 ROI를 저장했습니다. worker가 다음 설정 갱신 때 자동 적용합니다.";
    } catch (reason) {
      console.error("신원 인계 설정 저장 실패", reason);
      status.textContent = reason instanceof Error ? reason.message : "인계 설정을 저장하지 못했습니다.";
      render();
    }
  };

  const remove = async () => {
    if (!selectedRoute()) return;
    if (!window.confirm("이 CCTV의 신원 인계 설정을 삭제할까요?\n삭제 후에는 입구 신원이 CCTV track으로 넘어가지 않습니다.")) return;
    deleteButton.disabled = true;
    try {
      const response = await fetch(routePath(), {method: "DELETE"});
      if (!response.ok && response.status !== 204) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error?.message || "인계 설정을 삭제하지 못했습니다.");
      }
      routes = routes.filter((item) => item.classroom_camera_id !== cameraId());
      draftZone = null;
      render();
      status.textContent = "인계 설정을 삭제했습니다. worker가 다음 갱신 때 인계를 중지합니다.";
    } catch (reason) {
      status.textContent = reason instanceof Error ? reason.message : "인계 설정을 삭제하지 못했습니다.";
      render();
    }
  };

  classroomSelect?.addEventListener("change", () => {
    const url = new URL(location.href);
    url.searchParams.set("classroom_id", classroomSelect.value);
    location.assign(url);
  });
  cameraSelect?.addEventListener("change", () => {
    referenceRevision = null;
    draftZone = null;
    image.hidden = true;
    placeholder.hidden = false;
    stage.classList.remove("is-drawing");
    drawButton.textContent = "영역 다시 그리기";
    render();
    status.textContent = selectedRoute()
      ? "저장된 인계 영역이 있습니다. CCTV 화면을 캡처해 위치를 확인하세요."
      : "CCTV 화면을 캡처한 뒤 인계 영역을 그려 주세요.";
  });
  captureButton?.addEventListener("click", capture);
  drawButton?.addEventListener("click", beginDrawing);
  saveButton?.addEventListener("click", save);
  deleteButton?.addEventListener("click", remove);

  loadRoutes();
})();
