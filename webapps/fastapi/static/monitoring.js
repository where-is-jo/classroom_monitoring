/* monitoring.js — 실시간 모니터링 화면의 WebRTC player, bbox overlay, SSE */

(function () {
  "use strict";

  /* ── 상태 ── */
  const state = {
    sseConnections: new Map(),  /* camera_id → EventSource */
  };

  /* ── WebRTC player ── */
  function initWebRTCPlayer(videoEl, streamId) {
    /* WHEP signaling: POST /webrtc/{streamId} 로 SDP offer를 보내고 answer를 받는다 */
    const pc = new RTCPeerConfiguration();

    pc.addEventListener("track", (event) => {
      if (event.streams && event.streams[0]) {
        videoEl.srcObject = event.streams[0];
      }
    });

    pc.createOffer()
      .then((offer) => pc.setLocalDescription(offer))
      .then(() => {
        return fetch("/webrtc/" + streamId, {
          method: "POST",
          headers: { "Content-Type": "application/sdp" },
          body: pc.localDescription.sdp,
        });
      })
      .then((response) => {
        if (!response.ok) throw new Error("WebRTC signaling failed: " + response.status);
        return response.text();
      })
      .then((sdp) => {
        return pc.setRemoteDescription({ type: "answer", sdp: sdp });
      })
      .catch((err) => {
        console.error("WebRTC 오류:", err);
        showVideoError(videoEl.closest(".demo-video-frame"), "영상 연결 실패");
      });

    /* cleanup */
    videoEl._rtcpConnection = pc;
  }

  function stopWebRTC(videoEl) {
    if (videoEl._rtcpConnection) {
      videoEl._rtcpConnection.close();
      videoEl._rtcpConnection = null;
    }
    if (videoEl.srcObject) {
      videoEl.srcObject.getTracks().forEach((track) => track.stop());
      videoEl.srcObject = null;
    }
  }

  /* ── bbox overlay ── */
  function createOverlayContainer(videoEl) {
    let container = videoEl.parentElement.querySelector(".bbox-overlay");
    if (!container) {
      container = document.createElement("div");
      container.className = "bbox-overlay";
      container.style.cssText =
        "position:absolute;inset:0;pointer-events:none;overflow:hidden;";
      videoEl.parentElement.appendChild(container);
    }
    return container;
  }

  function drawBbox(overlay, detections, frameWidth, frameHeight) {
    /* 기존 bbox 제거 */
    overlay.innerHTML = "";

    if (!detections || detections.length === 0) return;

    const rect = overlay.getBoundingClientRect();
    const scaleX = rect.width / frameWidth;
    const scaleY = rect.height / frameHeight;

    detections.forEach((det) => {
      if (!det.bbox || det.bbox.length < 4) return;
      const [x1, y1, x2, y2] = det.bbox;
      const box = document.createElement("div");
      box.className = "bbox-box";
      box.style.cssText =
        "position:absolute;border:2px solid #00ff88;background:rgba(0,255,136,0.1);";
      box.style.left = x1 * scaleX + "px";
      box.style.top = y1 * scaleY + "px";
      box.style.width = (x2 - x1) * scaleX + "px";
      box.style.height = (y2 - y1) * scaleY + "px";

      /* 라벨 */
      const label = document.createElement("span");
      label.className = "bbox-label";
      label.style.cssText =
        "position:absolute;top:-20px;left:0;padding:2px 6px;background:#00ff88;color:#000;font-size:11px;font-weight:700;border-radius:3px;white-space:nowrap;";
      label.textContent = det.class_name + " " + Math.round((det.confidence || 0) * 100) + "%";
      box.appendChild(label);

      overlay.appendChild(box);
    });
  }

  function showVideoError(frameEl, message) {
    const errorEl = frameEl.querySelector(".demo-video-error") ||
      frameEl.querySelector(".demo-video-empty");
    if (errorEl) {
      errorEl.textContent = message;
      errorEl.hidden = false;
    }
  }

  /* ── SSE 탐지 이벤트 구독 ── */
  function subscribeSSE(cameraId, videoEl, overlay) {
    /* 기존 연결 해제 */
    if (state.sseConnections.has(cameraId)) {
      state.sseConnections.get(cameraId).close();
    }

    const eventSource = new EventSource("/api/v1/video-streams/" + cameraId + "/detection-events");

    eventSource.addEventListener("detection", (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.frame && data.detections) {
          drawBbox(overlay, data.detections, data.frame.width_pixels, data.frame.height_pixels);
        }
        /* 최근 탐지 정보 갱신 */
        updateDetectionInfo(cameraId, data);
      } catch (err) {
        console.error("SSE 데이터 파싱 오류:", err);
      }
    });

    eventSource.onerror = () => {
      console.warn("SSE 연결 끊김:", cameraId, "— 자동 재연결 시도");
    };

    state.sseConnections.set(cameraId, eventSource);
  }

  function unsubscribeAll() {
    state.sseConnections.forEach((es) => es.close());
    state.sseConnections.clear();
  }

  /* ── 탐지 정보 갱신 ── */
  function updateDetectionInfo(cameraId, data) {
    const card = document.querySelector('[data-camera-id="' + cameraId + '"]');
    if (!card) return;

    const countEl = card.querySelector("[data-detection-count]");
    if (countEl) countEl.textContent = data.detections_count || data.detections?.length || 0;

    const timeEl = card.querySelector("[data-last-detection]");
    if (timeEl && data.captured_at) {
      const d = new Date(data.captured_at);
      timeEl.textContent = d.toLocaleString("ko-KR");
    }
  }

  /* ── 카메라 ID 추출 ── */
  function getCameraIdFromStreamId(streamId) {
    /* streamId가 "stream-camera-a" 형태이면 "camera-a" 추출 */
    if (streamId.startsWith("stream-")) return streamId.slice(7);
    return streamId;
  }

  /* ── 초기화 ── */
  function init() {
    /* 실제 source 카드만 대상 (demo 제외) */
    document.querySelectorAll("[data-real-stream]").forEach((card) => {
      const streamId = card.dataset.streamId;
      const cameraId = getCameraIdFromStreamId(streamId);

      const videoEl = card.querySelector("video[data-webrtc]");
      const overlay = createOverlayContainer(videoEl);

      /* WebRTC player 시작 */
      if (videoEl) initWebRTCPlayer(videoEl, streamId);

      /* SSE 구독 */
      subscribeSSE(cameraId, videoEl, overlay);
    });
  }

  /* 페이지 언로드 시 정리 */
  window.addEventListener("beforeunload", unsubscribeAll);

  /* DOMContentLoaded */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();