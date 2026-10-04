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
    card.dir = "auto";
    paragraphs(card, text);
    log.append(card);
    scrollToEnd(card);
  }

  function externalLink(text, url, className) {
    const link = el("a", className || null, text);
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  }

  // Text with verse segments: verses come from the mushaf on the server and are shown with their reference.
  function segmentsInto(parent, segments) {
    let paragraph = el("p");
    parent.append(paragraph);
    (segments || []).forEach((seg) => {
      if (seg.type === "verse") {
        paragraph.append(el("span", "verse-inline", "﴿" + seg.text + "﴾"), " ", el("bdi", "verse-ref", "[" + seg.label + "]"));
        return;
      }
      String(seg.text).split("\n").forEach((part, i) => {
        if (i > 0) {
          paragraph = el("p");
          parent.append(paragraph);
        }
        if (part) paragraph.append(part);
      });
    });
    parent.querySelectorAll("p").forEach((p) => { if (!p.textContent.trim()) p.remove(); });
  }

  function section(card, title, className) {
    const box = el("section", "block " + (className || ""));
    if (title) box.append(el("h3", "block-title", title));
    card.append(box);
    return box;
  }

  let degradedShown = false;

  const renderers = {
    framing(card, b) {
      const box = el("p", "block block-framing");
      const badge = el("span", "tag tag-framing", b.label);
      badge.title = b.hint || "";
      box.append(badge, " ", b.text);
      card.append(box);
    },
    message(card, b) {
      const box = el("div", "block block-message");
      if (b.lang === "en") {
        box.lang = "en";
        box.dir = "ltr";
      }
      paragraphs(box, b.text);
      card.append(box);
    },
    notice(card, b) {
      const box = el("div", "block block-notice");
      paragraphs(box, b.text);
      card.append(box);
    },
    referral(card, b) {
      const box = el("div", "block block-referral");
      if (b.url) box.append(externalLink(b.label || b.url, b.url, "button-link"));
      if (b.text) box.append(el("p", "hint", b.text));
      card.append(box);
    },
    contacts(card, b) {
      const list = el("div", "block contacts");
      (b.items || []).forEach((item) => {
        const call = el("a", "call");
        call.href = "tel:" + item.number;
        call.append(el("span", "call-label", item.label), el("bdi", "call-number", item.number));
        list.append(call);
        const meta = [item.note, item.source ? "المصدر: " + item.source : ""].filter(Boolean).join(" · ");
        if (meta) list.append(el("p", "hint", meta));
      });
      card.append(list);
    },
    answer(card, b) {
      const box = el("section", "block block-answer");
      const tags = el("p", "tags");
      tags.append(el("span", "tag tag-reviewed", b.badge), el("span", "tag", b.level));
      box.append(tags);
      segmentsInto(box, b.summary);
      if (b.explain_simple && b.open === "explain") {
        const simple = el("div", "explain");
        segmentsInto(simple, b.explain_simple);
        box.append(simple);
      }
      const full = el("details", "full-answer");
      if (b.open === "body") full.open = true;
      full.append(el("summary", null, label("read_full", "اقرأ الإجابة كاملة")));
      segmentsInto(full, b.body);
      box.append(full);
      card.append(box);
    },
    sharia(card, b) {
      const box = section(card, label("sharia_texts", "النصوص الشرعية"), "block-sharia");
      (b.verses || []).forEach((v) => {
        box.append(el("p", "verse", "﴿" + v.text + "﴾"), el("p", "verse-ref", "[" + v.label + "]"));
      });
      (b.tafsir || []).forEach((t) => {
        const item = el("div", "tafsir");
        paragraphs(item, t.summary);
        const line = el("p", "hint", t.label + " · ");
        line.append(externalLink(label("verify", "تحقق من المصدر"), t.url));
        item.append(line);
        box.append(item);
      });
      (b.hadiths || []).forEach((h) => {
        const item = el("div", "hadith");
        item.append(el("p", null, h.text));
        const line = el("p", "hint", h.line + " · ");
        line.append(externalLink(label("verify", "تحقق من المصدر"), h.url));
        item.append(line);
        if (h.via) item.append(el("p", "hint", h.via));
        box.append(item);
      });
    },
    science(card, b) {
      const box = section(card, label("science", "معلومات علمية"), "block-science");
      box.append(el("p", "hint", b.note));
      (b.items || []).forEach((s) => {
        const item = el("div", "science-item");
        item.append(el("span", "tag degree degree-" + s.degree, s.degree_label), el("p", null, s.claim));
        const line = el("p", "hint", s.source + " · ");
        line.append(externalLink(label("verify", "تحقق من المصدر"), s.url));
        item.append(line);
        box.append(item);
      });
    },
    sources(card, b) {
      const box = section(card, label("sources", "المصادر"), "block-sources");
      const list = el("ul");
      (b.items || []).forEach((s) => {
        const li = el("li");
        li.append(externalLink([s.name, s.locator].filter(Boolean).join(" "), s.url));
        list.append(li);
      });
      box.append(list);
    },
    review(card, b) {
      card.append(el("p", "hint review-line", b.text));
    },
    related(card, b) {
      const box = section(card, b.title || label("related", "أسئلة مرتبطة"), "block-related");
      (b.items || []).forEach((r) => {
        const chip = el("button", "chip", r.question);
        chip.type = "button";
        chip.addEventListener("click", () => {
          input.value = r.question;
          submit();
        });
        box.append(chip);
      });
    },
  };

  function renderResponse(data) {
    const card = el("article", "msg bot kind-" + (data.kind || "abstain"));
    if (data.degraded) {
      const text = config.texts && (degradedShown ? config.texts.degraded_badge : config.texts.degraded_mode);
      if (text) card.append(el("p", degradedShown ? "tag tag-degraded" : "block block-notice", text));
      degradedShown = true;
    }
    (data.blocks || []).forEach((block) => {
      const render = renderers[block.type];
      if (render) render(card, block);
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
