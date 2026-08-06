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
