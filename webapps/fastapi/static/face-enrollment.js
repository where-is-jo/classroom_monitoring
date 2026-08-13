(() => {
  const setup = document.querySelector("#enrollment-setup");
  if (!setup) return;
  const panel = document.querySelector("#capture-panel");
  const video = document.querySelector("#camera-preview");
  const canvas = document.querySelector("#capture-canvas");
  const guidance = document.querySelector("#guidance-message");
  const connection = document.querySelector("#connection-status");
  const stage = document.querySelector(".camera-stage");
  const progressRing = document.querySelector("#face-progress-ring");
  const directionLabels = Object.fromEntries(
    [...document.querySelectorAll(".ring-direction[data-pose]")].map((element) => [element.dataset.pose, element]),
  );
  const progressMarkers = [];
  const progressMarkerCount = 40;
  const poseLabels = {FRONT: "정면", LEFT: "왼쪽", RIGHT: "오른쪽", UP: "위", DOWN: "아래"};
  let stream = null;
  let socket = null;
  let timer = null;
  let enrollmentId = null;
  let completed = false;
  let frameInFlight = false;
  const guideCenter = {x: 0.5, y: 0.5};
  const guideRadius = {x: 0.28, y: 0.36};
  const previousPosePercent = {FRONT: 0, LEFT: 0, RIGHT: 0, UP: 0, DOWN: 0};

  const poseForAngle = (angle) => {
    const horizontal = Math.cos(angle);
    const vertical = Math.sin(angle);
    if (Math.abs(vertical) >= Math.abs(horizontal)) return vertical < 0 ? "UP" : "DOWN";
    return horizontal < 0 ? "LEFT" : "RIGHT";
  };

  const initializeProgressMarkers = () => {
    for (let index = 0; index < progressMarkerCount; index += 1) {
      const angle = -Math.PI / 2 + (index / progressMarkerCount) * Math.PI * 2;
      const marker = document.createElement("span");
      marker.className = "face-progress-marker";
      marker.dataset.pose = poseForAngle(angle);
      marker.dataset.angle = String(angle);
      marker.style.left = `${50 + Math.cos(angle) * 28}%`;
      marker.style.top = `${50 + Math.sin(angle) * 36}%`;
      marker.style.setProperty("--marker-angle", `${angle * 180 / Math.PI + 90}deg`);
      marker.setAttribute("aria-hidden", "true");
      progressRing.append(marker);
      progressMarkers.push(marker);
    }
  };

  const updateProgressRing = (enrollment) => {
    const progressByPose = Object.fromEntries(enrollment.pose_progress.map((item) => [item.pose, item.completion_percent]));
    for (const pose of ["LEFT", "RIGHT", "UP", "DOWN"]) {
      const posePercent = progressByPose[pose] || 0;
      const targetAngles = {UP: -Math.PI / 2, RIGHT: 0, DOWN: Math.PI / 2, LEFT: Math.PI};
      const angularDistance = (angle, target) => Math.abs(Math.atan2(Math.sin(angle - target), Math.cos(angle - target)));
      const poseMarkers = progressMarkers
        .filter((marker) => marker.dataset.pose === pose)
        .sort((first, second) => (
          angularDistance(Number(first.dataset.angle), targetAngles[pose])
          - angularDistance(Number(second.dataset.angle), targetAngles[pose])
        ));
      const filledCount = Math.round(posePercent / 100 * poseMarkers.length);
      poseMarkers.forEach((marker, index) => {
        marker.classList.toggle("is-filled", index < filledCount);
      });
      directionLabels[pose].classList.toggle("is-complete", posePercent >= 100);
    }
    const frontPercent = progressByPose.FRONT || 0;
    const frontProgress = document.querySelector("#front-progress");
    frontProgress.querySelector("strong").textContent = `${frontPercent}%`;
    frontProgress.style.setProperty("--front-progress", `${frontPercent * 3.6}deg`);
    frontProgress.classList.toggle("is-filled", frontPercent >= 100);
    if (frontPercent > previousPosePercent.FRONT) {
      frontProgress.classList.remove("is-pulsing");
      window.requestAnimationFrame(() => frontProgress.classList.add("is-pulsing"));
      window.setTimeout(() => frontProgress.classList.remove("is-pulsing"), 650);
    }
    Object.assign(previousPosePercent, progressByPose);
    const percent = ringCompletionPercent(enrollment);
    const status = enrollment.status;
    const isComplete = status === "COMPLETE";
    const targetPoseByGuidance = {TURN_LEFT: "LEFT", TURN_RIGHT: "RIGHT", LOOK_UP: "UP", LOOK_DOWN: "DOWN"};
    const targetPose = targetPoseByGuidance[enrollment.guidance_code];
    progressMarkers.forEach((marker) => marker.classList.remove("is-target"));
    for (const pose of ["LEFT", "RIGHT", "UP", "DOWN"]) {
      const isTarget = pose === targetPose;
      directionLabels[pose].classList.toggle("is-target", isTarget);
      if (isTarget) {
        progressMarkers
          .filter((marker) => marker.dataset.pose === pose && !marker.classList.contains("is-filled"))
          .forEach((marker) => marker.classList.add("is-target"));
      }
    }
    progressRing.classList.toggle("is-complete", isComplete);
    progressRing.setAttribute("aria-valuenow", String(percent));
    progressRing.setAttribute("aria-valuetext", isComplete ? "얼굴 데이터 수집 완료" : `얼굴 데이터 ${percent}% 수집`);
  };

  const ringCompletionPercent = (enrollment) => {
    const requiredPoseCount = enrollment.pose_progress.reduce((sum, item) => sum + item.required_count, 0);
    const acceptedPoseCount = enrollment.pose_progress.reduce((sum, item) => sum + Math.min(item.accepted_count, item.required_count), 0);
    const posePercent = requiredPoseCount > 0 ? Math.round(acceptedPoseCount / requiredPoseCount * 100) : 0;
    return Math.min(enrollment.completion_percent, posePercent);
  };

  initializeProgressMarkers();

  const cameraErrorMessage = (reason) => {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      return "카메라는 localhost 또는 HTTPS 주소에서만 사용할 수 있습니다. http://127.0.0.1:8000으로 접속해 주세요.";
    }
    const messages = {
      NotAllowedError: "카메라 권한이 차단되었습니다. 주소창 왼쪽의 사이트 설정에서 카메라를 허용해 주세요.",
      NotFoundError: "사용할 수 있는 카메라를 찾지 못했습니다. Windows 카메라 설정과 장치 연결을 확인해 주세요.",
      NotReadableError: "카메라를 열 수 없습니다. 카메라 앱, Zoom, Teams 등 카메라를 사용 중인 프로그램을 종료해 주세요.",
      OverconstrainedError: "카메라가 요청한 촬영 조건을 지원하지 않습니다.",
      AbortError: "카메라 연결이 중단되었습니다. 잠시 후 다시 시도해 주세요.",
      SecurityError: "브라우저 보안 설정이 카메라 사용을 차단했습니다.",
    };
    if (messages[reason?.name]) return messages[reason.name];
    if (reason?.name === "Error" && reason?.message) return reason.message;
    return `카메라 연결에 실패했습니다${reason?.message ? `: ${reason.message}` : "."}`;
  };

  const render = (value) => {
    frameInFlight = false;
    const enrollment = value.enrollment || value;
    guidance.textContent = enrollment.guidance_message;
    const overall = document.querySelector("#overall-progress");
    overall.value = enrollment.completion_percent;
    overall.textContent = `${enrollment.completion_percent}%`;
    document.querySelector("#overall-progress-percent").textContent = `${enrollment.completion_percent}%`;
    updateProgressRing(enrollment);
    document.querySelector("#pose-progress").replaceChildren(...enrollment.pose_progress.map((item) => {
      const li = document.createElement("li");
      li.className = "pose-row";
      li.innerHTML = `<span>${poseLabels[item.pose] || item.pose}</span><progress max="100" value="${item.completion_percent}">${item.completion_percent}%</progress><span>${item.accepted_count}/${item.required_count}</span>`;
      return li;
    }));
    stage.dataset.state = value.accepted ? "capturing" : value.rejection_code === "POSE_QUOTA_FILLED" ? "searching" : "error";
    if (enrollment.status === "COMPLETE") {
      completed = true;
      connection.textContent = "등록 완료";
      stopCapture();
    }
  };

  const stopCapture = () => {
    if (timer) window.clearInterval(timer);
    timer = null;
    if (stream) stream.getTracks().forEach((track) => track.stop());
    stream = null;
    video.srcObject = null;
  };

  const sendFrame = () => {
    if (!socket || socket.readyState !== WebSocket.OPEN || video.readyState < 2 || frameInFlight) return;
    canvas.width = Math.min(video.videoWidth, 640);
    canvas.height = Math.round(canvas.width * video.videoHeight / video.videoWidth);
    const context = canvas.getContext("2d");
    context.fillStyle = "#101817";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.save();
    context.beginPath();
    context.ellipse(
      canvas.width * guideCenter.x,
      canvas.height * guideCenter.y,
      canvas.width * guideRadius.x,
      canvas.height * guideRadius.y,
      0,
      0,
      Math.PI * 2,
    );
    context.clip();
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    context.restore();
    canvas.toBlob((blob) => {
      if (blob && socket.readyState === WebSocket.OPEN) {
        frameInFlight = true;
        socket.send(blob);
      }
    }, "image/jpeg", 0.82);
  };

  const openCamera = async () => {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      throw new DOMException("안전한 브라우저 주소가 필요합니다.", "SecurityError");
    }
    stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: {ideal: "user"},
        width: {ideal: 640},
        height: {ideal: 480},
      },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();
  };

  const connectAnalysis = () => {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${protocol}://${location.host}/api/v1/face-enrollments/${enrollmentId}/frames`);
    socket.addEventListener("open", () => { connection.textContent = "AI 분석 연결됨"; timer = window.setInterval(sendFrame, 100); });
    socket.addEventListener("message", (event) => {
      const value = JSON.parse(event.data);
      if (value.error) {
        frameInFlight = false;
        const captureError = document.querySelector("#capture-error");
        captureError.textContent = value.error.message;
        captureError.hidden = false;
        stage.dataset.state = "error";
        return;
      }
      render(value);
    });
    socket.addEventListener("close", () => { stopCapture(); if (!completed) { connection.textContent = "연결이 종료되어 등록 데이터가 폐기되었습니다."; stage.dataset.state = "error"; } });
    socket.addEventListener("error", () => { connection.textContent = "AI 서버 연결에 실패했습니다."; stage.dataset.state = "error"; });
  };

  document.querySelector("#start-enrollment").addEventListener("click", async () => {
    const error = document.querySelector("#setup-error");
    error.hidden = true;
    try {
      await openCamera();
      const response = await fetch(`/api/v1/students/${encodeURIComponent(document.querySelector("#student-id").textContent)}/face-enrollments`, {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({consent_confirmed: document.querySelector("#consent-confirmed").checked, consent_confirmed_by: document.querySelector("#confirmed-by").value})
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || "등록 세션을 만들지 못했습니다.");
      enrollmentId = body.id;
      render(body);
      setup.hidden = true;
      panel.hidden = false;
      connectAnalysis();
    } catch (reason) {
      stopCapture();
      if (enrollmentId) {
        await fetch(`/api/v1/face-enrollments/${enrollmentId}`, {method: "DELETE"});
        enrollmentId = null;
      }
      error.textContent = cameraErrorMessage(reason);
      error.hidden = false;
    }
  });

  document.querySelector("#cancel-enrollment").addEventListener("click", async () => {
    stopCapture(); if (socket) socket.close();
    if (enrollmentId) await fetch(`/api/v1/face-enrollments/${enrollmentId}`, {method: "DELETE"});
    location.reload();
  });
})();
