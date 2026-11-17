/* 13개 속성 실검색 화면(B · D)의 "지난 검색" 안내 — 의료진 화면 A · C와 같은 동작
 *
 *   <form data-heart-form …>                         질의 입력 폼
 *   #results 안의 [data-heart-query] [data-heart-top-k]  검색에 쓴 값 (결과 조각이 서버에서 그린다)
 *   #results 안의 [data-heart-stale]                    안내 문장을 넣을 자리 (비어 있으면 CSS 가 숨긴다)
 *
 * 입력이나 결과 개수가 검색에 쓴 값과 달라지면 안내를 채우고, 되돌리면 지운다.
 * 결과 영역의 [data-heart-query] 요소에는 data-stale 속성도 붙여 시안 CSS 가 흐리게 그릴 수 있게 한다.
 */
(function () {
  'use strict';

  var form = document.querySelector('form[data-heart-form]');
  var results = document.getElementById('results');
  if (!form || !results) return;

  // B·D 문구(합니다체)에 맞춘다. 버튼 이름이 '검색 실행' · '측정 실행'으로 달라 '다시 실행'이라고 쓴다
  var MESSAGE = '이전 검색 결과입니다. 입력값이나 결과 개수가 바뀌었습니다. 지금 조건으로 보려면 다시 실행해 주세요.';

  function update() {
    var snapshot = results.querySelector('[data-heart-query]');
    if (!snapshot) return;
    var current = new FormData(form);
    var fields;
    try { fields = JSON.parse(snapshot.getAttribute('data-heart-query')); } catch (error) { return; }
    var stale = String(current.get('top_k')) !== snapshot.getAttribute('data-heart-top-k') ||
      fields.some(function (field) {
        return String(current.get(field.key) || '').trim() !== field.value;
      });
    snapshot.toggleAttribute('data-stale', stale);
    Array.prototype.forEach.call(results.querySelectorAll('[data-heart-stale]'), function (node) {
      var text = stale ? MESSAGE : '';
      if (node.textContent !== text) node.textContent = text;
    });
  }

  form.addEventListener('input', update);
  form.addEventListener('change', update);
  results.addEventListener('bm:swapped', function (event) {
    // 상세 기록 조각의 교체도 이 영역을 지나가므로 검색 조각일 때만 다시 비교한다
    if (event.target === results) update();
  });
  update();
})();
