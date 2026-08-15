(() => {
  const registrationDialog = document.querySelector("#student-registration-dialog");
  const registrationForm = document.querySelector("#student-registration-form");
  const registrationOpenButton = document.querySelector("#open-student-registration");
  const faceDialog = document.querySelector("#student-face-enrollment-dialog");
  const setup = document.querySelector("#enrollment-setup");
  const capture = document.querySelector("#capture-panel");
  const complete = document.querySelector("#face-enrollment-complete");
  let activeStudentId = null;
  let registrationBusy = false;

  const setModalState = () => {
    document.body.classList.toggle(
      "student-modal-open",
      Boolean(registrationDialog?.open || faceDialog?.open),
    );
  };
  const closeDialog = (dialog) => {
    if (dialog instanceof HTMLDialogElement && dialog.open) dialog.close();
    setModalState();
  };
  const closeOnBackdrop = (dialog) => dialog?.addEventListener("click", (event) => {
    if (event.target === dialog && !registrationBusy) closeDialog(dialog);
  });

  const registrationControls = () => document.querySelectorAll([
    "#open-student-registration",
    "#student-registration-dialog button",
    "#student-registration-dialog input",
    "#student-registration-dialog select",
  ].join(", "));

  const setRegistrationBusy = (busy) => {
    registrationBusy = busy;
    for (const control of registrationControls()) {
      if (busy) {
        control.dataset.registrationWasDisabled = control.disabled ? "true" : "false";
        control.disabled = true;
        continue;
      }
      const wasDisabled = control.dataset.registrationWasDisabled;
      if (wasDisabled !== undefined) {
        control.disabled = wasDisabled === "true";
        delete control.dataset.registrationWasDisabled;
      }
    }
  };

  const clearRegistrationError = () => {
    const error = registrationForm?.querySelector("#student-save-error");
    if (!error) return;
    error.textContent = "";
    error.hidden = true;
  };

  const showRegistrationError = (message) => {
    const error = registrationForm?.querySelector("#student-save-error");
    if (!error) return;
    error.textContent = message;
    error.hidden = false;
    error.focus();
  };

  registrationOpenButton?.addEventListener("click", () => {
    registrationForm?.reset();
    clearRegistrationError();
    registrationDialog.showModal();
    setModalState();
    registrationForm?.elements.name?.focus();
  });
  document.querySelector("#close-student-registration")?.addEventListener("click", () => closeDialog(registrationDialog));
  closeOnBackdrop(registrationDialog);
  registrationDialog?.addEventListener("cancel", (event) => {
    if (registrationBusy) event.preventDefault();
  });
  registrationDialog?.addEventListener("close", () => {
    setModalState();
    registrationOpenButton?.focus();
  });

  registrationForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!registrationForm.reportValidity()) return;
    const values = Object.fromEntries(new FormData(registrationForm).entries());
    values.phone = values.phone || null;
    values.face_enrollment_id = null;
    clearRegistrationError();
    setRegistrationBusy(true);
    try {
      let response;
      try {
        response = await fetch("/api/v1/students", {
          method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(values),
        });
      } catch {
        throw new Error("서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
      }
      let body = null;
      try {
        body = await response.json();
      } catch {
        // JSON이 아니면 아래의 안전한 기본 메시지를 사용한다.
      }
      if (!response.ok) {
        throw new Error(body?.error?.message || "학생 정보를 저장하지 못했습니다.");
      }
      if (
        !body
        || typeof body.id !== "string"
        || typeof body.student_number !== "string"
        || typeof body.name !== "string"
      ) {
        throw new Error("등록된 학생 정보를 확인하지 못했습니다.");
      }
      location.reload();
    } catch (reason) {
      showRegistrationError(
        reason instanceof Error ? reason.message : "학생 정보를 저장하지 못했습니다.",
      );
    } finally {
      setRegistrationBusy(false);
    }
  });

  document.querySelectorAll(".open-face-enrollment").forEach((button) => {
    button.addEventListener("click", () => {
      activeStudentId = button.dataset.studentId;
      document.querySelector("#student-id").textContent = activeStudentId;
      document.querySelector("#face-student-name").textContent = button.dataset.studentName;
      document.querySelector("#face-student-number").textContent = button.dataset.studentNumber;
      document.querySelector("#consent-confirmed").checked = false;
      document.querySelector("#setup-error").hidden = true;
      document.querySelector("#capture-error").hidden = true;
      setup.hidden = false;
      capture.hidden = true;
      complete.hidden = true;
      faceDialog.showModal();
      setModalState();
    });
  });

  const closeFaceDialog = () => closeDialog(faceDialog);
  document.querySelector("#close-face-enrollment")?.addEventListener("click", closeFaceDialog);
  faceDialog?.addEventListener("click", (event) => {
    if (event.target !== faceDialog) return;
    if (!capture.hidden) document.querySelector("#cancel-enrollment")?.click();
    else closeFaceDialog();
  });
  faceDialog?.addEventListener("cancel", (event) => {
    event.preventDefault();
    if (!capture.hidden) document.querySelector("#cancel-enrollment")?.click();
    else closeFaceDialog();
  });
  faceDialog?.addEventListener("close", setModalState);
  document.addEventListener("face-enrollment:aborted", closeFaceDialog);
  document.addEventListener("face-enrollment:complete", async (event) => {
    capture.hidden = true;
    complete.hidden = false;
    const enrollmentId = event.detail?.enrollment?.id;
    try {
      const response = await fetch(`/api/v1/students/${encodeURIComponent(activeStudentId)}/face-enrollment`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({enrollment_id: enrollmentId}),
      });
      if (!response.ok) throw new Error("얼굴 등록 상태를 저장하지 못했습니다.");
      window.setTimeout(() => location.reload(), 1200);
    } catch (reason) {
      const error = document.querySelector("#setup-error");
      error.textContent = `${reason instanceof Error ? reason.message : "얼굴 벡터를 저장하지 못했습니다."} 닫은 후 얼굴 등록을 다시 진행해 주세요.`;
      error.hidden = false;
      complete.hidden = true;
      capture.hidden = true;
      setup.hidden = false;
    }
  });
})();
