(() => {
  const editor = document.querySelector("#roi-live-editor");
  if (!(editor instanceof HTMLElement)) return;
  const stage = document.querySelector("#roi-media-stage");
  const referenceImage = document.querySelector("#roi-reference-image");
  const placeholder = document.querySelector("#roi-media-placeholder");
  const polygon = document.querySelector("#roi-live-polygon");
  const pointGroup = document.querySelector("#roi-live-points");
  const savedShapes = document.querySelector("#roi-saved-shapes");
  const previewShapes = document.querySelector("#roi-auto-preview");
  const savedLabels = document.querySelector("#roi-saved-labels");
  const savedSummary = document.querySelector("#roi-saved-summary");
  const captureButton = document.querySelector("#roi-capture");
  const startButton = document.querySelector("#roi-start");
  const finishButton = document.querySelector("#roi-finish");
  const resetButton = document.querySelector("#roi-reset");
  const cancelButton = document.querySelector("#roi-cancel");
  const redrawButton = document.querySelector("#roi-redraw");
  const deleteButton = document.querySelector("#roi-delete");
  const autoButton = document.querySelector("#roi-auto");
  const autoSaveButton = document.querySelector("#roi-auto-save");
  const autoConfirmButton = document.querySelector("#roi-auto-confirm");
  const seatFillInput = document.querySelector("#roi-seat-fill");
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
  // 자동 생성에서 좌석 구역 네 모서리를 찍는 중인지. 같은 클릭 동작을 쓰지만 찍은
  // 좌표의 의미가 다르다 — ROI 하나가 아니라 격자를 얹을 사각형이다.
  let autoPicking = false;
  // 아직 저장하지 않은 자동 생성 결과. 관리자가 겹쳐 보고 판단하는 대상이다.
  let previewSeats = [];
  let previewCorners = [];

  // 좌석 칸을 얼마나 채울지. 서버 기본값과 같은 값에서 시작한다.
  const seatFillRatio = () => {
    const percent = Number(seatFillInput?.value);
    if (!Number.isFinite(percent)) return 0.8;
    return Math.min(1, Math.max(0.3, percent / 100));
  };

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
    // 아직 저장하지 않은 자동 생성 결과를 위에 겹쳐 그린다. 어느 좌석이 어디로
    // 갔는지 보지 않고는 격자가 실제 배치와 맞는지 알 수 없다.
    const previewNodes = [];
    for (const seat of previewSeats) {
      if (!seat.polygon) continue;
      const shape = document.createElementNS(SVG_NS, "polygon");
      shape.setAttribute("points", seat.polygon.map((p) => `${p.x},${p.y}`).join(" "));
      shape.dataset.seatId = seat.seat_id;
      previewNodes.push(shape);

      const center = centerOf(seat.polygon);
      const label = document.createElement("span");
      label.className = "roi-saved-label roi-preview-label";
      label.style.left = `${center.x * 100}%`;
      label.style.top = `${center.y * 100}%`;
      label.textContent = seat.seat_label;
      const mark = document.createElement("span");
      mark.className = "review-mark";
      mark.textContent = "미리보기";
      label.append(mark);
      labels.push(label);
    }
    previewShapes.replaceChildren(...previewNodes);
    savedShapes.replaceChildren(...shapes);
    savedLabels.replaceChildren(...labels);

    const review = savedConnections.filter((item) => item.needs_review).length;
    const auto = savedConnections.filter((item) => item.auto_generated).length;
    if (savedConnections.length === 0) {
      savedSummary.textContent = "등록된 ROI가 없습니다.";
    } else if (auto > 0) {
      savedSummary.textContent =
        `등록된 ROI ${savedConnections.length}개 중 ${auto}개가 자동 생성분입니다. ` +
        "확인 전에는 좌석 판정에 쓰이지 않습니다. 어긋난 좌석은 다시 그리거나 지운 뒤 " +
        "‘자동 생성 확정’을 눌러 주세요.";
    } else if (review === 0) {
      savedSummary.textContent = `등록된 ROI ${savedConnections.length}개. 폴리곤을 클릭하면 다시 그리거나 지울 수 있습니다.`;
    } else {
      savedSummary.textContent =
        `등록된 ROI ${savedConnections.length}개 중 ${review}개가 재검토 대상입니다. ` +
        "기준 화면이 바뀌어 좌표를 믿을 수 없으므로 좌석 판정에서 빠집니다. 다시 그려 주세요.";
    }
    autoConfirmButton.hidden = auto === 0;
    autoConfirmButton.textContent = `자동 생성 확정 (${auto})`;
    updateSelectionButtons();
  };

  const clearPreview = () => {
    previewSeats = [];
    previewCorners = [];
    autoSaveButton.hidden = true;
    renderSaved();
  };

  // 건너뛴 좌석을 이유와 함께 알린다. 조용히 빠지면 관리자는 좌석이 등록된 줄 안다.
  const SKIP_REASONS = {
    EXISTING_KEPT: "이미 ROI가 있어 그대로 뒀습니다",
    NO_GRID_POSITION: "좌석에 행·열 좌표가 없습니다",
    TOO_SMALL: "화면에서 너무 작게 잡혔습니다",
    INVALID_POLYGON: "만들어진 좌표가 ROI 규칙을 통과하지 못했습니다",
  };

  const describeAutoResult = (body) => {
    const skipped = body.seats.filter((seat) => seat.outcome !== "GENERATED");
    const grouped = new Map();
    for (const seat of skipped) {
      const reason = SKIP_REASONS[seat.outcome] || seat.outcome;
      grouped.set(reason, (grouped.get(reason) || 0) + 1);
    }
    const detail = [...grouped].map(([reason, count]) => `${reason} ${count}개`).join(", ");
    const grid = `${body.grid_rows}행 ${body.grid_columns}열 격자`;
    return skipped.length === 0
      ? `${grid}에서 좌석 ${body.generated_count}개를 만들었습니다.`
      : `${grid}에서 좌석 ${body.generated_count}개를 만들고 ${skipped.length}개는 건너뛰었습니다 (${detail}).`;
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
    autoPicking = false;
    renderPolygon();
    stage.classList.remove("is-registering");
    autoButton.disabled = referenceRevision === null;
    autoButton.textContent = "자동 생성";
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
    previewSeats = [];
    previewCorners = [];
    autoSaveButton.hidden = true;
    finishRegistration();
    startButton.disabled = true;
    autoButton.disabled = true;
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
      clearPreview();
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

  const autoRoiPath = () =>
    `/api/v1/classrooms/${encodeURIComponent(selectedClassroomId())}/roi-connections/auto`;

  // 좌석 격자를 캡처 화면 위로 사영해 좌석마다 ROI를 만든다. dry_run이면 계산만 받아
  // 겹쳐 보여주고, 관리자가 확인한 뒤에 같은 좌표로 다시 불러 저장한다.
  const requestAutoRoi = async (dryRun) => {
    if (previewCorners.length !== 4 || referenceRevision === null) return;
    const button = dryRun ? autoButton : autoSaveButton;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = dryRun ? "계산 중" : "저장 중";
    try {
      const response = await fetch(autoRoiPath(), {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          camera_id: selectedCameraId(),
          corners: previewCorners,
          reference_image_revision: referenceRevision,
          seat_fill_ratio: seatFillRatio(),
          dry_run: dryRun,
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || "좌석 ROI를 만들지 못했습니다.");
      if (dryRun) {
        previewSeats = body.seats.filter((seat) => seat.polygon);
        autoSaveButton.hidden = previewSeats.length === 0;
        renderSaved();
        status.textContent =
          `${describeAutoResult(body)} 좌석 이름이 맞는 자리에 있는지 확인한 뒤 ` +
          "‘미리보기 저장’을 눌러 주세요. 어긋났다면 ‘자동 생성’을 다시 눌러 모서리를 다시 찍습니다.";
      } else {
        clearPreview();
        await loadConnections();
        status.textContent =
          `${describeAutoResult(body)} 자동 생성분은 확인 전까지 좌석 판정에 쓰이지 않습니다.`;
      }
    } catch (reason) {
      console.error("좌석 ROI 자동 생성 실패", reason);
      status.textContent = reason instanceof Error ? reason.message : "좌석 ROI를 만들지 못했습니다.";
      if (!dryRun) autoSaveButton.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = originalText;
      if (dryRun) autoButton.disabled = referenceRevision === null;
    }
  };

  const confirmAutoRoi = async () => {
    const auto = savedConnections.filter((item) => item.auto_generated).length;
    if (auto === 0) return;
    const proceed = window.confirm(
      `자동 생성한 ROI ${auto}개를 좌석 판정에 사용합니다.\n` +
      "계산으로 만든 좌표라 격자와 실제 배치가 어긋나면 다른 좌석으로 기록됩니다.\n\n" +
      "화면에서 자리를 확인하셨나요?",
    );
    if (!proceed) return;
    autoConfirmButton.disabled = true;
    try {
      const response = await fetch(`${autoRoiPath()}/confirm`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({camera_id: selectedCameraId()}),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || "자동 생성분을 확정하지 못했습니다.");
      await loadConnections();
      status.textContent = body.stale_count
        ? `${body.confirmed_count}개를 확정했습니다. ${body.stale_count}개는 기준 화면이 바뀌어 확정하지 않았습니다 — 다시 만들어 주세요.`
        : `${body.confirmed_count}개를 확정했습니다. 이제 좌석 판정에 사용됩니다.`;
    } catch (reason) {
      console.error("자동 생성 ROI 확정 실패", reason);
      status.textContent = reason instanceof Error ? reason.message : "자동 생성분을 확정하지 못했습니다.";
    } finally {
      autoConfirmButton.disabled = false;
    }
  };

  const startAutoPreview = () => {
    previewCorners = points.map((point) => ({x: point.x, y: point.y}));
    finishRegistration();
    status.textContent = "좌석 자리를 계산하는 중입니다.";
    void requestAutoRoi(true);
  };

  autoButton?.addEventListener("click", () => {
    if (referenceRevision === null) {
      status.textContent = "먼저 ‘현재 화면 캡처’로 기준 화면을 가져와 주세요.";
      return;
    }
    clearPreview();
    autoPicking = true;
    redrawSeatId = null;
    selectedSeatId = null;
    beginRegistration(
      "좌석 구역의 네 모서리를 찍어 주세요. 1행 1열 좌석의 바깥 모서리에서 시작해 " +
      "이웃한 순서로 돌면 됩니다. 통로는 좌석 관리 화면에서 빈 칸으로 두어야 자리가 맞습니다.",
    );
    autoButton.textContent = "모서리 지정 중";
    renderSaved();
  });
  autoSaveButton?.addEventListener("click", () => requestAutoRoi(false));
  autoConfirmButton?.addEventListener("click", confirmAutoRoi);

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
    clearPreview();
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
    // 좌석 구역은 네 모서리로 정해진다. 다 찍히면 바로 미리보기를 받는다.
    if (autoPicking && points.length === 4) startAutoPreview();
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
    if (autoPicking) {
      if (points.length !== 4) {
        status.textContent = "좌석 구역의 네 모서리를 모두 찍어 주세요.";
        return;
      }
      startAutoPreview();
      return;
    }
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
