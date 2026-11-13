/* Blind-Match UI 시안 공용 스크립트
 *
 * 서버(Jinja2)가 그린 HTML 조각을 받아 화면의 한 영역을 바꿔 끼운다.
 * 시안마다 JS를 따로 쓰지 않아도 되도록 data 속성만으로 동작한다.
 *
 *   <form data-bm-search action="/drafts/a/search" data-bm-target="#results"> … </form>
 *       제출하면 폼 값을 JSON으로 POST 하고, 응답 HTML로 #results 안을 교체한다.
 *
 *   <button type="button" data-bm-access="/drafts/a/records/P023/access" data-bm-target="#record-P023">
 *       누르면 상세 기록 조각을 받아 #record-P023 안을 교체한다.
 *
 *   <input type="radio" name="top_k" value="5" data-bm-autosubmit>
 *       값이 바뀌면 그 폼을 바로 제출한다 (선택 사항).
 *
 * 요청 중에는
 *   - 대상 영역에 aria-busy="true"
 *   - 누른 폼/버튼에 data-bm-busy 속성, 제출 버튼은 disabled
 *   - <html> 에 data-bm-busy 속성
 * 이 붙으므로 로딩 모양은 각 시안의 CSS에서 정한다.
 *
 * 검색 조각 안에 data-bm-invalid="age,max_hr" 를 가진 요소가 있으면
 * 그 이름의 입력에 aria-invalid="true" 를 붙이고, 없으면 지운다.
 *
 * 교체가 끝나면 대상 영역에서 bm:swapped 이벤트(detail: {ok, status})가 발생한다.
 * 주소에 ?delay=1500 을 붙이면 mock 서버가 그만큼 늦게 답해 로딩 상태를 볼 수 있다.
 */
(function () {
  'use strict';

  var NETWORK_ERROR =
    '<p class="bm-network-error" role="alert">서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.</p>';

  function mockDelay() {
    var value = new URLSearchParams(window.location.search).get('delay');
    return value ? Number(value) || 0 : 0;
  }

  function markInvalid(form, target) {
    var marker = target.querySelector('[data-bm-invalid]');
    var invalid = marker ? marker.getAttribute('data-bm-invalid').split(',') : [];
    Array.prototype.forEach.call(form.elements, function (element) {
      if (!element.name) return;
      if (invalid.indexOf(element.name) !== -1) element.setAttribute('aria-invalid', 'true');
      else element.removeAttribute('aria-invalid');
    });
  }

  async function swap(trigger, url, payload) {
    var selector = trigger.getAttribute('data-bm-target');
    var target = selector ? document.querySelector(selector) : null;
    if (!target || trigger.hasAttribute('data-bm-busy')) return;

    var buttons = trigger.matches('form')
      ? Array.prototype.slice.call(trigger.querySelectorAll('button[type="submit"]'))
      : [trigger];
    var delay = mockDelay();
    if (delay) payload.mock_delay_ms = delay;

    trigger.setAttribute('data-bm-busy', '');
    target.setAttribute('aria-busy', 'true');
    document.documentElement.setAttribute('data-bm-busy', '');
    buttons.forEach(function (button) { button.disabled = true; });

    var detail = { ok: false, status: 0 };
    try {
      var response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'bm' },
        body: JSON.stringify(payload),
      });
      target.innerHTML = await response.text();
      detail = { ok: response.ok, status: response.status };
    } catch (error) {
      target.innerHTML = NETWORK_ERROR;
    } finally {
      trigger.removeAttribute('data-bm-busy');
      target.removeAttribute('aria-busy');
      document.documentElement.removeAttribute('data-bm-busy');
      // 교체된 조각 안에 있던 버튼은 이미 사라졌으므로 남아 있는 것만 되살린다
      buttons.forEach(function (button) { if (button.isConnected) button.disabled = false; });
      if (trigger.matches('form')) markInvalid(trigger, target);
      target.dispatchEvent(new CustomEvent('bm:swapped', { bubbles: true, detail: detail }));
    }
  }

  document.addEventListener('change', function (event) {
    var control = event.target.closest('[data-bm-autosubmit]');
    if (!control || !control.form) return;
    if (control.form.requestSubmit) control.form.requestSubmit();
  });

  document.addEventListener('submit', function (event) {
    var form = event.target.closest('form[data-bm-search]');
    if (!form) return;
    event.preventDefault();
    var payload = {};
    new FormData(form).forEach(function (value, key) { payload[key] = value; });
    swap(form, form.getAttribute('action'), payload);
  });

  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-bm-access]');
    if (!button) return;
    event.preventDefault();
    swap(button, button.getAttribute('data-bm-access'), {});
  });
})();
