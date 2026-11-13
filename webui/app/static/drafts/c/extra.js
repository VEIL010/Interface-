/* 시안 C · 단계형 가이드 — 작은 보조 스크립트
 *
 * 요청과 화면 교체는 공용 bm.js 가 한다. 여기서는 그 앞뒤의 두 가지만 돕는다.
 *
 * 1. 검색이 끝나면 다음 단계(② 또는 입력 오류 안내)로 화면과 초점을 옮긴다.
 *    한 칼럼으로 길게 이어지는 화면이라, 버튼을 누른 뒤 결과가 화면 밖에 생기는 일을 막는다.
 * 2. 오류로 표시된 칸을 고치기 시작하면 그 칸의 오류 표시를 바로 지운다.
 *    (다시 검색하면 bm.js 가 서버 응답대로 다시 표시한다.)
 */
(function () {
  'use strict';

  var results = document.getElementById('results');
  if (!results) return;

  results.addEventListener('bm:swapped', function (event) {
    // 카드 안 상세 기록 교체도 여기까지 올라오므로 검색 결과 교체만 고른다
    if (event.target !== results) return;

    var next = results.querySelector('[role="alert"]') || results.querySelector('.step__title');
    if (!next) return;

    if (!next.hasAttribute('tabindex')) next.setAttribute('tabindex', '-1');
    next.focus({ preventScroll: true });

    var calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    next.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' });
  });

  document.addEventListener('input', function (event) {
    var field = event.target;
    if (field.getAttribute && field.getAttribute('aria-invalid') === 'true') {
      field.removeAttribute('aria-invalid');
    }
  });
})();
