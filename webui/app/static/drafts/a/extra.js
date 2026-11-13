/* 시안 A 보조 스크립트 — 공용 bm.js 가 하지 않는 작은 마무리만 맡는다.
 *
 *  1) 검색이 입력 오류로 끝나면, 서버가 결과 조각에 그려 준 필드별 메시지를
 *     해당 입력란 아래 빈 자리로 옮겨 적고 첫 번째 오류 입력란으로 포커스를 옮긴다.
 *     (패널이 위아래로 쌓이는 좁은 화면에서는 결과 영역이 화면 밖에 있을 수 있다)
 *  2) 새 결과의 첫 행이 화면에 다 보이지 않으면 결과 영역의 맨 위로 스크롤한다.
 *  3) 방금 열린 상세 기록이 화면에 다 보이지 않으면 보이는 데까지만 스크롤한다.
 *  4) 결과 영역이 한 번이라도 교체되면 data-swapped 를 붙여, 새로 온 패널을 CSS 가 알아보게 한다.
 *
 * 요청·응답 처리와 HTML 생성은 하지 않는다. 글자는 모두 서버가 그린 것을 그대로 쓴다.
 */
(function () {
  'use strict';

  var inputs = document.querySelector('[data-heart-inputs], form[data-bm-search]');
  if (!inputs) return;

  var exampleFields = inputs.querySelectorAll('[data-example]');
  inputs.querySelector('[data-example-fill]').addEventListener('click', function () {
    exampleFields.forEach(function (field) { field.value = field.dataset.example; });
    inputs.dispatchEvent(new Event('input'));
  });

  var results = document.getElementById('results');
  if (!results || !inputs.matches('form')) return;
  var form = inputs;

  var status = document.getElementById('a-status');
  var stale = document.getElementById('a-stale');
  var searched;

  function syncResultStatus() {
    var sheet = results.querySelector('[data-query]');
    searched = sheet ? {
      inputs: JSON.parse(sheet.dataset.query),
      topK: sheet.dataset.topK
    } : null;
    status.textContent = sheet ? sheet.dataset.summary || '' : '';
    showStaleNotice();
  }

  function showStaleNotice() {
    var changed = searched && (
      searched.topK !== form.elements.top_k.value ||
      searched.inputs.some(function (input) {
        return form.elements[input.key].valueAsNumber !== input.value;
      })
    );
    var message = changed ? '이전 검색 결과입니다. 현재 입력값으로 보려면 다시 검색하세요.' : '';
    if (stale.textContent !== message) stale.textContent = message;
  }

  form.addEventListener('input', showStaleNotice);
  syncResultStatus();

  var reduceMotion = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;

  function scrollBehavior() {
    return reduceMotion && reduceMotion.matches ? 'auto' : 'smooth';
  }

  function fullyVisible(element) {
    var rect = element.getBoundingClientRect();
    return rect.top >= 0 && rect.bottom <= window.innerHeight;
  }

  function syncFieldErrors() {
    Array.prototype.forEach.call(form.querySelectorAll('[data-error-slot]'), function (slot) {
      var key = slot.getAttribute('data-error-slot');
      var source = results.querySelector('[data-error-for="' + key + '"]');
      slot.textContent = source ? source.textContent : '';
      slot.parentElement.hidden = !source;
    });
  }

  results.addEventListener('bm:swapped', function (event) {
    // 상세 기록 조각이 끼워진 경우 (이벤트가 결과 영역까지 올라온다)
    if (event.target !== results) {
      if (!fullyVisible(event.target)) {
        event.target.scrollIntoView({ block: 'nearest', behavior: scrollBehavior() });
      }
      return;
    }

    results.setAttribute('data-swapped', '');   // style.css: 새 패널의 테두리가 한 번 반짝인다
    syncFieldErrors();
    syncResultStatus();

    var firstInvalid = form.querySelector('input:not([type="hidden"])[aria-invalid="true"]');
    if (firstInvalid) {
      firstInvalid.focus();
      if (firstInvalid.select) firstInvalid.select();
      return;
    }

    // 첫 번째 결과 행(없으면 제목)이 화면에 다 보이지 않으면 결과 영역의 맨 위로 간다
    var sheet = results.firstElementChild;
    var anchor = results.querySelector('ol > li') || results.querySelector('h2');
    if (sheet && anchor && !fullyVisible(anchor)) {
      sheet.scrollIntoView({ block: 'start', behavior: scrollBehavior() });
    }
  });
})();
