/* 시안 B · 암호 파이프라인 — 작은 보조 스크립트
 *
 * 검색 조각이 교체된 뒤, 서버가 그려 준 한 줄 요약(.lanes 의 data-b-summary)을
 * 미리 놓아 둔 role="status" 영역에 옮겨 스크린리더에 알린다.
 * 세 레인 전체를 aria-live 로 두면 교체될 때마다 화면 전체를 읽어 버리기 때문이다.
 * 요청·교체·화면 구성은 공용 bm.js 와 서버 템플릿이 맡는다. 여기서는 만들지 않는다.
 */
(function () {
  'use strict';

  var region = document.getElementById('results');
  var status = document.getElementById('b-status');
  if (!region || !status) return;

  region.addEventListener('bm:swapped', function (event) {
    // 상세 기록 조각의 교체도 이 영역을 지나가므로 검색 조각일 때만 처리한다
    if (event.target !== region) return;
    var lanes = region.querySelector('[data-b-summary]');
    // 오류일 때는 조각 안의 role="alert" 가 직접 알리므로 비워 둔다
    status.textContent = lanes ? lanes.getAttribute('data-b-summary') : '';
  });
})();
