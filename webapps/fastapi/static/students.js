/**
 * 학생 관리 폼 제출 스크립트.
 * data-api-url, data-api-method, data-success-url 속성을 사용해 API를 호출한다.
 */
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('form[data-api-url]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      event.preventDefault();

      var errorEl = form.querySelector('[data-form-error]');
      if (errorEl) {
        errorEl.hidden = true;
        errorEl.textContent = '';
      }

      var confirmMsg = form.getAttribute('data-confirm');
      if (confirmMsg && !window.confirm(confirmMsg)) {
        return;
      }

      var url = form.getAttribute('data-api-url');
      var method = form.getAttribute('data-api-method') || 'POST';
      var successUrl = form.getAttribute('data-success-url');

      var formData = new FormData(form);
      var data = {};

      // 체크박스 처리
      var checkboxes = form.querySelectorAll('input[type="checkbox"]');
      checkboxes.forEach(function (cb) {
        data[cb.name] = cb.checked;
      });

      // 일반 필드 처리
      for (var pair of formData.entries()) {
        if (data[pair[0]] === undefined) {
          data[pair[0]] = pair[1];
        }
      }

      fetch(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
        .then(function (response) {
          if (!response.ok) {
            return response.json().then(function (err) {
              throw err;
            });
          }
          if (successUrl) {
            window.location.href = successUrl;
          }
        })
        .catch(function (err) {
          if (errorEl) {
            var message = '요청 처리 중 오류가 발생했습니다.';
            if (err && err.error && err.error.message) {
              message = err.error.message;
            }
            errorEl.textContent = message;
            errorEl.hidden = false;
          }
        });
    });
  });
});
