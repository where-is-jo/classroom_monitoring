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
        updateSeatStatus(data.seat_id, data.state, data.confidence, data.observed_at);
      } catch (err) {
        console.error("SSE 데이터 파싱 오류:", err);
      }
    });

    eventSource.onerror = () => {
      console.warn("SSE 연결 끊김 — 자동 재연결을 시도합니다.");
    };

    state.eventSource = eventSource;
  }

  function updateSeatStatus(seatId, seatState, confidence, observedAt) {
    const seatEl = document.querySelector('[data-seat-id="' + seatId + '"]');
    if (!seatEl) return;

    const label = displayLabel(seatState);
    const normalized = normalizeState(seatState);
    const previous = normalizeState(seatEl.dataset.seatState);
    seatEl.classList.remove("seat-card--occupied", "seat-card--vacant", "seat-card--unknown");
    seatEl.classList.add("seat-card--" + normalized);
    seatEl.dataset.seatState = normalized;

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

    if (previous !== normalized) {
      adjustCount(previous, -1);
      adjustCount(normalized, 1);
    }

    if (observedAt) {
      const observedEl = document.querySelector("[data-last-observed]");
      if (observedEl) {
        observedEl.textContent = " · 마지막 관측 " + formatObservedAt(observedAt);
      }
    }
  }

  function normalizeState(seatState) {
    const normalized = String(seatState || "UNKNOWN").toLowerCase();
    return normalized === "occupied" || normalized === "vacant" ? normalized : "unknown";
  }

  function adjustCount(stateName, delta) {
    const countEl = document.querySelector('[data-occupancy-count="' + stateName + '"]');
    if (!countEl) return;
    const current = Number(countEl.textContent);
    if (!Number.isFinite(current)) return;
    countEl.textContent = String(Math.max(0, current + delta));
  }

  function formatObservedAt(observedAt) {
    const value = new Date(observedAt);
    if (Number.isNaN(value.getTime())) return "-";
    return new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(value);
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
    const dashboard = document.querySelector("[data-classroom-id]");
    if (!dashboard) return;

    const classroomId = dashboard.dataset.classroomId;
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
