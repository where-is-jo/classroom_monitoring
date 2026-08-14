(() => {
  const editor = document.querySelector("#roi-live-editor");
  if (!(editor instanceof HTMLElement)) return;
  const stage = document.querySelector("#roi-media-stage");
  const video = document.querySelector("#roi-live-video");
  const fallbackImage = document.querySelector("#roi-fallback-image");
  const placeholder = document.querySelector("#roi-media-placeholder");
  const polygon = document.querySelector("#roi-live-polygon");
  const pointGroup = document.querySelector("#roi-live-points");
  const startButton = document.querySelector("#roi-start");
  const finishButton = document.querySelector("#roi-finish");
  const resetButton = document.querySelector("#roi-reset");
  const cancelButton = document.querySelector("#roi-cancel");
  const classroomSelect = document.querySelector("#roi-classroom-select");
  const dialog = document.querySelector("#roi-student-dialog");
  const saveForm = document.querySelector("#roi-save-form");
  const seatSelect = document.querySelector("#roi-seat-select");
  const studentSelect = document.querySelector("#roi-student-select");
  const status = document.querySelector("#roi-live-status");
  const error = document.querySelector("#roi-save-error");
  const points = [];
  let peerConnection = null;

  const selectedClassroomId = () => classroomSelect?.value || editor.dataset.classroomId;
  const finishRegistration = () => {
    points.length = 0;
    renderPolygon();
    stage.classList.remove("is-registering");
    startButton.disabled = false;
    startButton.textContent = "등록 시작";
    startButton.classList.remove("is-registering");
    finishButton.disabled = true;
    resetButton.disabled = true;
    cancelButton.disabled = true;
  };
  const showPlaceholder = (title, message) => {
    video.hidden = true;
    fallbackImage.hidden = true;
    placeholder.hidden = false;
    placeholder.querySelector("strong").textContent = title;
    placeholder.querySelector("span").textContent = message;
    stage.dataset.state = "error";
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
  const stopStream = () => {
    if (peerConnection) peerConnection.close();
    peerConnection = null;
    if (video.srcObject) video.srcObject.getTracks().forEach((track) => track.stop());
    video.srcObject = null;
  };
  const connectWebRTC = async (cameraId) => {
    stopStream();
    const connection = new RTCPeerConnection();
    peerConnection = connection;
    connection.addTransceiver("video", {direction: "recvonly"});
    connection.addTransceiver("audio", {direction: "recvonly"});
    connection.addEventListener("track", (event) => {
      if (!event.streams?.[0]) return;
      video.srcObject = event.streams[0];
      video.hidden = false;
      fallbackImage.hidden = true;
      placeholder.hidden = true;
      stage.dataset.state = "connected";
      video.play().catch(() => {});
    });
    const offer = await connection.createOffer();
    await connection.setLocalDescription(offer);
    const response = await fetch(`http://${location.hostname}:8889/${encodeURIComponent(cameraId)}/whep`, {
      method: "POST", headers: {"Content-Type": "application/sdp"}, body: connection.localDescription.sdp,
    });
    if (!response.ok) throw new Error(`WHEP ${response.status}`);
    await connection.setRemoteDescription({type: "answer", sdp: await response.text()});
  };
  const loadClassroomMedia = async () => {
    const classroomId = selectedClassroomId();
    if (!classroomId) return showPlaceholder("연결 실패", "선택할 수 있는 강의실이 없습니다.");
    showPlaceholder("영상 연결 중", "강의실 실시간 모니터링 영상을 불러오고 있습니다.");
    try {
      const response = await fetch(`/api/v1/video-streams?classroom_id=${encodeURIComponent(classroomId)}`);
      if (!response.ok) throw new Error(`stream list ${response.status}`);
      const body = await response.json();
      const stream = body.items?.find((item) => item.is_demo === false && item.camera_id);
      if (!stream) throw new Error("stream not found");
      await connectWebRTC(stream.camera_id);
      status.textContent = `${stream.camera_label} 실시간 영상을 사용합니다.`;
    } catch (reason) {
      console.error("ROI 실시간 영상 연결 실패", reason);
      showPlaceholder("연결 실패", "실시간 모니터링 영상에 연결할 수 없습니다.");
      status.textContent = "5초 후 ROI 테스트용 대체 이미지를 표시합니다.";
      window.setTimeout(() => {
        if (selectedClassroomId() !== classroomId || stage.dataset.state !== "error") return;
        placeholder.hidden = true;
        video.hidden = true;
        fallbackImage.hidden = false;
        stage.dataset.state = "fallback";
        status.textContent = "영상 연결에 실패하여 ROI 테스트용 대체 이미지를 표시합니다.";
      }, 5000);
    }
  };

  classroomSelect?.addEventListener("change", () => {
    const url = new URL(location.href);
    url.searchParams.set("classroom_id", classroomSelect.value);
    location.assign(url);
  });
  startButton?.addEventListener("click", () => {
    points.length = 0;
    renderPolygon();
    startButton.disabled = true;
    startButton.textContent = "등록 중";
    startButton.classList.add("is-registering");
    finishButton.disabled = false;
    resetButton.disabled = false;
    cancelButton.disabled = false;
    stage.classList.add("is-registering");
    status.textContent = "영상 위를 클릭해 ROI 꼭짓점을 3개 이상 지정해 주세요.";
  });
  stage?.addEventListener("click", (event) => {
    if (!stage.classList.contains("is-registering") || event.target === placeholder) return;
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
    console.log("ROI 선택 완료", {classroom_id: selectedClassroomId(), polygon: points});
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
    const saveButton = document.querySelector("#roi-dialog-save");
    saveButton.disabled = true;
    error.hidden = true;
    const payload = {seat_id: seatSelect.value, student_id: studentSelect.value, polygon: points};
    try {
      const response = await fetch(`/api/v1/classrooms/${encodeURIComponent(selectedClassroomId())}/roi-connection`, {
        method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || "ROI를 저장하지 못했습니다.");
      console.log("ROI 연결 저장", {classroom_id: body.classroom_id, seat_id: body.seat_id, student_id: body.student_id, polygon: body.polygon});
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
  window.addEventListener("beforeunload", stopStream);
  loadClassroomMedia();
})();
