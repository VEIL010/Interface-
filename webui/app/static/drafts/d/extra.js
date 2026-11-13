/* 시안 D · 벤치마크 콘솔 — 공용 bm.js 가 하지 않는 작은 보완만 한다.
 *
 *  1) 입력 오류로 측정이 실행되지 않으면 첫 번째 잘못된 입력으로 초점을 옮기고,
 *     그 입력을 오류 문장(#err-<name>)과 aria-describedby 로 잇는다.
 *     잘못된 입력이 없으면(501 등) 오류 안내 자체로 초점을 옮긴다.
 *  2) 상세 기록 조각이 들어오면 그 조각의 제목 영역으로 초점을 옮긴다.
 *
 * 요청·교체는 bm.js 가 전담한다. 여기서는 HTML 을 만들지 않는다.
 */
(function () {
  'use strict';

  function linkErrors(form) {
    Array.prototype.forEach.call(form.elements, function (element) {
      if (!element.name || !element.id) return;
      var base = element.getAttribute('data-d-describedby');
      if (base === null) {
        base = element.getAttribute('aria-describedby') || '';
        element.setAttribute('data-d-describedby', base);
      }
      var message = document.getElementById('err-' + element.name);
      var invalid = element.getAttribute('aria-invalid') === 'true';
      var ids = invalid && message ? (base + ' ' + message.id).trim() : base;
      if (ids) element.setAttribute('aria-describedby', ids);
      else element.removeAttribute('aria-describedby');
    });
  }

  document.addEventListener('bm:swapped', function (event) {
    var target = event.target;
    if (!target || !target.querySelector) return;

    if (target.id === 'results') {
      var form = document.querySelector('form[data-bm-search]');
      if (form) linkErrors(form);
      var alert = target.querySelector('[role="alert"]');
      if (!alert) return;
      var invalid = form ? form.querySelector('[aria-invalid="true"]') : null;
      (invalid || alert).focus();
      return;
    }

    var panel = target.querySelector('[data-d-focus]');
    if (panel) panel.focus();
  });
})();
