"use strict";

/* Blind-Match 데이터셋 등록의 두 화면. 둘 다 의료진 검색 화면 시안 C(단계형 가이드)와 같은 틀이다.
 *
 *   등록본 검색  (/,          body[data-page="search"])   여러 번 쓰는 화면
 *     1 환자 정보 입력 → 2 검색 과정 → 3 유사 기록 → 4 검색 결과 확인
 *   데이터셋 등록 (/register, body[data-page="register"]) 데이터셋마다 한 번 쓰는 화면
 *     1 CSV 파일 → 2 열 연결 · 전체 행 검증 → 3 검증 결과 → 4 평문 등록 (첫 행으로 등록 확인까지)
 *
 * 아직 할 수 없는 단계는 회색 숫자 · 점선 레일의 "예고" 상태로 무엇이 나올지만 한 줄로 알린다
 * (시안 C의 step--upcoming). 요청이 끝나면 결과가 생긴 단계로 초점과 화면을 옮긴다 (시안 C의 extra.js).
 * 화면 글자는 모두 textContent 로 넣는다 (CSV 열 이름 · 데이터셋 이름이 그대로 들어오기 때문).
 */
(() => {
  const page = document.body.dataset.page === "register" ? "register" : "search";
  const $ = (id) => document.getElementById(id);
  const MISSING = "__MISSING__";
  // 정답 레이블 · 식별자로 보이는 열 이름. 기관 서비스(enrollment.py BLOCKED_COLUMNS)와 같은 목록이다.
  const BLOCKED_COLUMNS = new Set(["target", "num", "record_id", "patient_id", "id"]);
  const STALE_MESSAGE = "이전 검색 결과입니다. 현재 입력값이나 결과 개수가 바뀌었어요. 지금 조건으로 보려면 다시 검색해 주세요.";
  const state = {
    schema: null,
    active: null, connected: null, categories: null, queryReady: false,
    serviceDown: false,      // 기관 서비스(이 화면의 서버)에 닿지 못했다
    statusUnknown: false,    // 등록 직후 상태 확인이 실패해 검색 준비 여부를 모른다
    lastSeenId: null,        // 수신 서버에 연결된 상태에서 마지막으로 본 현재 등록본
    busy: false,
    // 등록 화면
    csv: "", inspect: null, preview: null, mappingDup: false, mappingBlocked: false, registeredId: null,
    // 검색 화면
    lastQuery: null,
  };

  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const fields = () => state.schema?.fields || [];
  const fieldOf = (key) => fields().find((field) => field.key === key);
  const calm = () => Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);

  async function api(path, body) {
    let response;
    try {
      response = await fetch(new URL(path.slice(1), new URL("./", location.href)), body === undefined ? { cache: "no-store" } : {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
    } catch {
      throw new Error("기관 서비스에 연결하지 못했습니다. Interface 서버가 실행 중인지 확인해 주세요.");
    }
    let data;
    try { data = await response.json(); } catch { throw new Error("서버 응답을 읽지 못했습니다. 연결을 확인해 주세요."); }
    if (!response.ok) {
      const error = new Error(data.error?.message || "요청을 처리하지 못했습니다. 입력값을 확인해 주세요.");
      error.issues = data.error?.issues;
      error.code = data.error?.code;     // 화면은 문구가 아니라 이 이름으로 상황을 알아본다
      error.status = response.status;
      throw error;
    }
    return data;
  }

  // ── 공통: 단계 · 초점 · 오류 ──────────────────────────────────────────────
  // active: 보통 · upcoming: 아직 할 수 없음(회색 숫자, 점선 레일) · alert: 숫자 옆에 빨간 상자만
  function stepMode(id, mode) {
    $(id).classList.toggle("step--upcoming", mode === "upcoming");
    $(id).classList.toggle("step--alert", mode === "alert");
  }
  // 결과가 생긴 자리로 초점과 화면을 옮긴다. 한 칼럼이 길어 결과가 화면 밖에 생기는 일을 막는다.
  function moveTo(node) {
    if (!node) return;
    node.focus({ preventScroll: true });
    node.scrollIntoView({ behavior: calm() ? "auto" : "smooth", block: "start" });
  }
  function announce(text) { if (text) $("live-status").textContent = text; }

  // 입력란 하나의 오류 표시: 테두리 · 색, 그리고 힌트 줄 끝의 사유 (style.css 의 aria-invalid 규칙).
  function setFieldError(input, flagId, message) {
    if (!input) return;
    if (message) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
    const flag = $(flagId);
    if (flag) flag.textContent = message ? ` · ${message}` : "";
  }
  // 오류 상자. link(issue) 가 { label, target } 을 주면 항목 이름이 그 입력란으로 가는 링크가 된다.
  // action 을 주면 상자 아래에 다른 화면으로 가는 링크를 단다.
  function fillAlert(id, title, error, link, action) {
    const box = $(id);
    box.replaceChildren(el("p", title, "alert__title"), el("p", error.message || "처리 중 오류가 발생했습니다.", "alert__lead"));
    const issues = Array.isArray(error.issues) ? error.issues.slice(0, 50) : [];
    if (issues.length) {
      const list = el("ul", undefined, "alert__list");
      list.setAttribute("role", "list");
      issues.forEach((issue) => {
        const to = link?.(issue);
        const where = to?.label
          ?? [issue.row ? `${issue.row}행` : "", fieldOf(issue.field)?.label ?? issue.field].filter(Boolean).join(" · ");
        const item = el("li", undefined, "alert__item");
        if (to?.target) {
          const anchor = el("a", where, "alert__field");
          anchor.href = `#${to.target.id}`;
          item.append(anchor);
        } else if (where) {
          item.append(el("span", where, "alert__field"));
        }
        item.append(el("span", issue.message, "alert__msg"));
        list.append(item);
      });
      box.append(list);
    }
    if (action) {
      const anchor = el("a", action.text, "link");
      anchor.href = action.href;
      const row = el("p", undefined, "alert__action");
      row.append(anchor);
      box.append(row);
    }
    box.hidden = false;
    return box;
  }
  function clearAlert(id) {
    $(id).hidden = true;
    $(id).replaceChildren();
  }

  // 처리 과정 패널의 한 줄: 큰 줄은 한 문장(say), 작은 줄은 상태 · 단계 이름 · 수치 (시안 C와 같은 짜임)
  function stageItem({ status, tag, say, label, detail }) {
    const done = status === "done";
    const item = el("li", undefined, `stage stage--${done ? "done" : "not_connected"}`);
    const nodeBox = el("span", undefined, "stage__node");
    const node = el("span", done ? "✓" : "○", `node node--${done ? "done" : "off"}`);
    node.setAttribute("aria-hidden", "true");
    nodeBox.append(node);
    const text = el("span", undefined, "tech__text");
    text.append(el("span", label, "tech__name"), detail ? ` · ${detail}` : "");
    const tech = el("p", undefined, "tech");
    tech.append(el("span", tag || (done ? "완료" : "미연결"), `tag tag--${done ? "done" : "not_connected"}`), text);
    const main = el("div", undefined, "stage__main");
    main.append(el("p", say || label, "stage__say"), tech);
    item.append(nodeBox, main);
    return item;
  }

  // 입력란 묶음 (기본 정보 · 혈압·혈액 검사 …). 의료진 화면과 같은 묶음을 쓴다.
  function groupSets(makeField) {
    return state.schema.groups.map((group) => {
      const set = el("fieldset", undefined, "group");
      set.append(el("legend", group.title, "group__title"));
      const grid = el("div", undefined, "group__fields");
      group.keys.map(fieldOf).filter(Boolean).forEach((field) => grid.append(makeField(field)));
      set.append(grid);
      return set;
    });
  }

  // ── 공통: 요청 중 ───────────────────────────────────────────────────────
  function syncButtons() {
    $("refresh-status").disabled = state.busy;
    if (page === "register") {
      const values = state.schema ? mappingValues() : {};
      const complete = Boolean(state.inspect) && fields().every((field) => values[field.key])
        && !state.mappingDup && !state.mappingBlocked;
      $("validate").disabled = state.busy || !complete;
      $("register").disabled = state.busy || !state.preview || !$("confirm-plaintext").checked;
      $("confirm-plaintext").disabled = state.busy || !state.preview;
      $("load-sample").disabled = state.busy || !state.schema;
      $("csv-file").disabled = state.busy || !state.schema;
      $("file-label").setAttribute("aria-disabled", String($("csv-file").disabled));
      $("dataset-name").disabled = state.busy;
      document.querySelectorAll("#mapping-grid select").forEach((node) => { node.disabled = state.busy || !state.inspect; });
    } else {
      $("search").disabled = state.busy || !state.queryReady;
      $("fill-example").disabled = state.busy || !state.queryReady;
      document.querySelectorAll("#query-fields input, #query-fields select, input[name='top_k']").forEach((node) => {
        node.disabled = state.busy || !state.queryReady;
      });
    }
  }
  // 버튼 글자가 "… 중"으로 바뀌고, 결과가 생길 단계(to)는 흐려지고, 누른 단계(from)의 레일이 흐른다.
  function setBusy(busy, { button, from, to = [], message } = {}) {
    state.busy = busy;
    document.documentElement.toggleAttribute("data-working", busy);
    document.querySelectorAll(".btn[data-busy]").forEach((node) => node.removeAttribute("data-busy"));
    document.querySelectorAll(".step[data-flowing]").forEach((node) => node.removeAttribute("data-flowing"));
    document.querySelectorAll(".step[aria-busy]").forEach((node) => node.removeAttribute("aria-busy"));
    if (busy) {
      if (button) $(button).setAttribute("data-busy", "");
      if (from) $(from).setAttribute("data-flowing", "");
      [].concat(to).forEach((id) => $(id).setAttribute("aria-busy", "true"));
    }
    announce(message);
    syncButtons();
  }

  // ── 공통: 머리의 현재 등록본 ─────────────────────────────────────────────
  function registeredAt(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString("ko-KR", { month: "long", day: "numeric", hour: "numeric", minute: "2-digit" });
  }
  function renderNow() {
    const { active, connected, serviceDown } = state;
    const bad = serviceDown || connected === false;
    const mode = $("mode");
    mode.textContent = serviceDown ? "기관 서비스 연결 안 됨 · 암호화 미연결"
      : connected === null ? "로컬 수신 서버 확인 중"
      : connected ? "로컬 수신 서버 연결 · 암호화 미연결" : "로컬 수신 서버 연결 안 됨 · 암호화 미연결";
    mode.classList.toggle("mode--bad", bad);

    let name;
    let meta = "";
    let id = "";
    if (serviceDown) {
      name = "기관 서비스에 연결하지 못했어요";
      meta = "Medical-Search-API의 실행과 연결 설정을 살펴본 뒤 ‘연결 다시 확인’을 눌러 주세요.";
    } else if (connected === null) {
      name = "확인 중…";
    } else if (active) {
      name = active.name;
      const when = registeredAt(active.created_at);
      meta = [`${active.records.toLocaleString()}건`, `${active.dimension}차원`, when && `${when} 등록`].filter(Boolean).join(" · ");
      id = `등록본 ID ${active.dataset_id}`;
    } else if (connected) {
      name = "아직 등록한 데이터셋이 없어요";
      meta = page === "search" ? "데이터셋 등록 화면에서 CSV를 한 번 등록하면 여기에 표시돼요."
        : "아래 단계를 따라 CSV를 등록하면 여기에 표시돼요.";
    } else {
      name = "로컬 수신 서버에 연결하지 못했어요";
      meta = "Medical-Search-API의 실행과 연결 설정을 살펴본 뒤 ‘연결 다시 확인’을 눌러 주세요.";
    }
    $("active-name").textContent = name;
    $("active-name").classList.toggle("is-bad", bad);
    $("active-meta").textContent = meta;
    $("active-id").textContent = id;
  }
  function updateActive(active, connected, categories, { unknown = false, serviceDown = false } = {}) {
    const changed = state.active?.dataset_id !== active?.dataset_id;
    state.active = active;
    state.connected = connected;
    state.serviceDown = serviceDown;
    state.statusUnknown = unknown;
    state.categories = active && categories ? categories : null;
    state.queryReady = Boolean(state.schema && active && connected && state.categories);
    if (connected) state.lastSeenId = active?.dataset_id ?? null;
    renderNow();
    if (page === "search") {
      renderQueryStep();
      applyCategories();
      if (changed || !state.queryReady) {
        clearFieldErrors();
        resetResults();
      }
    } else if (changed && connected && active?.dataset_id !== state.registeredId && !$("register-done").hidden) {
      // 이 화면에서 등록한 등록본이 다른 화면의 등록으로 바뀌었다. 이미 등록에 쓴 검증 결과는
      // 다시 쓸 수 없으니 3·4단계를 처음으로 돌린다 (연결이 잠깐 끊긴 것은 바뀐 것이 아니다).
      invalidatePreview();
    }
    syncButtons();
  }
  // quiet: 부른 쪽이 따로 알린다 (검색 중 등록본이 바뀐 경우).
  async function refreshStatus({ quiet = false } = {}) {
    if (state.busy) return;
    const before = state.lastSeenId;
    const hadResults = page === "search" && !$("results-done").hidden;
    const hadRegister = page === "register" && !$("register-done").hidden;
    setBusy(true, { message: quiet ? "" : "로컬 수신 서버 연결을 확인하고 있어요." });
    let message;
    try {
      const status = await api("/api/status");
      updateActive(status.active, status.server_connected, status.categories);
      const now = status.active?.dataset_id ?? null;
      if (!status.server_connected) {
        message = "로컬 수신 서버에 연결하지 못했어요.";
      } else if (before && now !== before) {
        const lost = [hadRegister && $("register-done").hidden && "검증·등록 결과", hadResults && "검색 결과"].filter(Boolean);
        message = `현재 등록본이 바뀌었어요: ‘${status.active?.name ?? "없음"}’.`
          + (lost.length ? ` 이 화면의 ${lost.join("와 ")}를 지웠어요.` : "");
      } else if (status.active && !status.query_ready) {
        message = "이 등록본의 기관 전처리기가 없어, CSV를 다시 등록해야 검색할 수 있어요.";
      } else {
        message = "로컬 수신 서버 연결을 확인했어요.";
      }
    } catch {
      updateActive(null, null, null, { serviceDown: true });
      message = "기관 서비스에 연결하지 못했어요.";
    } finally { setBusy(false); }
    if (!quiet) announce(message);
  }

  // ════════════════════════════════════════════════════════════════════════
  // 등록본 검색 화면
  // ════════════════════════════════════════════════════════════════════════

  // 1단계: 검색할 수 있으면 입력 폼, 등록본이 없으면 등록 화면으로 안내, 쓸 수 없는 등록본이면 빨간 상자
  function renderQueryStep() {
    const { active, connected, queryReady, serviceDown } = state;
    const empty = !serviceDown && connected === true && !active;
    const blocked = !serviceDown && connected === true && Boolean(active) && !queryReady;
    let heading = "환자 정보를 입력해 주세요";
    let lead = "";
    if (serviceDown) lead = "기관 서비스에 연결되면 검색할 수 있어요.";
    else if (connected === null) lead = "로컬 수신 서버 연결을 확인하고 있어요.";
    else if (!connected) lead = "로컬 수신 서버에 연결되면 현재 등록본에서 검색할 수 있어요.";
    else if (empty) {
      heading = "먼저 데이터셋을 등록해 주세요";
      lead = "검색할 등록본이 아직 없어요. 기관 CSV를 한 번 등록하면 이 화면에서 계속 검색할 수 있어요.";
    } else if (queryReady) {
      lead = `현재 등록본 ‘${active.name}’에서 찾아요. 등록할 때 학습한 전처리기를 그대로 써서 입력을 벡터로 바꿔요.`;
    }
    stepMode("step-query", queryReady || empty ? "active" : blocked ? "alert" : "upcoming");
    $("query-heading").textContent = heading;
    $("query-lead").textContent = lead;
    $("search-form").hidden = !queryReady;
    $("query-empty").hidden = !empty;
    if (blocked) {
      fillAlert("query-unavailable", "이 등록본은 여기서 검색할 수 없어요",
        { message: "이 등록본의 기관 전처리기가 이 컴퓨터에 없어요. CSV를 다시 등록해 주세요." },
        null, { href: "register", text: "데이터셋 등록 화면으로" });
    } else {
      clearAlert("query-unavailable");
    }
  }

  function renderQueryForm() {
    $("query-fields").replaceChildren(...groupSets((field) => {
      const wrap = el("div", undefined, "field");
      const label = el("label", field.label, "field__label");
      label.htmlFor = `query-${field.key}`;
      const choice = field.options.length > 0;
      let input;
      if (choice) {
        input = el("select", undefined, "field__input field__input--select");
      } else {
        input = el("input", undefined, field.unit ? "field__input" : "field__input field__input--bare");
        input.type = "number";
        input.inputMode = field.step === "1" ? "numeric" : "decimal";
        input.step = field.step || "any";
        input.autocomplete = "off";
        if (field.min !== null && field.min !== undefined) input.min = field.min;
        if (field.max !== null && field.max !== undefined) input.max = field.max;
      }
      input.id = `query-${field.key}`;
      input.name = field.key;
      const hint = el("p", [field.en, !choice && field.unit].filter(Boolean).join(" · "), "field__hint");
      hint.id = `query-hint-${field.key}`;
      const flag = el("span", "", "field__flag");
      flag.id = `query-flag-${field.key}`;
      hint.append(flag);
      input.setAttribute("aria-describedby", hint.id);
      input.addEventListener("input", () => queryEdited(field.key));
      input.addEventListener("change", () => queryEdited(field.key));
      wrap.append(label, input);
      if (field.unit && !choice) {
        const unit = el("span", field.unit, "field__unit");
        unit.setAttribute("aria-hidden", "true");
        wrap.append(unit);
      }
      wrap.append(hint);
      return wrap;
    }));
    applyCategories();
    fillQuery(examples());
  }
  const examples = () => Object.fromEntries(fields().map((field) => [field.key, field.example ?? ""]));

  // 범주 선택지는 현재 등록본이 학습한 값만 고를 수 있게 한다 (모르는 범주는 서버가 거절한다)
  function applyCategories() {
    if (page !== "search" || !state.schema) return;
    fields().filter((field) => field.options.length).forEach((field) => {
      const select = $(`query-${field.key}`);
      if (!select) return;
      const known = state.categories?.[field.key];
      const current = select.value;
      const blankAllowed = !known || known.includes(MISSING);
      const blank = new Option(blankAllowed ? "미입력" : "미입력 (이 등록본에서는 불가)", "");
      blank.disabled = !blankAllowed;
      select.replaceChildren(blank);
      field.options.forEach(([value, text]) => {
        const allowed = !known || known.includes(String(value));
        const option = new Option(allowed ? text : `${text} (등록본에 없음)`, value);
        option.disabled = !allowed;
        select.append(option);
      });
      const usable = [...select.options].filter((option) => !option.disabled);
      select.value = usable.some((option) => option.value === current) ? current : (usable[0]?.value ?? "");
    });
  }
  function fillQuery(values) {
    fields().forEach((field) => {
      const input = $(`query-${field.key}`);
      if (!input) return;
      const value = values[field.key];
      let text = value === null || value === undefined ? "" : String(value);
      if (field.options.length) {
        const match = text && field.options.find(([option]) => Number(option) === Number(text));
        if (match) text = String(match[0]);
        const option = [...input.options].find((node) => node.value === text);
        if (!option || option.disabled) return;   // 등록본에 없는 값은 넣지 않는다
      }
      input.value = text;
    });
    clearFieldErrors();
    updateStale();
  }
  function clearFieldErrors() {
    if (page !== "search") return;
    fields().forEach((field) => setFieldError($(`query-${field.key}`), `query-flag-${field.key}`, ""));
  }
  function showFieldErrors(issues) {
    (issues || []).forEach((issue) => {
      if (fieldOf(issue.field)) setFieldError($(`query-${issue.field}`), `query-flag-${issue.field}`, issue.message);
    });
  }
  function searchLink(issue) {
    const field = fieldOf(issue.field);
    const target = field && $(`query-${field.key}`);
    return target ? { label: field.label, target } : null;
  }
  function queryEdited(key) {
    setFieldError($(`query-${key}`), `query-flag-${key}`, "");
    updateStale();
  }
  function currentQuery() {
    return {
      values: Object.fromEntries(fields().map((field) => [field.key, ($(`query-${field.key}`)?.value || "").trim()])),
      topK: Number(document.querySelector("input[name='top_k']:checked")?.value || 5),
    };
  }
  // 입력 요약에 쓰는 글자: 범주는 이름, 수치는 단위와 함께, 빈 값은 어떻게 처리했는지
  function shownValue(field, value) {
    if (field.options.length) {
      return field.options.find(([code]) => code === value)?.[1] ?? "미입력";
    }
    return value ? [value, field.unit].filter(Boolean).join(" ") : "미입력 (중앙값 사용)";
  }

  // 2~4단계: 검색 전에는 예고, 검색하면 과정 · 기록 · 확인
  function resetResults() {
    if (page !== "search") return;
    state.lastQuery = null;
    setStale(false);
    ["step-process", "step-results", "step-confirm"].forEach((id) => stepMode(id, "upcoming"));
    clearAlert("search-error");
    $("process-done").hidden = true;
    $("results-done").hidden = true;
    $("confirm-points").hidden = true;
    $("process-heading").textContent = "검색 과정";
    $("process-lead").hidden = false;
    $("process-lead").textContent = "검색하면 입력을 벡터로 바꿔 등록 기록과 비교하는 과정을 보여 드려요.";
    $("results-heading").textContent = "유사 기록";
    $("results-lead").textContent = "검색하면 가장 비슷한 기록을 순위대로 보여 드려요.";
    $("confirm-heading").textContent = "검색 결과 확인";
    $("confirm-lead").hidden = false;
  }
  function showSearchError(title, error, link) {
    resetResults();
    stepMode("step-process", "alert");
    return fillAlert("search-error", title, error, link);
  }
  // 입력을 바꾸면 결과는 그대로 두고 "이전 검색"으로 표시한다. 되돌리면 표시도 사라진다 (시안 C와 같다).
  function setStale(stale) {
    const text = stale ? STALE_MESSAGE : "";
    if ($("search-stale").textContent !== text) $("search-stale").textContent = text;
    $("results-stale-label").hidden = !stale;
  }
  function updateStale() {
    if (page !== "search") return;
    if (!state.lastQuery) {
      setStale(false);
      return;
    }
    const now = currentQuery();
    const last = state.lastQuery;
    setStale(now.topK !== last.topK || fields().some((field) => now.values[field.key] !== last.values[field.key]));
  }
  function renderResults(result, query) {
    const hits = result.results || [];
    resetResults();
    state.lastQuery = query;

    stepMode("step-process", "active");
    $("process-heading").textContent = "검색을 마쳤어요";
    $("process-lead").textContent = "입력값을 등록본의 전처리기로 벡터로 바꾸고, 로컬 수신 서버에서 평문 코사인 유사도를 계산했어요.";
    $("process-stages").replaceChildren(...[
      { status: "done", say: `${fields().length}개 의료 속성을 ${state.active.dimension}차원 벡터로 바꿨어요`,
        label: "질의 인코딩", detail: "등록본 전처리기 · L2 정규화" },
      { status: "done", say: `등록 기록 ${result.searched.toLocaleString()}건과 평문으로 비교했어요`,
        label: "평문 검색", detail: `로컬 수신 서버 · 코사인 유사도 · ${Number(result.elapsed_ms).toFixed(2)} ms` },
      { status: "not_connected", say: "CKKS 암호화 검색은 아직 연결 전이에요",
        label: "CKKS 암호화", detail: "향후 BlindMatchCore 연동 예정" },
    ].map(stageItem));
    $("query-summary").replaceChildren(...fields().map((field) => {
      const row = el("div");
      row.append(el("dt", field.label), el("dd", shownValue(field, query.values[field.key])));
      return row;
    }));
    $("process-done").hidden = false;

    stepMode("step-results", "active");
    $("results-heading").textContent = `가장 비슷한 기록 ${hits.length}건`;
    $("results-lead").textContent = `등록 기록 ${result.searched.toLocaleString()}건 중 Top-${query.topK} 결과예요.`;
    $("search-result-rows").replaceChildren(...hits.map((hit) => {
      const score = Number(hit.score);
      const item = el("li", undefined, "card");
      const head = el("div", undefined, "card__head");
      const rank = el("p", undefined, "rank");
      rank.append(el("span", "순위 ", "vh"), el("span", String(hit.rank), "rank__n"), el("span", "위", "rank__u"));
      const id = el("div", undefined, "stat");
      id.append(el("p", "기록 ID", "stat__label"), el("h3", hit.record_id, "stat__value"));
      const similarity = el("div", undefined, "stat stat--score");
      similarity.append(el("p", "코사인 유사도", "stat__label"), el("p", score.toFixed(3), "stat__value"));
      head.append(rank, id, similarity);
      const bar = el("div", undefined, "bar");
      bar.setAttribute("aria-hidden", "true");
      const fill = el("span", undefined, "bar__fill");
      fill.style.setProperty("--pct", String(Math.max(0, Math.min(1, score)) * 100));
      bar.append(fill);
      item.append(head, bar);
      return item;
    }));
    $("results-done").hidden = false;

    stepMode("step-confirm", "active");
    $("confirm-heading").textContent = "검색 결과를 확인해 주세요";
    $("confirm-lead").hidden = true;
    $("confirm-points").hidden = false;
    updateStale();
  }

  function bindSearchPage() {
    document.querySelectorAll("input[name='top_k']").forEach((node) => node.addEventListener("change", updateStale));
    $("fill-example").addEventListener("click", () => {
      fillQuery(examples());
      // 고친 입력을 가리키던 오류 상자는 더 맞지 않는다. 결과가 보이는 중이면 "이전 검색" 표시가 대신 말한다.
      if ($("step-process").classList.contains("step--alert")) resetResults();
    });
    $("search-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (state.busy || !state.active || !state.queryReady) return;
      clearFieldErrors();
      const query = currentQuery();
      setBusy(true, { button: "search", from: "step-query", to: ["step-process", "step-results", "step-confirm"],
        message: "등록본에서 유사 기록을 찾고 있어요." });
      let next = null;
      let datasetChanged = false;
      try {
        const result = await api("/api/search", { dataset_id: state.active.dataset_id, values: query.values, top_k: query.topK });
        renderResults(result, query);
        announce(`검색을 마쳤어요. 유사 기록 ${result.results.length}건, 1위 ${result.results[0]?.record_id ?? "없음"}.`);
        next = $("s2-title");
      } catch (error) {
        datasetChanged = error.code === "dataset_changed";
        if (!datasetChanged) {
          next = showSearchError("검색하지 못했어요", error, searchLink);
          showFieldErrors(error.issues);
        }
      } finally { setBusy(false); }
      if (datasetChanged) {
        // 다른 화면이 새 등록본을 활성화했다. 화면을 새 등록본에 맞춘 뒤 그 사실을 같은 자리에 알린다.
        await refreshStatus({ quiet: true });
        if (state.queryReady) {
          next = showSearchError("등록본이 바뀌었어요", {
            message: "다른 화면에서 새 등록본이 활성화됐어요. 이 화면도 그 등록본에 맞췄으니 다시 검색해 주세요.",
          });
        } else {
          // 맞춘 뒤에도 검색할 수 없으면 1단계가 그 이유를 말한다
          announce("현재 등록본이 바뀌었어요. 1단계에서 이유를 확인해 주세요.");
          next = $("query-unavailable").hidden ? $("s1-title") : $("query-unavailable");
        }
      }
      moveTo(next);
    });
  }

  // ════════════════════════════════════════════════════════════════════════
  // 데이터셋 등록 화면
  // ════════════════════════════════════════════════════════════════════════

  // 2단계: 열 연결
  function mappingValues() {
    return Object.fromEntries(fields().map((field) => [field.key, $(`map-${field.key}`)?.value || ""]));
  }
  // 같은 열 두 번 · 정답/식별자 열은 서버에 묻기 전에 그 칸에 바로 표시하고 검증 버튼을 끈다.
  function updateMappingCount() {
    const values = mappingValues();
    const counts = {};
    Object.values(values).filter(Boolean).forEach((column) => { counts[column] = (counts[column] || 0) + 1; });
    let dup = false;
    let blocked = false;
    fields().forEach((field) => {
      const column = values[field.key];
      const isDup = Boolean(column) && counts[column] > 1;
      const isBlocked = Boolean(column) && BLOCKED_COLUMNS.has(column.toLowerCase());
      dup = dup || isDup;
      blocked = blocked || isBlocked;
      setFieldError($(`map-${field.key}`), `map-flag-${field.key}`,
        isDup ? "다른 속성과 같은 열이에요" : isBlocked ? "정답·식별자 열은 연결할 수 없어요" : "");
    });
    state.mappingDup = dup;
    state.mappingBlocked = blocked;
    const chosen = Object.values(values).filter(Boolean).length;
    const text = !state.inspect ? "파일을 먼저 불러오세요"
      : dup ? "같은 열을 두 속성에 연결했어요"
      : blocked ? "정답·식별자 열을 연결했어요"
      : `${chosen} / ${fields().length} 연결`;
    const label = $("mapping-count");
    if (label.textContent !== text) label.textContent = text;   // 같은 말을 다시 읽지 않게 바뀔 때만 쓴다
    label.classList.toggle("is-bad", dup || blocked);
    syncButtons();
  }
  function renderMapping() {
    const ready = Boolean(state.inspect);
    stepMode("step-map", ready ? "active" : "upcoming");
    $("map-heading").textContent = ready ? "열을 의료 속성에 연결해 주세요" : "열 연결";
    $("map-lead").textContent = ready
      ? "열 이름이 속성 키와 같으면 자동으로 연결해요. 정답 열(target, num)과 환자 식별자 열은 연결하지 마세요."
      : `CSV를 불러오면 나이·성별 같은 의료 속성 ${fields().length || 13}개에 파일의 열을 연결해요.`;
    $("map-form").hidden = !ready;
    if (!state.schema) return;
    const columns = state.inspect?.columns || [];
    $("mapping-grid").replaceChildren(...groupSets((field) => {
      const wrap = el("div", undefined, "field");
      const label = el("label", field.label, "field__label");
      label.htmlFor = `map-${field.key}`;
      const select = el("select", undefined, "field__input field__input--select");
      select.id = `map-${field.key}`;
      select.append(new Option(columns.length ? "열 선택" : "파일 없음", ""));
      columns.forEach((column) => select.append(new Option(column, column)));
      select.value = state.inspect?.mapping[field.key] || "";
      const hint = el("p", `${field.en} · ${field.key}`, "field__hint");
      hint.id = `map-hint-${field.key}`;
      const flag = el("span", "", "field__flag");
      flag.id = `map-flag-${field.key}`;
      hint.append(flag);
      select.setAttribute("aria-describedby", hint.id);
      select.addEventListener("change", () => { invalidatePreview(); updateMappingCount(); });
      wrap.append(label, select, hint);
      return wrap;
    }));
    updateMappingCount();
  }
  // 검증 오류 중 행이 없는 것은 화면의 입력(이름 · 열 연결) 문제다. 행이 있는 것은 파일 내용 문제라 링크하지 않는다.
  function validationLink(issue) {
    if (issue.row) return null;
    if (issue.field === "name") return { label: "데이터셋 이름", target: $("dataset-name") };
    const field = fieldOf(issue.field);
    const target = field && $(`map-${field.key}`);
    return target ? { label: field.label, target } : null;
  }
  function markValidationIssues(issues) {
    (issues || []).filter((issue) => !issue.row).forEach((issue) => {
      if (issue.field === "name") setFieldError($("dataset-name"), "dataset-name-flag", issue.message);
      else if (fieldOf(issue.field)) setFieldError($(`map-${issue.field}`), `map-flag-${issue.field}`, issue.message);
    });
  }

  // 1단계: 파일. 파일 · 열 연결 · 이름이 바뀌면 검증 결과와 등록 단계는 다시 "예고"로 돌아간다.
  function invalidatePreview() {
    state.preview = null;
    $("confirm-plaintext").checked = false;
    clearAlert("register-error");
    renderCheck("idle");
    renderRegister("upcoming");
    syncButtons();
  }
  function resetFile() {
    invalidatePreview();
    clearAlert("file-error");
    state.inspect = null;
    state.csv = "";
    $("dropzone").classList.remove("is-loaded");
    $("file-name").textContent = "CSV 파일을 여기에 끌어 놓으세요";
    $("file-description").textContent = "또는 아래 버튼으로 직접 고를 수 있어요";
    $("file-label").textContent = "파일 선택";
    renderMapping();
  }
  async function inspectCsv(text, filename) {
    if (new TextEncoder().encode(text).length > state.schema.max_bytes) {
      throw new Error("파일이 너무 큽니다. 1 MB 이하의 CSV를 선택해 주세요.");
    }
    const inspected = await api("/api/inspect", { csv_text: text });
    state.csv = text;
    state.inspect = inspected;
    $("dropzone").classList.add("is-loaded");
    $("file-name").textContent = filename;
    $("file-description").textContent = `${inspected.row_count.toLocaleString()}행 · 열 ${inspected.columns.length}개`;
    $("file-label").textContent = "다른 파일 선택";
    renderMapping();
    announce("CSV를 읽었어요. 2단계에서 열 연결을 확인하고 전체 행을 검증해 주세요.");
  }
  // 파일을 읽으면 이제 할 수 있게 된 2단계로, 못 읽으면 1단계의 오류 상자로 옮긴다.
  async function loadFile(file) {
    if (!file || state.busy || !state.schema) return;
    setBusy(true, { message: "선택한 CSV 파일을 읽고 있어요." });
    resetFile();
    let next = null;
    try {
      if (!file.name.toLowerCase().endsWith(".csv")) {
        throw new Error("CSV 파일을 선택해 주세요. DB 데이터는 CSV로 내보낸 뒤 등록할 수 있습니다.");
      }
      if (file.size > state.schema.max_bytes) throw new Error("파일이 너무 큽니다. 1 MB 이하의 CSV를 선택해 주세요.");
      let text;
      try { text = await file.text(); } catch { throw new Error("브라우저가 이 파일을 열지 못했습니다. 파일을 다시 선택해 주세요."); }
      await inspectCsv(text, file.name);
      next = $("s2-title");
    } catch (error) {
      next = fillAlert("file-error", "파일을 불러오지 못했어요", error);
    } finally { setBusy(false); }
    moveTo(next);
  }
  async function loadSample() {
    if (state.busy || !state.schema) return;
    setBusy(true, { message: "합성 예시 CSV를 불러오고 있어요." });
    resetFile();
    let next = null;
    try {
      let response = null;
      try { response = await fetch(new URL("api/sample", new URL("./", location.href)), { cache: "no-store" }); } catch { /* 아래에서 알린다 */ }
      if (!response?.ok) throw new Error("기관 서비스에서 합성 예시를 받지 못했습니다. 실행 중인지 확인해 주세요.");
      await inspectCsv(await response.text(), "veil-synthetic-sample.csv");
      next = $("s2-title");
    } catch (error) {
      next = fillAlert("file-error", "파일을 불러오지 못했어요", error);
    } finally { setBusy(false); }
    moveTo(next);
  }

  // 3단계: 검증 결과
  function renderCheck(mode) {
    stepMode("step-check", mode === "idle" ? "upcoming" : mode === "error" ? "alert" : "active");
    if (mode !== "error") clearAlert("check-error");
    $("check-done").hidden = mode !== "done";
    if (mode === "idle") {
      $("check-heading").textContent = "검증 결과";
      $("check-lead").textContent = "전체 행을 검증하면 등록할 데이터의 처음 5행과 결측 수를 보여 드려요.";
    }
  }
  function renderPreview() {
    const preview = state.preview;
    $("check-heading").textContent = "검증을 통과했어요";
    // 앞 문장은 한 문장으로, 수치는 그 아래 작은 줄로 (시안 C의 step__tech)
    const facts = [`결측 ${preview.missing_cells.toLocaleString()}개`];
    if (preview.ignored_columns.length) facts.push(`등록에서 빼는 열 ${preview.ignored_columns.join(", ")}`);
    $("check-lead").replaceChildren(`${preview.records.toLocaleString()}행을 모두 확인했어요.`, el("span", facts.join(" · "), "step__tech"));

    const head = el("tr");
    const corner = el("th", "행");
    corner.scope = "col";
    head.append(corner);
    fields().forEach((field) => {
      const th = el("th", field.label);
      th.scope = "col";
      th.append(el("small", field.key));
      head.append(th);
    });
    $("preview-table").tHead.replaceChildren(head);
    // 행 번호는 오류 목록과 같은 스프레드시트 행 번호다 (머리글이 1행).
    const lines = Array.isArray(preview.sample_lines) ? preview.sample_lines : [];
    $("preview-table").tBodies[0].replaceChildren(...preview.sample.slice(0, 5).map((row, index) => {
      const tr = el("tr");
      const number = el("th", String(lines[index] ?? index + 2));
      number.scope = "row";
      tr.append(number);
      fields().forEach((field) => {
        const value = row[field.key];
        const missing = value === null || value === undefined || value === "";
        tr.append(el("td", missing ? "결측" : String(value), missing ? "is-missing" : undefined));
      });
      return tr;
    }));
    $("preview-notes").replaceChildren(...(preview.notes || []).map((note) => el("li", note)));
    renderCheck("done");
    renderRegister("ready");
  }

  // 4단계: 평문 등록
  function renderRegister(mode) {
    stepMode("step-register", mode === "upcoming" ? "upcoming" : "active");
    $("register-ready").hidden = mode !== "ready";
    $("register-done").hidden = mode !== "done";
    if (mode === "upcoming") {
      $("register-heading").textContent = "평문 등록";
      $("register-lead").textContent = "검증을 통과하면 평문 테스트로 등록할 수 있어요.";
    } else if (mode === "ready") {
      $("register-heading").textContent = "평문으로 등록할까요?";
      $("register-lead").textContent = "아래 내용을 확인하고 등록해 주세요.";
    } else {
      $("register-heading").textContent = "등록했어요";
    }
  }
  // 등록 확인: 첫 등록 행으로 검색해 그 행(R000001)이 유사도 1.000으로 나오는지 본다.
  // 검색 화면에 의료 데이터 행을 넘기지 않고, 등록한 그 자리에서 끝까지 확인한다.
  async function verifyRegistration(firstRow, active) {
    const label = "등록 확인";
    if (!firstRow) return { status: "not_connected", tag: "확인 못 함", label, say: "등록 확인을 하지 못했어요", detail: "첫 행 정보가 없어요" };
    if (!state.queryReady) {
      return { status: "not_connected", tag: "확인 못 함", label, say: "등록 확인을 하지 못했어요",
        detail: "검색 준비 상태를 모르니 ‘연결 다시 확인’ 뒤 검색 화면에서 확인해 주세요" };
    }
    const values = Object.fromEntries(fields().map((field) => {
      const value = firstRow[field.key];
      return [field.key, value === null || value === undefined ? "" : String(value)];
    }));
    try {
      const result = await api("/api/search", { dataset_id: active.dataset_id, values, top_k: 3 });
      const hit = (result.results || []).find((item) => item.record_id === "R000001");
      if (hit && Number(hit.score) > 0.999) {
        return { status: "done", label, say: "첫 행으로 검색해 그대로 찾는 것을 확인했어요",
          detail: `R000001 · ${hit.rank}위 · 유사도 ${Number(hit.score).toFixed(3)}` };
      }
      return { status: "not_connected", tag: "확인 필요", label, say: "첫 행이 예상대로 검색되지 않았어요",
        detail: "검색 화면에서 첫 행 값으로 다시 확인해 주세요" };
    } catch (error) {
      return { status: "not_connected", tag: "확인 못 함", label, say: "등록 확인을 하지 못했어요", detail: error.message };
    }
  }
  function renderStages(result, check) {
    const active = result.active;
    const checked = check.status === "done" ? " 첫 행으로 검색해 확인까지 마쳤어요." : "";
    $("register-lead").textContent =
      `‘${active.name}’ ${active.records.toLocaleString()}건을 ${active.dimension}차원 벡터로 등록했어요.${checked}`;
    $("stage-list").replaceChildren(...result.stages.map(stageItem), stageItem(check));
    renderRegister("done");
  }

  function bindRegisterPage() {
    $("csv-file").addEventListener("change", async (event) => {
      await loadFile(event.target.files[0]);
      event.target.value = "";
    });
    $("load-sample").addEventListener("click", loadSample);

    const drop = $("dropzone");
    ["dragenter", "dragover"].forEach((name) => drop.addEventListener(name, (event) => {
      event.preventDefault();
      if (!state.busy) drop.classList.add("is-over");
    }));
    drop.addEventListener("dragleave", (event) => {
      if (!drop.contains(event.relatedTarget)) drop.classList.remove("is-over");
    });
    drop.addEventListener("drop", (event) => {
      event.preventDefault();
      drop.classList.remove("is-over");
      if (!state.busy) loadFile(event.dataTransfer.files[0]);
    });

    $("dataset-name").addEventListener("input", () => {
      setFieldError($("dataset-name"), "dataset-name-flag", "");
      invalidatePreview();
    });
    $("confirm-plaintext").addEventListener("change", syncButtons);

    $("validate").addEventListener("click", async () => {
      if (state.busy || !state.inspect) return;
      invalidatePreview();
      setFieldError($("dataset-name"), "dataset-name-flag", "");
      updateMappingCount();   // 지난 검증이 표시한 열 연결 오류를 지금 값 기준으로 다시 맞춘다
      setBusy(true, { button: "validate", from: "step-map", to: "step-check", message: "모든 행의 의료 속성을 검증하고 있어요." });
      let next = null;
      try {
        state.preview = await api("/api/preview", { csv_text: state.csv, mapping: mappingValues(), name: $("dataset-name").value.trim() });
        renderPreview();
        announce("전체 행 검증을 마쳤어요. 4단계에서 평문 테스트 등록 여부를 확인해 주세요.");
        next = $("s3-title");
      } catch (error) {
        renderCheck("error");
        next = fillAlert("check-error", "검증하지 못했어요", error, validationLink);
        markValidationIssues(error.issues);
      } finally { setBusy(false); }
      moveTo(next);
    });

    $("register").addEventListener("click", async () => {
      if (state.busy || !state.preview || !$("confirm-plaintext").checked) return;
      clearAlert("register-error");
      const firstRow = state.preview.sample[0] ?? null;
      setBusy(true, { button: "register", from: "step-register", message: "일괄 인코딩한 뒤 로컬 수신 서버에 등록하고 있어요." });
      let next = null;
      try {
        const result = await api("/api/register", { preview_id: state.preview.preview_id, scheme: "plaintext" });
        let status = null;
        try { status = await api("/api/status"); } catch { /* 등록은 끝났다. 검색 준비 상태만 모른다 */ }
        state.preview = null;
        state.registeredId = result.active.dataset_id;
        $("confirm-plaintext").checked = false;
        updateActive(status?.active || result.active, status ? status.server_connected : true, status?.categories ?? null,
          { unknown: !status });
        const check = await verifyRegistration(firstRow, result.active);
        renderStages(result, check);
        announce(check.status === "done"
          ? "등록하고 첫 행으로 확인까지 마쳤어요. 검색 화면에서 이 등록본을 계속 검색할 수 있어요."
          : "등록을 마쳤어요. 등록 확인은 하지 못했어요.");
        next = $("s4-title");
      } catch (error) {
        // 검증 결과가 만료됐으면 그 결과로는 다시 등록할 수 없다. 버튼을 끄고 2단계로 안내한다(문구).
        if (error.status === 410) {
          state.preview = null;
          $("confirm-plaintext").checked = false;
        }
        next = fillAlert("register-error", "등록하지 못했어요", error);
      } finally { setBusy(false); }
      moveTo(next);
    });
  }

  // ════════════════════════════════════════════════════════════════════════
  // 공통 이벤트 · 시작
  // ════════════════════════════════════════════════════════════════════════

  // 건너뛰기 링크로 갈 때만 <main> 이 초점을 받는다. 늘 tabindex 를 두면 본문 글자를 클릭한 뒤
  // Tab 이 1단계로 돌아가 버린다.
  document.querySelector(".skip").addEventListener("click", () => {
    const main = $("main");
    main.setAttribute("tabindex", "-1");
    main.addEventListener("blur", () => main.removeAttribute("tabindex"), { once: true });
  });

  // 화면을 준비하지 못했으면(기관 서비스가 꺼져 있었으면) 처음부터 다시 준비한다.
  // 요청 동안 꺼졌던 버튼에서 초점이 빠졌으면 돌려준다.
  $("refresh-status").addEventListener("click", async () => {
    const button = $("refresh-status");
    if (state.schema) await refreshStatus();
    else await initialize();
    if (document.activeElement === document.body || !document.activeElement) button.focus({ preventScroll: true });
  });

  // 오류 목록의 항목 이름을 누르면 그 입력란으로 간다
  document.addEventListener("click", (event) => {
    const link = event.target.closest?.("a.alert__field[href^='#']");
    const target = link && $(link.getAttribute("href").slice(1));
    if (!target) return;
    event.preventDefault();
    target.focus({ preventScroll: true });
    target.scrollIntoView({ behavior: calm() ? "auto" : "smooth", block: "center" });
  });

  // 기관 서비스가 꺼져 있었으면 "연결 다시 확인"이 이 함수를 다시 부른다.
  async function initialize() {
    if (state.busy) return;
    setBusy(true);
    let ready = false;
    try {
      state.schema = await api("/api/schema");
      if (page === "register") {
        $("file-limits").textContent = `최대 ${Math.round(state.schema.max_bytes / 1048576)} MB · ${state.schema.max_rows.toLocaleString()}행`;
        $("field-count").textContent = `의료 속성 ${fields().length}개`;
        clearAlert("file-error");
        renderMapping();
      } else {
        renderQueryForm();
      }
      ready = true;
    } catch (error) {
      state.schema = null;
      updateActive(null, null, null, { serviceDown: true });
      if (page === "register") fillAlert("file-error", "화면을 준비하지 못했어요", error);
    } finally { setBusy(false); }
    if (ready) await refreshStatus();
  }

  if (page === "register") {
    bindRegisterPage();
    renderCheck("idle");
    renderRegister("upcoming");
    renderMapping();
  } else {
    bindSearchPage();
    resetResults();
    renderQueryStep();
  }
  renderNow();
  syncButtons();
  initialize();
})();
