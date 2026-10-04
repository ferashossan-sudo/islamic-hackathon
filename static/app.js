"use strict";
// Chat client. Every string reaches the page through textContent; no HTML is built from data.
(function () {
  const MAX_CHARS = 800;
  const log = document.getElementById("log");
  const form = document.getElementById("composer");
  const input = document.getElementById("message");
  const sendButton = document.getElementById("send");
  const counter = document.getElementById("counter");
  const clearButton = document.getElementById("clear");

  const mode = new URLSearchParams(window.location.search).get("mode") === "offline" ? "offline" : "";
  const context = { prev_entry_id: null, recent: [], repeat_count: 0 };
  let config = { labels: {}, texts: {} };
  let turn = 0;
  let busy = false;

  fetch("/api/config")
    .then((r) => r.json())
    .then((c) => { config = c; })
    .catch(() => {});

  function label(key, fallback) {
    return (config.labels && config.labels[key]) || fallback;
  }

  function sessionId() {
    try {
      let id = window.sessionStorage.getItem("sid");
      if (!id) {
        id = window.crypto.randomUUID();
        window.sessionStorage.setItem("sid", id);
      }
      return id;
    } catch (e) {
      return "";
    }
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function paragraphs(parent, text) {
    String(text || "").split("\n").forEach((line) => {
      if (line.trim()) parent.append(el("p", null, line));
    });
  }

  function scrollToEnd(node) {
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    node.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "end" });
  }

  function addUserMessage(text) {
    const card = el("article", "msg user");
    paragraphs(card, text);
    log.append(card);
    scrollToEnd(card);
  }

  function renderResponse(data) {
    const card = el("article", "msg bot kind-" + (data.kind || "abstain"));
    (data.blocks || []).forEach((block) => {
      if (block.type === "message" || block.type === "notice") {
        const box = el("div", "block block-" + block.type);
        paragraphs(box, block.text);
        card.append(box);
      } else if (block.type === "referral") {
        const box = el("div", "block block-referral");
        if (block.url) {
          const link = el("a", "button-link", block.label || block.url);
          link.href = block.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          box.append(link);
        }
        if (block.text) box.append(el("p", "hint", block.text));
        card.append(box);
      }
    });
    log.append(card);
    scrollToEnd(card);
  }

  function renderNetworkError(text) {
    const card = el("article", "msg bot kind-error");
    paragraphs(card, (config.texts && config.texts.network_error) || "تعذّر الاتصال بالخدمة الآن.");
    const retry = el("button", "button-link", label("retry", "أعد المحاولة"));
    retry.type = "button";
    retry.addEventListener("click", () => {
      card.remove();
      input.value = text;
      submit();
    });
    card.append(retry);
    if (config.texts && config.texts.support_line) card.append(el("p", "hint", config.texts.support_line));
    log.append(card);
    scrollToEnd(card);
  }

  function setBusy(state) {
    busy = state;
    sendButton.disabled = state;
    log.setAttribute("aria-busy", state ? "true" : "false");
  }

  async function submit() {
    const text = input.value.trim();
    if (!text || busy) return;
    setBusy(true);
    const pending = el("p", "msg bot pending", label("loading", "…"));
    log.append(pending);
    scrollToEnd(pending);
    turn += 1;
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, sid: sessionId(), turn: turn, mode: mode, context: context }),
      });
      const data = await response.json();
      pending.remove();
      if (response.ok) {
        addUserMessage(text);
        input.value = "";
        updateCounter();
        if (data.entry_id) {
          context.prev_entry_id = data.entry_id;
          context.recent = context.recent.concat([{ entry_id: data.entry_id, kind: data.kind, layer: data.layer || "summary" }]).slice(-10);
        }
      }
      renderResponse(data);
    } catch (e) {
      pending.remove();
      renderNetworkError(text);
    } finally {
      setBusy(false);
      input.focus();
    }
  }

  function updateCounter() {
    const length = input.value.length;
    if (length > MAX_CHARS * 0.8) {
      counter.textContent = label("char_counter", "{العدد} من 800").replace("{العدد}", String(length));
      counter.classList.toggle("over", length > MAX_CHARS);
    } else {
      counter.textContent = "";
    }
  }

  function autoGrow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit();
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submit();
    }
  });

  input.addEventListener("input", () => {
    updateCounter();
    autoGrow();
  });

  clearButton.addEventListener("click", () => {
    if (!window.confirm(label("clear_chat_confirm", "تُمسح المحادثة من هذه الصفحة."))) return;
    log.querySelectorAll(".msg:not(.welcome)").forEach((node) => node.remove());
    context.prev_entry_id = null;
    context.recent = [];
    context.repeat_count = 0;
    turn = 0;
    input.focus();
  });

  document.querySelectorAll("[data-open]").forEach((link) => {
    link.addEventListener("click", () => {
      const target = document.getElementById(link.getAttribute("data-open"));
      if (target) target.open = true;
    });
  });
})();
