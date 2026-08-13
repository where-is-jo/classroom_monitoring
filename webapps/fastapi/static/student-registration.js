(() => {
  const form = document.querySelector("#student-registration-form");
  const notice = document.querySelector("#student-save-notice");
  const dialog = document.querySelector("#student-face-enrollment-dialog");
  const openButton = document.querySelector("#open-face-enrollment");
  const closeButton = document.querySelector("#close-face-enrollment");
  const cancelButton = document.querySelector("#cancel-enrollment");
  const setup = document.querySelector("#enrollment-setup");
  const capture = document.querySelector("#capture-panel");
  const complete = document.querySelector("#face-enrollment-complete");
  if (!(form instanceof HTMLFormElement) || !(notice instanceof HTMLElement)) return;

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form).entries());
    console.log("[학생 등록 테스트] 저장할 학생 정보", values);
    form.reset();
    notice.hidden = false;
    notice.focus();
    window.scrollTo({top: 0, behavior: "smooth"});
  });

  form.addEventListener("reset", () => {
    notice.hidden = true;
    openButton?.classList.remove("is-complete");
    if (openButton) openButton.textContent = "얼굴 등록";
  });
  if (!(dialog instanceof HTMLDialogElement) || !(openButton instanceof HTMLButtonElement)) return;

  const closeDialog = () => {
    if (dialog.open) dialog.close();
    document.body.classList.remove("student-modal-open");
  };

  openButton.addEventListener("click", () => {
    const name = form.elements.namedItem("name");
    const studentNumber = form.elements.namedItem("student_number");
    if (!(name instanceof HTMLInputElement) || !(studentNumber instanceof HTMLInputElement)) return;
    if (!name.reportValidity() || !studentNumber.reportValidity()) return;
    document.querySelector("#face-student-name").textContent = name.value.trim();
    document.querySelector("#student-id").textContent = studentNumber.value.trim();
    document.querySelector("#consent-confirmed").checked = false;
    document.querySelector("#setup-error").hidden = true;
    document.querySelector("#capture-error").hidden = true;
    setup.hidden = false;
    capture.hidden = true;
    complete.hidden = true;
    dialog.showModal();
    document.body.classList.add("student-modal-open");
  });

  closeButton?.addEventListener("click", closeDialog);
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    if (!capture.hidden) cancelButton?.click(); else closeDialog();
  });
  dialog.addEventListener("close", () => document.body.classList.remove("student-modal-open"));

  document.addEventListener("face-enrollment:aborted", closeDialog);
  document.addEventListener("face-enrollment:complete", () => {
    capture.hidden = true;
    complete.hidden = false;
    openButton.textContent = "얼굴 등록 완료";
    openButton.classList.add("is-complete");
    window.setTimeout(closeDialog, 1800);
  });
})();
