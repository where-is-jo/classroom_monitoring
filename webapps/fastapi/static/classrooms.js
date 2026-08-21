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
    occupancyEventSource: null,
    studentStateEventSource: null,
  };

  function subscribeOccupancySSE(classroomId) {
    /* 기존 연결 해제 후 새로 맺는다 */
    if (state.occupancyEventSource) {
      state.occupancyEventSource.close();
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

    state.occupancyEventSource = eventSource;
  }

  function loadStudentStates(classroomId) {
    return fetch(
      "/api/v1/classrooms/" + encodeURIComponent(classroomId) + "/student-states",
      { headers: { Accept: "application/json" } }
    )
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (body) {
        (body.states || []).forEach(updateStudentState);
      })
      .catch(function (error) {
        console.warn("초기 학생 상태를 불러오지 못했습니다:", error);
      });
  }

  function subscribeStudentStateSSE(classroomId, onState) {
    if (state.studentStateEventSource) {
      state.studentStateEventSource.close();
    }
    const eventSource = new EventSource(
      "/api/v1/classrooms/" + encodeURIComponent(classroomId) + "/student-state-events"
    );
    eventSource.addEventListener("student-state", function (event) {
      try {
        onState(JSON.parse(event.data));
      } catch (error) {
        /* malformed event 하나만 버리고 EventSource의 기본 재연결을 유지한다. */
        console.error("학생 상태 SSE 데이터 파싱 오류:", error);
      }
    });
    eventSource.onerror = function () {
      console.warn("학생 상태 SSE 연결 끊김 — 자동 재연결을 시도합니다.");
    };
    state.studentStateEventSource = eventSource;
  }

  function updateStudentState(data) {
    if (!data || !data.student_id) return;
    document.querySelectorAll("[data-assigned-student-id]").forEach(function (element) {
      if (element.dataset.assignedStudentId !== String(data.student_id)) return;

      setText(element, "[data-student-name]", data.student_name || "-");
      setText(element, "[data-student-no]", data.student_no || "-");
      setText(element, "[data-assigned-seat-label]", data.assigned_seat_label || "-");
      setText(element, "[data-current-seat-label]", data.current_seat_label || "-");
      setText(element, "[data-student-confidence]", formatConfidence(data.confidence));
      setText(
        element,
        "[data-student-observed-at]",
        formatObservedAt(data.observed_at || data.last_observed_at)
      );

      const normalized = normalizeStudentState(data.current_state);
      const stateEl = element.querySelector("[data-student-state]");
      if (stateEl) {
        STUDENT_STATES.forEach(function (name) {
          stateEl.classList.remove("state--" + name);
        });
        stateEl.classList.add("state--" + normalized);
        stateEl.setAttribute("aria-label", "현재 상태 " + studentStateLabel(normalized));
        if (data.reason) stateEl.setAttribute("title", "근거: " + data.reason);
      }
      setText(element, "[data-student-state-label]", studentStateLabel(normalized));
      setText(element, "[data-student-state-icon]", studentStateIcon(normalized));
      element.dataset.studentState = normalized;
    });
  }

  function setText(root, selector, value) {
    const element = root.querySelector(selector);
    if (element) element.textContent = value;
  }

  // 서버가 내려보내는 상태 어휘. 모르는 값이 오면 unknown으로 접는다 —
  // 화면이 새 상태를 임의로 해석해 이름을 붙이는 것보다 모른다고 말하는 편이 낫다.
  const STUDENT_STATES = ["present", "wrong_seat", "in_classroom", "absent", "unknown"];
  const STUDENT_STATE_LABELS = {
    present: "재석",
    wrong_seat: "잘못된 자리",
    in_classroom: "강의실 안",
    absent: "결석",
    unknown: "확인 필요",
  };
  const STUDENT_STATE_ICONS = {
    present: "●",
    wrong_seat: "▲",
    in_classroom: "◆",
    absent: "✕",
    unknown: "?",
  };

  function normalizeStudentState(value) {
    const normalized = String(value || "UNKNOWN").toLowerCase();
    return STUDENT_STATES.indexOf(normalized) === -1 ? "unknown" : normalized;
  }

  function studentStateLabel(value) {
    return STUDENT_STATE_LABELS[value] || STUDENT_STATE_LABELS.unknown;
  }

  function studentStateIcon(value) {
    return STUDENT_STATE_ICONS[value] || STUDENT_STATE_ICONS.unknown;
  }

  function formatConfidence(value) {
    const number = Number(value);
    return value == null || !Number.isFinite(number) ? "-" : Math.round(number * 100) + "%";
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
    if (!observedAt) return "-";
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
    if (state.occupancyEventSource) {
      state.occupancyEventSource.close();
      state.occupancyEventSource = null;
    }
    if (state.studentStateEventSource) {
      state.studentStateEventSource.close();
      state.studentStateEventSource = null;
    }
  }

  function init() {
    const occupancyDashboard = document.querySelector(
      "[data-classroom-dashboard][data-classroom-id]"
    );
    const studentDashboard = document.querySelector(
      "[data-student-state-dashboard][data-classroom-id]"
    );
    const dashboard = occupancyDashboard || studentDashboard;
    if (!dashboard) return;

    const classroomId = dashboard.dataset.classroomId;
    if (!classroomId) return;

    if (occupancyDashboard) subscribeOccupancySSE(classroomId);
    let initialStateLoaded = false;
    const pendingStudentStates = [];
    subscribeStudentStateSSE(classroomId, function (studentState) {
      if (initialStateLoaded) updateStudentState(studentState);
      else pendingStudentStates.push(studentState);
    });
    loadStudentStates(classroomId).then(function () {
      initialStateLoaded = true;
      pendingStudentStates.forEach(updateStudentState);
    });
  }

  /* 페이지 언로드 시 정리 */
  window.addEventListener("beforeunload", unsubscribe);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
