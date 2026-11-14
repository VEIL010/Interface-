/* 시안 C의 예시 입력과 이전 검색 결과 안내. */
(function () {
  'use strict';

  var wrapper = document.getElementById('c-heart-form');
  if (!wrapper) return;

  wrapper.querySelector('[data-heart-example]').addEventListener('click', function () {
    wrapper.querySelectorAll('[data-example]').forEach(function (field) {
      field.value = field.getAttribute('data-example') || '';
      field.removeAttribute('aria-invalid');
    });
    wrapper.dispatchEvent(new Event('input', { bubbles: true }));
  });

  var results = document.getElementById('results');
  if (!results || !wrapper.matches('form')) return;

  function updateStaleNotice() {
    var snapshot = results.querySelector('[data-heart-query]');
    if (!snapshot) return;

    var current = new FormData(wrapper);
    var stale = current.get('top_k') !== snapshot.dataset.heartTopK ||
      JSON.parse(snapshot.dataset.heartQuery).some(function (field) {
        return current.get(field.key)?.trim() !== field.value;
      });
    var notice = snapshot.querySelector('[data-heart-stale]');
    var message = stale ? '이전 검색 결과입니다. 현재 입력값이나 결과 개수가 바뀌었어요. 지금 조건으로 보려면 다시 검색해 주세요.' : '';
    if (notice.textContent !== message) notice.textContent = message;
    results.querySelector('[data-heart-stale-label]').hidden = !stale;
  }

  wrapper.addEventListener('input', updateStaleNotice);
  wrapper.addEventListener('change', updateStaleNotice);
  results.addEventListener('bm:swapped', function (event) {
    if (event.target === results) updateStaleNotice();
  });
  updateStaleNotice();
})();
