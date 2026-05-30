// 진도 read/write + 섹션 추적.
// serve.py가 /api/progress/{key} GET/POST를 제공한다.
// key: "global" 또는 안전한 파일명 (chapter_id 등)
(function () {
  "use strict";

  const API = (key) => `/api/progress/${encodeURIComponent(key)}`;

  async function getJSON(key) {
    try {
      const r = await fetch(API(key), { method: "GET" });
      if (!r.ok) return null;
      return await r.json();
    } catch (e) {
      console.warn("progress GET failed:", e);
      return null;
    }
  }

  async function postJSON(key, data) {
    try {
      await fetch(API(key), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
    } catch (e) {
      console.warn("progress POST failed:", e);
    }
  }

  function debounce(fn, ms) {
    let t = null;
    return (...args) => {
      if (t) clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  function throttle(fn, ms) {
    let last = 0;
    let pending = null;
    return (...args) => {
      const now = Date.now();
      if (now - last >= ms) {
        last = now;
        fn(...args);
      } else {
        if (pending) clearTimeout(pending);
        pending = setTimeout(() => {
          last = Date.now();
          fn(...args);
        }, ms - (now - last));
      }
    };
  }

  // ---------------------- Chapter page ----------------------
  async function initChapterPage(chapterId) {
    const state = {
      chapter_id: chapterId,
      last_position: null,
      completed: false,
      answers: {},
      mc_score: { correct: 0, total: 0 },
    };

    const persisted = await getJSON(chapterId);
    if (persisted && typeof persisted === "object") {
      Object.assign(state, persisted);
    }

    const postChapter = throttle(() => {
      state.last_updated = new Date().toISOString();
      postJSON(chapterId, state);
    }, 2000);
    const postGlobal = throttle((position) => {
      postJSON("global", {
        last_chapter: chapterId,
        last_position: position,
        last_updated: new Date().toISOString(),
      });
    }, 2000);

    // 답안 복원
    restoreAnswers(state.answers || {});
    updateMcSummary(state.mc_score);

    // last_position scroll
    if (state.last_position) {
      const el = document.getElementById(state.last_position);
      if (el) setTimeout(() => el.scrollIntoView({ behavior: "instant", block: "start" }), 50);
    }

    // 객관식 채점 hook
    document.querySelectorAll(".question.mc").forEach((qel) => {
      const qid = qel.dataset.qid;
      const answerIdx = parseInt(qel.dataset.answer, 10);
      qel.querySelectorAll("input[type=radio]").forEach((input) => {
        input.addEventListener("change", () => {
          const sel = parseInt(input.value, 10);
          const correct = sel === answerIdx;
          state.answers[qid] = { selected: sel, correct };
          // 라벨 클래스 갱신
          qel.querySelectorAll(".options label").forEach((lab, i) => {
            lab.classList.toggle("correct", i === answerIdx);
            lab.classList.toggle("wrong", i === sel && sel !== answerIdx);
          });
          const exp = qel.querySelector(".explanation");
          if (exp) exp.hidden = false;
          recountMc(state);
          updateMcSummary(state.mc_score);
          postChapter();
        });
      });
    });

    // 단답/주관/확장 입력 hook
    document.querySelectorAll(".question.text").forEach((qel) => {
      const qid = qel.dataset.qid;
      const ta = qel.querySelector("textarea");
      const reveal = qel.querySelector(".reveal");
      const answerBlock = qel.querySelector(".model-answer");
      const debouncedSave = debounce(() => {
        const prev = state.answers[qid] || {};
        state.answers[qid] = {
          text: ta.value,
          viewed_answer: !!prev.viewed_answer,
        };
        postChapter();
      }, 1000);
      if (ta) ta.addEventListener("input", debouncedSave);
      if (reveal && answerBlock) {
        reveal.addEventListener("click", () => {
          answerBlock.hidden = false;
          reveal.disabled = true;
          const prev = state.answers[qid] || { text: ta ? ta.value : "" };
          state.answers[qid] = { text: prev.text || (ta ? ta.value : ""), viewed_answer: true };
          postChapter();
        });
      }
    });

    // last_position(섹션 자동 스크롤 복원용)은 IntersectionObserver로 추적.
    // 진행률 자동 측정은 하지 않음 — 완료 여부는 아래의 명시적 버튼에서만 토글.
    const sections = document.querySelectorAll("article > section[id]");
    if (sections.length) {
      const visible = new Set();
      const ordered = Array.from(sections).map((s) => s.id);
      const io = new IntersectionObserver((entries) => {
        for (const e of entries) {
          if (e.isIntersecting) visible.add(e.target.id);
          else visible.delete(e.target.id);
        }
        if (visible.size) {
          const first = ordered.find((id) => visible.has(id));
          if (first) {
            state.last_position = first;
            postChapter();
            postGlobal(first);
          }
        }
      }, { threshold: 0.3 });
      sections.forEach((s) => io.observe(s));
    }

    // 완료 토글 버튼
    const completeBtn = document.querySelector(".complete-btn");
    if (completeBtn) {
      const applyCompletedUI = () => {
        const done = !!state.completed;
        completeBtn.setAttribute("aria-pressed", done ? "true" : "false");
        completeBtn.querySelector(".label").textContent =
          done ? "완료 표시됨 (다시 누르면 취소)" : "이 챕터 완료로 표시";
        setSidebarCompleted(chapterId, done);
      };
      applyCompletedUI();
      completeBtn.addEventListener("click", () => {
        state.completed = !state.completed;
        applyCompletedUI();
        postChapter();
      });
    }

    // 페이지 떠날 때 한 번 더 sync (best-effort)
    window.addEventListener("beforeunload", () => {
      state.last_updated = new Date().toISOString();
      try {
        const blob = new Blob([JSON.stringify(state)], { type: "application/json" });
        navigator.sendBeacon(API(chapterId), blob);
      } catch (_) {}
    });

    // 사이드바 초기화: 모든 챕터 완료 여부 fetch + 자기 챕터는 현재 state로 즉시 반영
    setSidebarCompleted(chapterId, !!state.completed);
    refreshSidebarOthers(chapterId);
    initSidebarToggle();
  }

  function setSidebarCompleted(chapterId, done) {
    const link = document.querySelector(
      `.sidebar-link[data-chapter="${chapterId}"]`
    );
    if (link) link.classList.toggle("is-completed", !!done);
  }

  async function refreshSidebarOthers(currentId) {
    const others = document.querySelectorAll(
      `.sidebar-link[data-chapter]:not([data-chapter="${currentId}"])`
    );
    await Promise.all(
      Array.from(others).map(async (el) => {
        const cid = el.dataset.chapter;
        const p = await getJSON(cid);
        if (!p) return;
        setSidebarCompleted(cid, !!p.completed);
      })
    );
  }

  function initSidebarToggle() {
    const sidebar = document.getElementById("sidebar");
    const toggle = document.querySelector(".sidebar-toggle");
    const scrim = document.querySelector(".sidebar-scrim");
    if (!sidebar || !toggle) return;

    const close = () => {
      sidebar.classList.remove("is-open");
      toggle.setAttribute("aria-expanded", "false");
      if (scrim) scrim.hidden = true;
    };
    const open = () => {
      sidebar.classList.add("is-open");
      toggle.setAttribute("aria-expanded", "true");
      if (scrim) scrim.hidden = false;
    };
    toggle.addEventListener("click", () => {
      sidebar.classList.contains("is-open") ? close() : open();
    });
    if (scrim) scrim.addEventListener("click", close);
    // 모바일에서 다른 챕터로 이동할 때 닫힘 상태로 — 새 페이지 로드라 자동 처리.
    // ESC로도 닫기
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && sidebar.classList.contains("is-open")) close();
    });
  }

  function restoreAnswers(answers) {
    for (const [qid, a] of Object.entries(answers)) {
      const qel = document.querySelector(`.question[data-qid="${qid}"]`);
      if (!qel) continue;
      if (qel.classList.contains("mc") && typeof a.selected === "number") {
        const input = qel.querySelector(`input[type=radio][value="${a.selected}"]`);
        if (input) input.checked = true;
        const answerIdx = parseInt(qel.dataset.answer, 10);
        qel.querySelectorAll(".options label").forEach((lab, i) => {
          lab.classList.toggle("correct", i === answerIdx);
          lab.classList.toggle("wrong", i === a.selected && a.selected !== answerIdx);
        });
        const exp = qel.querySelector(".explanation");
        if (exp) exp.hidden = false;
      } else if (qel.classList.contains("text")) {
        const ta = qel.querySelector("textarea");
        if (ta && typeof a.text === "string") ta.value = a.text;
        if (a.viewed_answer) {
          const block = qel.querySelector(".model-answer");
          const btn = qel.querySelector(".reveal");
          if (block) block.hidden = false;
          if (btn) btn.disabled = true;
        }
      }
    }
  }

  function recountMc(state) {
    let correct = 0, total = 0;
    document.querySelectorAll(".question.mc").forEach((qel) => {
      const qid = qel.dataset.qid;
      total += 1;
      const a = state.answers[qid];
      if (a && a.correct) correct += 1;
    });
    state.mc_score = { correct, total };
  }

  function updateMcSummary(score) {
    const el = document.querySelector(".mc-summary");
    if (!el || !score) return;
    el.innerHTML = `객관식 정답 <strong>${score.correct}</strong> / ${score.total}`;
  }

  // ---------------------- Index page ----------------------
  async function initIndexPage() {
    const global = await getJSON("global");
    if (global && global.last_chapter) {
      const el = document.querySelector(`.chapter-link[data-chapter="${global.last_chapter}"]`);
      if (el) el.classList.add("last-read");
    }
    const links = document.querySelectorAll(".chapter-link[data-chapter]");
    await Promise.all(
      Array.from(links).map(async (el) => {
        const cid = el.dataset.chapter;
        const p = await getJSON(cid);
        if (!p) return;
        const done = !!p.completed;
        el.classList.toggle("is-completed", done);
        const txt = el.querySelector(".status-text");
        if (txt) {
          let s = done ? "완료" : "아직 학습하지 않음";
          if (p.mc_score && p.mc_score.total) {
            s += ` · 객관식 ${p.mc_score.correct}/${p.mc_score.total}`;
          }
          txt.textContent = s;
        }
      })
    );
  }

  // ---------------------- bootstrap ----------------------
  document.addEventListener("DOMContentLoaded", () => {
    const main = document.querySelector("[data-page]");
    if (!main) return;
    const page = main.dataset.page;
    if (page === "chapter") {
      const cid = main.dataset.chapterId;
      if (cid) initChapterPage(cid);
    } else if (page === "index") {
      initIndexPage();
    }
  });
})();
