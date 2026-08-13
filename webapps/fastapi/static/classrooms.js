/* classrooms.js — 강의실 좌석 현황 화면의 SSE 구독과 좌석 상태 갱신
 * /classrooms?classroom_id=... 화면에서 좌석 배치도의 좌석 카드를 실시간으로 갱신한다.
 * - data-classroom-id: SSE를 구독할 강의실 id (좌석 배치도 컨테이너)
 * - data-seat-id: SSE 이벤트로 갱신할 좌석 카드
 * 서버가 보내는 occupancy 이벤트의 seat_id·state·confidence로 카드의 색상,
 * 아이콘, 상태 문구, 신뢰도를 바꾼다.
 */

(function () {
  "use strict";

  const state = {
    eventSource: null,
  };

  function subscribeSSE(classroomId) {
    /* 기존 연결 해제 후 새로 맺는다 */
    if (state.eventSource) {
      state.eventSource.close();
    }

    const eventSource = new EventSource(
      "/api/v1/classrooms/" + encodeURIComponent(classroomId) + "/occupancy-events"
    );

    eventSource.addEventListener("occupancy", (event) => {
      try {
        const data = JSON.parse(event.data);
        updateSeatStatus(data.seat_id, data.state, data.confidence);
      } catch (err) {
        console.error("SSE 데이터 파싱 오류:", err);
      }
    });

    eventSource.onerror = () => {
      console.warn("SSE 연결 끊김 — 자동 재연결을 시도합니다.");
    };

    state.eventSource = eventSource;
  }

  function updateSeatStatus(seatId, seatState, confidence) {
    const seatEl = document.querySelector('[data-seat-id="' + seatId + '"]');
    if (!seatEl) return;

    const label = displayLabel(seatState);
    const normalized = String(seatState || "UNKNOWN").toLowerCase();
    seatEl.className = "seat-card seat-card--" + normalized;

    const stateEl = seatEl.querySelector(".state");
    if (stateEl) {
      stateEl.setAttribute("aria-label", "좌석 상태 " + label);
    }

    const labelEl = seatEl.querySelector(".state-label");
    if (labelEl) {
      labelEl.textContent = label;
    }

    const iconEl = seatEl.querySelector(".state-icon");
    if (iconEl) {
      iconEl.textContent = stateIcon(seatState);
    }

    const confEl = seatEl.querySelector(".confidence");
    if (confEl) {
      confEl.textContent = confidence == null ? "" : Math.round(confidence * 100) + "%";
    }
  }

  function displayLabel(seatState) {
    switch (seatState) {
      case "OCCUPIED":
        return "재석";
      case "VACANT":
        return "부재";
      default:
        return "확인 필요";
    }
  }

  function stateIcon(seatState) {
    switch (seatState) {
      case "OCCUPIED":
        return "●";
      case "VACANT":
        return "○";
      default:
        return "?";
    }
  }

  function unsubscribe() {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
  }

  function init() {
    const seatMap = document.querySelector("[data-classroom-id]");
    if (!seatMap) return;

    const classroomId = seatMap.dataset.classroomId;
    if (!classroomId) return;

    subscribeSSE(classroomId);
  }

  /* 페이지 언로드 시 정리 */
  window.addEventListener("beforeunload", unsubscribe);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
