document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.matches("[data-submit-state]")) return;
  const button = form.querySelector("button[type='submit']");
  if (button instanceof HTMLButtonElement) {
    button.disabled = true;
    button.textContent = button.dataset.pendingLabel || "처리 중…";
  }
  const pending = form.querySelector(".form-pending");
  if (pending instanceof HTMLElement) pending.hidden = false;
});
