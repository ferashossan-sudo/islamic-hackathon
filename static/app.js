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
  let sentMessages = [];  // normalized, for repeat_count only; never sent to the server
  let history = [];  // last turns, sent with each message so the conversation can continue; never stored
  const REPEAT_SIMILARITY = 0.8;

  // Same rule as app/arabic.py: strip marks and tatweel, unify letter forms, keep words.
  function normalize(text) {
    return String(text)
      .normalize("NFC")
      .replace(/[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u08d3-\u08ff\u0640\u00ad\u200b-\u200f\u2060-\u2064\ufeff]/g, "")
      .replace(/[أإآٱٲٳ]/g, "ا").replace(/[ىیئ]/g, "ي").replace(/ة/g, "ه").replace(/ؤ/g, "و").replace(/ء/g, "").replace(/ک/g, "ك")
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s_]/gu, " ")
      .split(/\s+/).filter(Boolean).join(" ");
  }

  function trigramSimilarity(a, b) {
    const grams = (t) => {
      const set = new Set();
      if (t.length < 3) { set.add(t); return set; }
      for (let i = 0; i <= t.length - 3; i++) set.add(t.slice(i, i + 3));
      return set;
    };
    const ga = grams(a), gb = grams(b);
    let shared = 0;
    ga.forEach((g) => { if (gb.has(g)) shared += 1; });
    const union = ga.size + gb.size - shared;
    return union ? shared / union : 0;
  }

  fetch("/api/config")
    .then((r) => r.json())
    .then((c) => {
      config = c;
      if (c.preview_drafts) {
        const banner = el("p", "preview-banner", "معاينة داخلية لمسودات لم يعتمدها المراجع الشرعي بعد");
        document.body.prepend(banner);
      }
      renderFeatured(c.featured || []);
    })
    .catch(() => {});

  // Suggested questions under the welcome message: approved entries marked as featured.
  function renderFeatured(items) {
    const welcome = document.querySelector(".msg.welcome");
    if (!welcome || !items.length) return;
    const box = el("div", "block block-related featured");
    items.forEach((item) => {
      const chip = el("button", "chip", item.question);
      chip.type = "button";
      chip.addEventListener("click", () => {
        input.value = item.question;
        submit();
      });
      box.append(chip);
    });
    welcome.append(box);
  }

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

  // The conversational reply: text, verses from the mushaf, and hadiths from the approved entry.
  const LONG_HADITH = 280;

  function chatInto(parent, segments) {
    let paragraph = el("p");
    parent.append(paragraph);
    const notes = [];
    (segments || []).forEach((seg) => {
      if (seg.type === "verse") {
        paragraph.append(el("span", "verse-inline", "﴿" + seg.text + "﴾"), " ", el("bdi", "verse-ref", "[" + seg.label + "]"));
      } else if (seg.type === "hadith") {
        if (seg.text.length > LONG_HADITH) {
          // A long hadith folds below the sentence instead of breaking it; nothing is cut from its text.
          const box = el("details", "hadith-long");
          box.append(el("summary", "", label("hadith_full", "نص الحديث كاملاً")), el("p", "hadith-inline", "«" + seg.text + "»"));
          parent.append(box);
          paragraph = el("p");
          parent.append(paragraph);
        } else {
          paragraph.append(el("span", "hadith-inline", "«" + seg.text + "»"));
        }
        notes.push(seg);
      } else {
        String(seg.text).split("\n").forEach((part, i) => {
          if (i > 0) {
            paragraph = el("p");
            parent.append(paragraph);
          }
          if (part) paragraph.append(part);
        });
      }
    });
    parent.querySelectorAll("p").forEach((p) => { if (!p.textContent.trim()) p.remove(); });
    notes.forEach((h) => {
      const line = el("p", "hint", h.line + " · ");
      line.append(externalLink(label("verify", "تحقق من المصدر"), h.url));
      parent.append(line);
    });
  }

  const renderers = {
    chat(card, b) {
      const box = el("div", "block block-chat");
      const badge = el("span", "tag tag-chat", b.label);
      badge.title = b.hint || "";
      box.append(badge);
      chatInto(box, b.segments);
      card.append(box);
    },
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
      if (!b.body || !b.body.length) {
        card.append(box);
        return;
      }
      const full = el("details", "full-answer");
      if (b.open === "body") full.open = true;
      full.append(el("summary", null, label("read_full", "اقرأ الإجابة كاملة")));
      segmentsInto(full, b.body);
      box.append(full);
      card.append(box);
    },
    // «بالعقل والعلم»: the reviewed chain of reasoning, then the common objections, each folded under its question.
    reasoning(card, b) {
      const box = section(card, b.title || label("reasoning_title", "بالعقل والعلم"), "block-reasoning");
      const sourceLine = (item, prefix) => {
        const line = el("p", "hint", prefix ? prefix + " · " : "");
        line.append(externalLink([item.source, item.locator].filter(Boolean).join(" "), item.url));
        return line;
      };
      const steps = el("ol", "reasoning-steps");
      (b.steps || []).forEach((s) => {
        const li = el("li", "reasoning-step");
        li.append(el("p", null, s.text), sourceLine(s, s.basis_label));
        steps.append(li);
      });
      box.append(steps);
      const objections = b.objections || [];
      if (!objections.length) return;
      box.append(el("h4", "block-subtitle", b.objections_title || label("objections_title", "اعتراضات شائعة وجوابها")));
      objections.forEach((o) => {
        const item = el("details", "objection");
        item.append(el("summary", null, o.objection));
        paragraphs(item, o.response);
        item.append(sourceLine(o, ""));
        box.append(item);
      });
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
        if (h.verify_url) line.append(" · ", externalLink(label("verify_grade", "حكمه في الموسوعة الحديثية"), h.verify_url));
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
    guidance(card, b) {
      const box = el("div", "block block-notice");
      segmentsInto(box, b.summary);
      card.append(box);
    },
    glossary(card, b) {
      const box = el("div", "block block-glossary");
      box.lang = "en";
      box.dir = "ltr";
      box.append(el("p", "hint", b.title));
      (b.items || []).forEach((t) => {
        const item = el("p");
        item.append(el("strong", null, t.term_en), " (", el("bdi", null, t.term_ar), "): ", t.definition_en, " ");
        item.append(externalLink("Source", t.url));
        box.append(item);
      });
      card.append(box);
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
    const blocks = data.blocks || [];
    let target = card;
    blocks.forEach((block, i) => {
      const render = renderers[block.type];
      if (!render) return;
      render(target, block);
      if (i === 0 && block.type === "chat") {
        // The approved answer and its sources stay one tap away under the conversational reply.
        const details = el("details", "card-details");
        details.append(el("summary", null, block.toggle || "الإجابة المراجعة ومصادرها"));
        card.append(details);
        target = details;
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
    const normalized = normalize(text);
    context.repeat_count = sentMessages.filter((m) => trigramSimilarity(m, normalized) >= REPEAT_SIMILARITY).length;
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, sid: sessionId(), turn: turn, mode: mode, context: context,
                               history: history.slice(-4) }),
      });
      const data = await response.json();
      pending.remove();
      if (response.ok) {
        sentMessages.push(normalized);
        const chat = (data.blocks || []).find((b) => b.type === "chat");
        history.push({ role: "user", text: text.slice(0, 1200) });
        if (chat) history.push({ role: "assistant", text: chat.history_text.slice(0, 1200) });
        history = history.slice(-4);
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
    sentMessages = [];
    history = [];
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
