/* 시안 C의 예시 입력 편의 기능. API/전처리 로직과 무관합니다. */
(function () {
  'use strict';
  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-heart-example]');
    if (!button) return;
    var wrapper = document.getElementById('c-heart-form');
    if (!wrapper) return;
    wrapper.querySelectorAll('[data-example]').forEach(function (field) {
      field.value = field.getAttribute('data-example') || '';
      field.removeAttribute('aria-invalid');
    });
    wrapper.dispatchEvent(new Event('input', { bubbles: true }));
  });
})();
