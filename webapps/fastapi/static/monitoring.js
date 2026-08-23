/* monitoring.js — 실시간 모니터링 화면의 player lifecycle (TASK-004)
 *
 * 각 real card에 대해:
 *   1. FastAPI playback session 생성 (POST /api/v1/video-streams/{stream_id}/playback-sessions)
 *   2. 생성 응답의 FastAPI signaling URL로만 WebRTC(WHEP) offer 전송 (결정 0014)
 *   3. camera_id 기반 탐지 SSE 구독 → bbox overlay·탐지 수·마지막 탐지 갱신
 *   4. unload에서 EventSource·RTCPeerConnection·playback session을 모두 정리
 *
 * 브라우저는 MediaMTX 주소·포트·RTSP URL·credential을 구성하거나
 * 호출하지 않는다 (MON-005). 오류는 해당 카드에만 표시하고 다른 카드의
 * 재생·SSE를 중단하지 않는다.
 */

(function () {
  "use strict";

  /* camera_id → player context */
  const players = new Map();

  /* ── playback session (FastAPI 계약) ─────────────────────────────────── */

  function createPlaybackSession(streamId) {
    const url =
      "/api/v1/video-streams/" + encodeURIComponent(streamId) + "/playback-sessions";
    return fetch(url, { method: "POST" }).then(function (response) {
      if (!response.ok) {
        return parseErrorResponse(response).then(function (message) {
          throw new Error(message);
        });
      }
      return response.json();
    });
  }

  function parseErrorResponse(response) {
    return response
      .json()
      .then(function (body) {
        const code =
          body && body.error && body.error.code
            ? body.error.code
            : "HTTP " + response.status;
        return code;
      })
      .catch(function () {
        return "HTTP " + response.status;
      });
  }

  /* ── WebRTC player (FastAPI signaling proxy 경유, MediaMTX 미접근) ──── */

  function initWebRTCPlayer(ctx) {
    const pc = new RTCPeerConnection();
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.addTransceiver("audio", { direction: "recvonly" });
    pc.addEventListener("track", function (event) {
      if (event.streams && event.streams[0]) {
        ctx.videoEl.srcObject = event.streams[0];
        // 해상도는 메타데이터가 도착해야 알 수 있다. resize도 함께 듣는다 —
        // 카메라를 바꾸거나 스트림이 재협상되면 비율이 달라진다.
        ctx.videoEl.addEventListener("loadedmetadata", function () {
          applyVideoAspect(ctx.videoEl);
        });
        ctx.videoEl.addEventListener("resize", function () {
          applyVideoAspect(ctx.videoEl);
        });
      }
    });
    ctx.pc = pc;

    return pc
      .createOffer()
      .then(function (offer) {
        return pc.setLocalDescription(offer);
      })
      .then(function () {
        return fetch(ctx.signalingUrl, {
          method: "POST",
          headers: { "Content-Type": "application/sdp" },
          body: pc.localDescription.sdp,
        });
      })
      .then(function (response) {
        if (!response.ok) {
          return response.text().then(function (text) {
            throw new Error(
              "signaling HTTP " + response.status + (text ? " " + text.slice(0, 120) : "")
            );
          });
        }
        return response.text();
      })
      .then(function (sdp) {
        return pc.setRemoteDescription({ type: "answer", sdp: sdp });
      })
      .then(function () {
        ctx.connected = true;
      });
  }

  /* ── bbox overlay ─────────────────────────────────────────────────────── */

  /** 영상의 실제 비율을 프레임 상자에 알린다.
   *
   * 카메라마다 비율이 다르다 — 강의실 CCTV는 세로가 긴 어안(1280x1944)이고 입구
   * 카메라는 가로형이다. CSS에 16:9를 박아 두면 세로형이 잘리므로, 메타데이터가
   * 오는 즉시 실제 값으로 바꾼다.
   */
  function applyVideoAspect(videoEl) {
    const frame = videoEl.closest(".camera-monitoring-frame");
    if (!frame || !videoEl.videoWidth || !videoEl.videoHeight) return;
    frame.style.setProperty(
      "--frame-aspect",
      videoEl.videoWidth + " / " + videoEl.videoHeight
    );
  }

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
    if (!overlay) return;
    overlay.innerHTML = "";
    if (!detections || detections.length === 0) return;

    const rect = overlay.getBoundingClientRect();
    if (!rect.width || !rect.height || !frameWidth || !frameHeight) return;

    // 영상은 object-fit:contain으로 그려진다. 상자와 영상의 비율이 다르면 위아래나
    // 좌우에 여백이 생기고, 그 여백을 무시하고 상자 크기로 환산하면 bbox가 사람에서
    // 어긋난다. 실제로 영상이 차지하는 사각형을 구해 거기에 맞춘다.
    const scale = Math.min(rect.width / frameWidth, rect.height / frameHeight);
    const drawnWidth = frameWidth * scale;
    const drawnHeight = frameHeight * scale;
    const offsetX = (rect.width - drawnWidth) / 2;
    const offsetY = (rect.height - drawnHeight) / 2;

    detections.forEach(function (det) {
      if (!det.bbox || det.bbox.length < 4) return;
      const x1 = det.bbox[0];
      const y1 = det.bbox[1];
      const x2 = det.bbox[2];
      const y2 = det.bbox[3];

      const box = document.createElement("div");
      box.className = "bbox-box";
      box.style.cssText =
        "position:absolute;border:2px solid #00ff88;background:rgba(0,255,136,0.1);";
      box.style.left = offsetX + x1 * scale + "px";
      box.style.top = offsetY + y1 * scale + "px";
      box.style.width = (x2 - x1) * scale + "px";
      box.style.height = (y2 - y1) * scale + "px";

      const label = document.createElement("span");
      label.className = "bbox-label";
      label.style.cssText =
        "position:absolute;top:-20px;left:0;padding:2px 6px;background:#00ff88;color:#000;font-size:11px;font-weight:700;border-radius:3px;white-space:nowrap;";
      label.textContent =
        (det.display_label || "사람") +
        " " +
        Math.round((det.confidence || 0) * 100) +
        "%";
      box.appendChild(label);

      overlay.appendChild(box);
    });
  }

  /* ── 카드 한정 오류 ──────────────────────────────────────────────────── */

  function showCardError(ctx, message) {
    const errorEl = ctx.card.querySelector("[data-video-error]");
    if (!errorEl) return;
    errorEl.textContent = message;
    errorEl.hidden = false;
  }

  /* ── SSE 구독 (camera_id 기반 탐지 수신, MON-005) ───────────────────── */

  function subscribeSSE(ctx) {
    const url =
      "/api/v1/video-streams/" + encodeURIComponent(ctx.cameraId) + "/detection-events";
    const eventSource = new EventSource(url);
    ctx.eventSource = eventSource;

    eventSource.addEventListener("detection", function (event) {
      try {
        const data = JSON.parse(event.data);
        if (data.frame && data.detections) {
          drawBbox(ctx.overlay, data.detections, data.frame.width_pixels, data.frame.height_pixels);
        }
        updateDetectionInfo(ctx, data);
      } catch (err) {
        /* malformed event는 해당 event만 무시한다 (SPEC §4). */
        console.error("SSE 데이터 파싱 오류:", err);
      }
    });

    eventSource.onerror = function () {
      /* EventSource 기본 재연결 동작에 맡긴다 (SPEC §4). */
      console.warn("SSE 연결 끊김:", ctx.cameraId, "- 자동 재연결 시도");
    };
  }

  function updateDetectionInfo(ctx, data) {
    const countEl = ctx.card.querySelector("[data-detection-count]");
    if (countEl) {
      const count =
        data.detections_count != null
          ? data.detections_count
          : data.detections
            ? data.detections.length
            : 0;
      countEl.textContent = String(count);
    }

    const timeEl = ctx.card.querySelector("[data-last-detection]");
    if (timeEl && data.captured_at) {
      const d = new Date(data.captured_at);
      if (!isNaN(d.getTime())) {
        timeEl.textContent = d.toLocaleString("ko-KR");
      }
    }
  }

  /* ── 카드 초기화 (오류는 카드 한정) ──────────────────────────────────── */

  function initCard(card) {
    const streamId = card.dataset.streamId;
    const cameraId = card.dataset.cameraId;
    if (!streamId || !cameraId) return;
    if (players.has(cameraId)) return;

    const videoEl = card.querySelector("video[data-webrtc]");
    const ctx = {
      card: card,
      streamId: streamId,
      cameraId: cameraId,
      videoEl: videoEl,
      overlay: videoEl ? createOverlayContainer(videoEl) : null,
      signalingUrl: null,
      sessionId: null,
      pc: null,
      eventSource: null,
      connected: false,
    };
    players.set(cameraId, ctx);

    createPlaybackSession(streamId)
      .then(function (session) {
        ctx.sessionId = session.session_id;
        ctx.signalingUrl = session.signaling_url;
        if (!ctx.signalingUrl) {
          throw new Error("PLAYBACK_SESSION_MISSING_SIGNALING_URL");
        }
        if (videoEl) {
          return initWebRTCPlayer(ctx);
        }
      })
      .catch(function (err) {
        const reason = err && err.message ? err.message : String(err);
        showCardError(
          ctx,
          "영상을 시작하지 못했습니다. (" +
            reason +
            ") 연결 상태를 확인한 뒤 다시 시도해 주세요."
        );
      });

    /* SSE는 playback session 성공과 무관하게 구독한다 (탐지 수신). */
    subscribeSSE(ctx);
  }

  /* ── 전체화면 (MON-008, TASK-006) ────────────────────────────────────── */

  function initFullscreenToggle() {
    const toggle = document.querySelector("[data-fullscreen-toggle]");
    if (!toggle) return;
    const target = document.getElementById("main-content");
    if (!target || typeof target.requestFullscreen !== "function") {
      toggle.hidden = true;
      return;
    }

    function updateState() {
      const isFullscreen = document.fullscreenElement === target;
      toggle.setAttribute("aria-pressed", String(isFullscreen));
      const label = toggle.querySelector("[data-fullscreen-label]");
      if (label) {
        label.textContent = isFullscreen ? "전체화면 종료" : "전체화면";
      }
    }

    toggle.addEventListener("click", function () {
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else {
        target.requestFullscreen();
      }
    });

    /* ESC·시스템 종료로 빠져나오는 경우에도 상태를 반영한다. */
    document.addEventListener("fullscreenchange", updateState);
  }

  /* ── unload 정리 (MON-005) ───────────────────────────────────────────── */

  /** 카드 하나의 재생 자원을 되돌린다.
   *
   * 카메라를 전환할 때도 쓴다. 보이지 않는 카메라의 WebRTC와 SSE를 열어 두면
   * 화면에 없는 영상을 계속 받아 대역과 CPU를 쓰고, playback session도 살아 있는
   * 채로 쌓인다.
   */
  function teardownCard(ctx) {
    if (ctx.eventSource) {
      ctx.eventSource.close();
      ctx.eventSource = null;
    }
    if (ctx.pc) {
      try {
        ctx.pc.close();
      } catch (err) {
        /* 이미 닫힌 connection은 무시한다. */
      }
      ctx.pc = null;
    }
    if (ctx.videoEl && ctx.videoEl.srcObject) {
      try {
        ctx.videoEl.srcObject.getTracks().forEach(function (track) {
          track.stop();
        });
      } catch (err) {
        /* srcObject 해제 실패는 무시한다. */
      }
      ctx.videoEl.srcObject = null;
    }
    if (ctx.signalingUrl) {
      /* DELETE는 idempotent (결정 0014). 실패는 server log/metric 대상이며
       * local session을 다시 활성화하지 않는다. */
      try {
        fetch(ctx.signalingUrl, { method: "DELETE" }).catch(function () {});
      } catch (err) {
        /* fetch 실패는 무시한다. */
      }
      ctx.signalingUrl = null;
    }
    ctx.connected = false;
  }

  /* ── 카메라 전환 ──────────────────────────────────────────────────────── */

  /** 선택한 카메라만 화면에 두고 재생한다.
   *
   * 켜 둔 채 숨기지 않고 실제로 끊는다. 화면에 없는 영상을 계속 받을 이유가 없다.
   */
  function activateCamera(cameraId) {
    document.querySelectorAll("[data-real-stream]").forEach(function (card) {
      const isTarget = card.dataset.cameraId === cameraId;
      card.hidden = !isTarget;
      if (isTarget) return;
      const ctx = players.get(card.dataset.cameraId);
      if (ctx) {
        teardownCard(ctx);
        players.delete(card.dataset.cameraId);
      }
    });
    document.querySelectorAll("[data-camera-tab]").forEach(function (tab) {
      const isTarget = tab.dataset.cameraTab === cameraId;
      tab.setAttribute("aria-selected", isTarget ? "true" : "false");
      tab.tabIndex = isTarget ? 0 : -1;
    });
    const target = document.querySelector(
      '[data-real-stream][data-camera-id="' + cameraId + '"]'
    );
    if (target) initCard(target);
  }

  function initCameraSwitcher() {
    const tabs = Array.prototype.slice.call(
      document.querySelectorAll("[data-camera-tab]")
    );
    if (tabs.length === 0) return;
    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () {
        activateCamera(tab.dataset.cameraTab);
      });
      // role=tablist는 좌우 방향키 이동을 기대한다. 버튼만으로는 Tab 키로만
      // 옮길 수 있어 탭 목록의 관례와 어긋난다.
      tab.addEventListener("keydown", function (event) {
        let next = null;
        if (event.key === "ArrowRight") next = tabs[(index + 1) % tabs.length];
        else if (event.key === "ArrowLeft") next = tabs[(index - 1 + tabs.length) % tabs.length];
        else if (event.key === "Home") next = tabs[0];
        else if (event.key === "End") next = tabs[tabs.length - 1];
        if (!next) return;
        event.preventDefault();
        activateCamera(next.dataset.cameraTab);
        next.focus();
      });
    });
  }

  function cleanupAll() {
    players.forEach(function (ctx) {
      if (ctx.eventSource) {
        ctx.eventSource.close();
        ctx.eventSource = null;
      }
      if (ctx.pc) {
        try {
          ctx.pc.close();
        } catch (err) {
          /* 이미 닫힌 connection은 무시한다. */
        }
        ctx.pc = null;
      }
      if (ctx.videoEl && ctx.videoEl.srcObject) {
        try {
          ctx.videoEl.srcObject.getTracks().forEach(function (track) {
            track.stop();
          });
        } catch (err) {
          /* srcObject 해제 실패는 무시한다. */
        }
        ctx.videoEl.srcObject = null;
      }
      if (ctx.signalingUrl) {
        /* DELETE는 idempotent (결정 0014). 실패는 server log/metric 대상이며
         * local session을 다시 활성화하지 않는다. */
        try {
          fetch(ctx.signalingUrl, { method: "DELETE" }).catch(function () {});
        } catch (err) {
          /* fetch 실패는 무시한다. */
        }
      }
    });
    players.clear();
  }

  /* ── 초기화 ───────────────────────────────────────────────────────────── */

  function init() {
    /* **보이는 카드 하나만 연결한다.** 예전에는 모든 카드를 한꺼번에 열었지만
     * 이제 화면에 한 대만 나오므로, 나머지까지 WebRTC를 맺으면 보지도 않는 영상을
     * 받게 된다. 나머지는 탭으로 전환할 때 연결한다. */
    const visible = Array.prototype.filter.call(
      document.querySelectorAll("[data-real-stream]"),
      function (card) {
        return !card.hidden;
      }
    );
    visible.forEach(initCard);
    initCameraSwitcher();
    initFullscreenToggle();
  }

  window.addEventListener("beforeunload", cleanupAll);
  window.addEventListener("pagehide", cleanupAll);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
