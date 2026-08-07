const navToggle = document.querySelector(".nav-toggle");
const primaryNavigation = document.querySelector("#primary-navigation");

if (navToggle instanceof HTMLButtonElement && primaryNavigation instanceof HTMLElement) {
  document.body.classList.add("nav-enhanced");
  navToggle.addEventListener("click", () => {
    const isOpen = document.body.classList.toggle("nav-open");
    navToggle.setAttribute("aria-expanded", String(isOpen));
  });
  primaryNavigation.addEventListener("click", (event) => {
    if (event.target instanceof Element && event.target.closest("a")) {
      document.body.classList.remove("nav-open");
      navToggle.setAttribute("aria-expanded", "false");
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("nav-open")) {
      document.body.classList.remove("nav-open");
      navToggle.setAttribute("aria-expanded", "false");
      navToggle.focus();
    }
  });
}

const notificationPopover = document.querySelector("[data-notification-popover]");

if (notificationPopover instanceof HTMLDetailsElement) {
  const trigger = notificationPopover.querySelector("summary");
  const panel = notificationPopover.querySelector(".notification-popover-panel");
  const statusRegion = notificationPopover.querySelector("[data-notification-status]");
  const list = notificationPopover.querySelector("[data-notification-list]");
  const retryButton = notificationPopover.querySelector("[data-notification-retry]");
  const readAllButton = notificationPopover.querySelector("[data-notification-read-all]");
  const closeButton = notificationPopover.querySelector("[data-notification-close]");
  const badge = notificationPopover.querySelector("[data-notification-badge]");
  const notificationType = notificationPopover.dataset.notificationType || "";
  const csrfToken = notificationPopover.dataset.csrfToken || "";
  let loaded = false;

  const operationId = () => {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
  };

  const writeHeaders = () => ({
    "Content-Type": "application/json",
    "X-CSRF-Token": csrfToken,
  });

  const updateBadge = (count) => {
    if (!(badge instanceof HTMLElement)) return;
    badge.textContent = String(count);
    badge.hidden = count === 0;
    badge.setAttribute("aria-label", `읽지 않은 알림 ${count}개`);
  };

  const setError = () => {
    if (statusRegion instanceof HTMLElement) {
      statusRegion.hidden = false;
      statusRegion.className = "alert alert--error";
      statusRegion.textContent = "알림을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
    }
    if (retryButton instanceof HTMLButtonElement) retryButton.hidden = false;
    if (readAllButton instanceof HTMLButtonElement) readAllButton.hidden = true;
  };

  const markRead = async (notification) => {
    if (!notification.is_read) {
      const response = await fetch(`/api/v1/notifications/${encodeURIComponent(notification.id)}`, {
        method: "PATCH",
        credentials: "same-origin",
        headers: writeHeaders(),
        body: JSON.stringify({ operation_id: operationId() }),
      });
      if (!response.ok) throw new Error("notification mark read failed");
    }
    if (notification.target_route) window.location.assign(notification.target_route);
  };

  const renderItems = (items) => {
    if (!(list instanceof HTMLElement) || !(statusRegion instanceof HTMLElement)) return;
    list.replaceChildren();
    if (items.length === 0) {
      statusRegion.hidden = false;
      statusRegion.className = "empty";
      statusRegion.textContent = "표시할 알림이 없습니다.";
      if (readAllButton instanceof HTMLButtonElement) readAllButton.hidden = true;
      return;
    }
    statusRegion.hidden = true;
    items.forEach((notification) => {
      const item = document.createElement("li");
      item.className = `notification-popover-item${notification.is_read ? " is-read" : ""}`;
      const type = document.createElement("span");
      type.className = "eyebrow";
      type.textContent = notification.type === "INTERVIEW_WAIT_READY" ? "면담 가능" : "마감 후 좌석";
      const title = document.createElement("strong");
      title.textContent = notification.title;
      const body = document.createElement("p");
      body.textContent = notification.body;
      const time = document.createElement("time");
      time.dateTime = notification.created_at;
      time.textContent = new Intl.DateTimeFormat("ko-KR", {
        dateStyle: "short",
        timeStyle: "short",
      }).format(new Date(notification.created_at));
      const action = document.createElement("button");
      action.type = "button";
      action.className = "button--ghost";
      action.textContent = notification.target_route ? "알림 열기" : "읽음으로 표시";
      action.addEventListener("click", async () => {
        action.disabled = true;
        try {
          await markRead(notification);
          item.classList.add("is-read");
          if (!notification.target_route) await loadNotifications();
        } catch {
          action.disabled = false;
          setError();
        }
      });
      item.append(type, title, body, time, action);
      list.append(item);
    });
    if (readAllButton instanceof HTMLButtonElement) {
      readAllButton.hidden = !items.some((item) => !item.is_read);
    }
  };

  const loadNotifications = async () => {
    if (!(statusRegion instanceof HTMLElement)) return;
    statusRegion.hidden = false;
    statusRegion.className = "";
    statusRegion.textContent = "알림을 불러오는 중입니다.";
    if (retryButton instanceof HTMLButtonElement) retryButton.hidden = true;
    try {
      const query = new URLSearchParams({ limit: "10", offset: "0", type: notificationType });
      const unreadQuery = new URLSearchParams({
        limit: "1",
        offset: "0",
        type: notificationType,
        is_read: "false",
      });
      const [itemsResponse, unreadResponse] = await Promise.all([
        fetch(`/api/v1/notifications?${query}`, { credentials: "same-origin" }),
        fetch(`/api/v1/notifications?${unreadQuery}`, { credentials: "same-origin" }),
      ]);
      if (!itemsResponse.ok || !unreadResponse.ok) throw new Error("notification load failed");
      const payload = await itemsResponse.json();
      const unreadPayload = await unreadResponse.json();
      renderItems(payload.items.slice(0, 10));
      updateBadge(unreadPayload.total);
      loaded = true;
    } catch {
      setError();
    }
  };

  notificationPopover.addEventListener("toggle", () => {
    if (notificationPopover.open && !loaded) loadNotifications();
  });
  if (retryButton instanceof HTMLButtonElement) retryButton.addEventListener("click", loadNotifications);
  if (closeButton instanceof HTMLButtonElement) {
    closeButton.addEventListener("click", () => {
      notificationPopover.open = false;
      if (trigger instanceof HTMLElement) trigger.focus();
    });
  }
  if (readAllButton instanceof HTMLButtonElement) {
    readAllButton.addEventListener("click", async () => {
      readAllButton.disabled = true;
      try {
        const response = await fetch("/api/v1/notification-read-batches", {
          method: "POST",
          credentials: "same-origin",
          headers: writeHeaders(),
          body: JSON.stringify({ operation_id: operationId() }),
        });
        if (!response.ok) throw new Error("notification mark all read failed");
        await loadNotifications();
      } catch {
        setError();
      } finally {
        readAllButton.disabled = false;
      }
    });
  }
  notificationPopover.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      notificationPopover.open = false;
      if (trigger instanceof HTMLElement) trigger.focus();
      return;
    }
    if (event.key !== "Tab" || !(panel instanceof HTMLElement)) return;
    const focusable = [...panel.querySelectorAll("button:not([disabled]), a[href], input, select")];
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
}

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.matches("[data-submit-state]")) return;
  form.setAttribute("aria-busy", "true");
  const button = event.submitter instanceof HTMLButtonElement
    ? event.submitter
    : form.querySelector("button[type='submit'], button:not([type])");
  if (button instanceof HTMLButtonElement) {
    button.disabled = true;
    button.textContent = button.dataset.pendingLabel || "처리 중…";
  }
  const pending = form.querySelector(".form-pending");
  if (pending instanceof HTMLElement) pending.hidden = false;
});
