"use strict";
// Chat client. Every string reaches the page through textContent; no HTML is built from data.
(function () {
  const MAX_CHARS = 800;
  const log = document.getElementById("log");
  const form = document.getElementById("composer");
  const input = document.getElementById("message");
  const sendButton = document.getElementById("send");
  const counter = document.getElementById("counter");
  const app = document.getElementById("app");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const STARTERS = 4;

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

  // Suggested questions under the empty composer: approved entries marked as featured, most asked first.
  function renderFeatured(items) {
    const box = document.getElementById("starters");
    if (!box) return;
    items.slice(0, STARTERS).forEach((item) => {
      const chip = el("button", "chip", item.question);
      chip.type = "button";
      chip.addEventListener("click", () => {
        input.value = item.question;
        submit();
      });
      box.append(chip);
    });
  }

  // Outline icons (24×24, stroke), built as SVG nodes: no markup from strings.
  const SVG_NS = "http://www.w3.org/2000/svg";
  const ICONS = {
    book: ["M12 7v14", "M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"],
    link: ["M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71", "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"],
    layers: ["M12 2 2 7l10 5 10-5-10-5z", "m2 17 10 5 10-5", "m2 12 10 5 10-5"],
    text: ["M17 6.1H3", "M21 12.1H3", "M15.1 18H3"],
    retry: ["M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8", "M21 3v5h-5"],
  };

  function icon(name, size) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", String(size || 15));
    svg.setAttribute("height", String(size || 15));
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "icon");
    (ICONS[name] || []).forEach((d) => {
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", d);
      svg.append(path);
    });
    return svg;
  }

  // The one-line summary of a folded part: an icon, the text (cut with an ellipsis until opened), a chevron in CSS.
  function summary(text, iconName) {
    const node = el("summary");
    if (iconName) node.append(icon(iconName));
    node.append(el("span", "sum-text", text));
    return node;
  }

  // A verse inside text: «﴿…﴾ [البقرة: 255]», the ornate brackets in the accent color.
  function verseInline(text, ref) {
    const verse = el("span", "verse-inline");
    verse.append(el("span", "orn", "﴿"), text, el("span", "orn", "﴾"));
    return [verse, " ", el("bdi", "verse-ref", "[" + ref + "]")];
  }

  function setEmpty(empty) {
    app.classList.toggle("is-empty", empty);
  }

  function appendToLog(node) {
    log.append(node);
    setEmpty(false);
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

  function scrollToEnd(node, block) {
    node.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: block || "end" });
  }

  function addUserMessage(text) {
    const card = el("article", "msg user");
    card.dir = "auto";
    paragraphs(card, text);
    appendToLog(card);
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
        paragraph.append(...verseInline(seg.text, seg.label));
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
    (segments || []).forEach((seg) => {
      if (seg.type === "verse") {
        paragraph.append(...verseInline(seg.text, seg.label));
      } else if (seg.type === "hadith") {
        if (seg.text.length > LONG_HADITH) {
          // A long hadith folds below the sentence instead of breaking it; nothing is cut from its text.
          const box = el("details", "hadith-long toggle");
          box.append(summary(label("hadith_full", "نص الحديث كاملاً"), "text"), el("p", "hadith-inline", "«" + seg.text + "»"));
          parent.append(box);
          paragraph = el("p");
          parent.append(paragraph);
        } else {
          paragraph.append(el("span", "hadith-inline", "«" + seg.text + "»"));
        }
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
  }

  const renderers = {
    chat(card, b) {
      const box = el("div", "block block-chat");
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
      const full = el("details", "full-answer toggle");
      if (b.open === "body") full.open = true;
      full.append(summary(label("read_full", "اقرأ الإجابة كاملة"), "text"));
      segmentsInto(full, b.body);
      box.append(full);
      card.append(box);
    },
    // «بالعقل والعلم»: the reviewed chain of reasoning, then the common objections, each folded under its question.
    reasoning(card, b, bare) {
      const box = section(card, b.title || label("reasoning_title", "بالعقل والعلم"), "block-reasoning");
      const sourceLine = (item, prefix) => {
        if (bare) return "";
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
      shariaInto(section(card, label("sharia_texts", "النصوص الشرعية"), "block-sharia"), b);
    },
    science(card, b) {
      const box = section(card, label("science", "معلومات علمية"), "block-science");
      box.append(el("p", "hint", b.note));
      // What each degree means, for the degrees this answer uses («حقيقة ثابتة: ثبتت بالرصد…»).
      const used = new Set((b.items || []).map((s) => s.degree_label));
      String((config.texts && config.texts.science_degree_hints) || "").split("\n").forEach((line) => {
        if (used.has(line.split(":")[0].trim())) box.append(el("p", "hint degree-hint", line));
      });
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

  // The verses (mushaf text with their reference), tafsir summaries and hadiths of the approved answer.
  function shariaInto(box, b) {
    (b.verses || []).forEach((v) => {
      const verse = el("p", "verse");
      verse.append(el("span", "orn", "﴿"), v.text, el("span", "orn", "﴾"));
      box.append(verse, el("p", "verse-ref", "[" + v.label + "]"));
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
  }

  // The sources of the answer, once each, as links in one folded line under the reply: its own sources, the sources
  // of its reasoning, its tafsir, its hadiths and its scientific statements. The reply itself names none of them.
  // A source's name without its page or section: «الدرر السنية: الموسوعة العقدية»، «U.S. Department of Energy, DOE
  // Explains». A tafsir keeps its surah.
  function shortName(name) {
    const text = String(name || "").trim();
    const colon = text.indexOf(":");
    if (/^[A-Za-z]/.test(text)) return colon > 0 ? text.slice(0, colon) : text;
    const cut = colon < 0 ? -1 : text.indexOf("، ", colon);
    if (cut < 0) return text;
    const rest = text.slice(cut + 2);
    if (!rest.startsWith("سورة")) return text.slice(0, cut);
    const next = rest.indexOf("، ");
    return text.slice(0, cut + 2) + (next < 0 ? rest : rest.slice(0, next));
  }

  function sourcesInto(card, blocks) {
    const items = [];
    const seen = new Set();
    const add = (fullName, url) => {
      const name = shortName(fullName);
      const key = String(url || "").split("#")[0];
      if (!name || !key || seen.has(key) || seen.has(name)) return;
      seen.add(key);
      seen.add(name);
      items.push({ name, url });
    };
    const of = (type) => blocks.filter((b) => b.type === type);
    of("sources").forEach((b) => (b.items || []).forEach((s) => add(s.name, s.url)));
    of("reasoning").forEach((b) => (b.steps || []).concat(b.objections || []).forEach((s) => add(s.source, s.url)));
    of("sharia").forEach((b) => {
      (b.tafsir || []).forEach((x) => add(String(x.label || "").split("المصدر: ").pop() || x.mufassir, x.url));
      (b.hadiths || []).forEach((h) => add(String(h.line || "").replace(/^المصدر: /, "").split(" · ")[0], h.url));
    });
    of("science").forEach((b) => (b.items || []).forEach((s) => add(s.source, s.url)));
    if (!items.length) return null;
    const box = el("details", "block sources-compact toggle");
    box.append(summary(label("sources", "المصادر") + " (" + items.length + ")", "link"));
    const list = el("ul");
    items.forEach((s) => {
      const li = el("li");
      li.append(externalLink(s.name, s.url));
      list.append(li);
    });
    box.append(list);
    card.append(box);
    return box;
  }

  // The reply arrives the way a person writes: word after word, quickly; verses, hadiths and folded parts appear
  // whole. The text is in the page from the start (only its opacity changes), so the card never jumps and screen
  // readers get all of it. The sources line fades in when the reply is done.
  function typeIn(box, after) {
    if (after) after.classList.add("tw-after");
    const finish = () => { if (after) after.classList.add("in"); };
    if (reducedMotion || !box) {
      finish();
      return;
    }
    const units = [];
    const split = (parent) => {
      Array.from(parent.childNodes).forEach((node) => {
        if (node.nodeType === Node.TEXT_NODE) {
          const frag = document.createDocumentFragment();
          node.textContent.split(/(\s+)/).forEach((part) => {
            if (!part) return;
            if (!part.trim()) {
              frag.append(part);
              return;
            }
            const word = el("span", "tw", part);
            units.push(word);
            frag.append(word);
          });
          node.replaceWith(frag);
        } else if (node.nodeType === Node.ELEMENT_NODE) {
          node.classList.add("tw");
          units.push(node);
        }
      });
    };
    Array.from(box.children).forEach((child) => {
      if (child.tagName === "P") split(child);
      else if (child.tagName === "OL" || child.tagName === "UL") {
        Array.from(child.children).forEach((li) => {
          li.classList.add("tw");
          units.push(li);
        });
      } else {
        child.classList.add("tw");
        units.push(child);
      }
    });
    if (!units.length) {
      finish();
      return;
    }
    const step = Math.max(14, Math.min(38, 2600 / units.length));  // two and a half seconds at most
    const start = performance.now();
    let shown = 0;
    const reveal = (target) => {
      while (shown < target) units[shown++].classList.add("in");
      if (shown >= units.length) finish();
    };
    // A paused page (another tab, a locked phone) shows everything at once when the time is up.
    const safety = window.setTimeout(() => reveal(units.length), units.length * step + 1500);
    const tick = (now) => {
      if (shown >= units.length) return;
      reveal(Math.min(units.length, Math.floor((now - start) / step) + 1));
      if (shown < units.length) window.requestAnimationFrame(tick);
      else window.clearTimeout(safety);
    };
    window.requestAnimationFrame(tick);
  }

  function renderResponse(data) {
    const card = el("article", "msg bot kind-" + (data.kind || "abstain"));
    if (data.degraded) {
      const text = config.texts && (degradedShown ? config.texts.degraded_badge : config.texts.degraded_mode);
      if (text) card.append(el("p", degradedShown ? "tag tag-degraded" : "block block-notice", text));
      degradedShown = true;
    }
    const blocks = data.blocks || [];
    if (!blocks.some((b) => b.type === "answer")) {
      // Referrals, abstentions, support: shown as they come.
      blocks.forEach((block) => renderers[block.type] && renderers[block.type](card, block));
      appendToLog(card);
      scrollToEnd(card, "start");
      if (data.kind !== "distress") typeIn(card.querySelector(".block-message"), null);  // support shows at once
      return;
    }
    // One message, like a conversation: the reply (or, without one, the approved summary, or its reasoning when the
    // person asked to be convinced by reason), a line for a request the service never does, and one folded line
    // with the links to the sources.
    const chat = blocks.find((b) => b.type === "chat");
    const lead = !chat && blocks[0].type === "reasoning" ? blocks[0] : null;
    const shown = chat ? ["chat", "message", "notice"] : lead ? ["message", "notice"] : ["answer", "message", "notice"];
    if (lead) renderers.reasoning(card, lead, true);
    blocks.forEach((block) => {
      if (block.key === "level_c_notice") return;  // the sharia reviewer: no warning line with the reply
      if (shown.includes(block.type) && renderers[block.type]) renderers[block.type](card, block);
    });
    const sources = sourcesInto(card, blocks);
    appendToLog(card);
    scrollToEnd(card, "start");
    typeIn(card.querySelector(".block-chat, .block-answer, .block-reasoning"), sources);
  }

  function renderNetworkError(text) {
    const card = el("article", "msg bot kind-error");
    paragraphs(card, (config.texts && config.texts.network_error) || "تعذّر الاتصال بالخدمة الآن.");
    const retry = el("button", "button-link");
    retry.type = "button";
    retry.append(icon("retry", 16), label("retry", "أعد المحاولة"));
    retry.addEventListener("click", () => {
      card.remove();
      submit(text);
    });
    card.append(retry);
    if (config.texts && config.texts.support_line) card.append(el("p", "hint", config.texts.support_line));
    appendToLog(card);
    scrollToEnd(card);
  }

  function setBusy(state) {
    busy = state;
    updateSend();
    log.setAttribute("aria-busy", state ? "true" : "false");
  }

  // «ليطمئنّ قلبي» is looking: three dots, and the label for screen readers and long waits.
  function typingIndicator() {
    const box = el("div", "typing");
    box.setAttribute("role", "status");
    const dots = el("div", "typing-dots");
    dots.append(el("span"), el("span"), el("span"));
    box.append(dots, el("span", "visually-hidden", label("loading", "…")));
    return box;
  }

  // The question shows at once and the composer moves down; a retry after a network error resends it as it was.
  async function submit(retryText) {
    const retrying = typeof retryText === "string";
    const text = retrying ? retryText : input.value.trim();
    if (!text || busy) return;
    setBusy(true);
    if (!retrying) {
      addUserMessage(text);
      input.value = "";
      updateCounter();
      autoGrow();
    }
    const pending = typingIndicator();
    appendToLog(pending);
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
        if (data.entry_id) {
          context.prev_entry_id = data.entry_id;
          context.recent = context.recent.concat([{ entry_id: data.entry_id, kind: data.kind, layer: data.layer || "summary" }]).slice(-10);
        }
      } else if (!input.value) {
        input.value = text;  // too long or limited: the text comes back to be edited
        updateCounter();
        autoGrow();
      }
      renderResponse(data);
    } catch (e) {
      pending.remove();
      renderNetworkError(text);
    } finally {
      setBusy(false);
      if (!window.matchMedia("(pointer: coarse)").matches) input.focus();  // phones keep the keyboard closed
    }
  }

  function updateSend() {
    sendButton.disabled = busy || !input.value.trim();
  }

  function updateCounter() {
    const length = input.value.length;
    counter.textContent = String(length) + " / " + String(MAX_CHARS);
    counter.classList.toggle("near", length > MAX_CHARS * 0.94 && length <= MAX_CHARS);
    counter.classList.toggle("over", length > MAX_CHARS);
    updateSend();
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

  // «حفظ المحادثة PDF»: the browser's print dialog, where «Save as PDF» keeps the conversation on the person's own
  // device. Nothing is sent anywhere. Every folded part (sources, the approved card) is opened for the copy, and the
  // print stylesheet writes each link's address next to it, so the sources can be checked later from the PDF.
  let reopened = [];
  window.addEventListener("beforeprint", () => {
    reopened = Array.from(log.querySelectorAll("details:not([open])"));
    reopened.forEach((d) => { d.open = true; });
    const date = document.getElementById("print-date");
    if (date) date.textContent = new Date().toLocaleDateString("ar-SA-u-ca-gregory", { dateStyle: "long" });
  });
  window.addEventListener("afterprint", () => {
    reopened.forEach((d) => { d.open = false; });
    reopened = [];
  });

  function clearConversation() {
    log.replaceChildren();
    setEmpty(true);
    context.prev_entry_id = null;
    context.recent = [];
    context.repeat_count = 0;
    sentMessages = [];
    history = [];
    turn = 0;
  }

  // The sheet: the menu, and the about, privacy, support and clear panels, one at a time.
  const sheet = document.getElementById("sheet");
  const sheetTitle = document.getElementById("sheet-title");
  let sheetOpener = null;

  function openSheet(name) {
    let title = "";
    sheet.querySelectorAll("[data-panel]").forEach((panel) => {
      const on = panel.getAttribute("data-panel") === name;
      panel.hidden = !on;
      if (on) title = panel.getAttribute("data-title") || "";
    });
    sheetTitle.textContent = title;
    const empty = !log.querySelector(".msg");
    document.getElementById("m-new").disabled = empty;
    document.getElementById("m-pdf").disabled = empty;
    if (sheet.hidden) {
      sheetOpener = document.activeElement;
      sheet.classList.remove("closing");
      sheet.hidden = false;
    }
    sheet.querySelector(".sheet-head [data-close]").focus();
  }

  function closeSheet(then) {
    if (sheet.hidden || sheet.classList.contains("closing")) return;
    sheet.classList.add("closing");
    window.setTimeout(() => {
      sheet.hidden = true;
      sheet.classList.remove("closing");
      if (sheetOpener && document.contains(sheetOpener)) sheetOpener.focus();
      sheetOpener = null;
      if (then) then();
    }, reducedMotion ? 0 : 260);
  }

  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-sheet]");
    if (opener) {
      openSheet(opener.getAttribute("data-sheet"));
      return;
    }
    if (event.target.closest("[data-close]")) closeSheet();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !sheet.hidden) closeSheet();
  });
  document.getElementById("menu-open").addEventListener("click", () => openSheet("menu"));
  document.getElementById("m-new").addEventListener("click", () => openSheet("clear"));
  document.getElementById("m-pdf").addEventListener("click", () => closeSheet(() => window.print()));
  document.getElementById("confirm-clear").addEventListener("click", () => closeSheet(clearConversation));

  // First visit: the splash, «أسئلتك الكبيرة تستحق إجابة مقنعة», then «قبل أن نبدأ» with three confirmations.
  // Only the fact that they were confirmed is remembered on this device, so the next visit opens the conversation.
  const CONSENT_KEY = "lq-consent-v1";
  const onboarding = document.getElementById("onboarding");
  // «?intro», for filming the demo: the introduction again even after consent, and the splash waits for a click.
  const INTRO = new URLSearchParams(window.location.search).has("intro");

  function consentGiven() {
    try {
      return window.localStorage.getItem(CONSENT_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function showApp(arrive) {
    onboarding.hidden = true;
    app.hidden = false;
    if (arrive) app.classList.add("arrive");
    updateCounter();
  }

  function startOnboarding() {
    const splash = document.getElementById("splash");
    const steps = document.getElementById("steps");
    const welcome = document.getElementById("step-welcome");
    const consent = document.getElementById("step-consent");
    const next = document.getElementById("onb-next");
    const checks = ["c-age", "c-ai", "c-privacy"].map((id) => document.getElementById(id));
    const later = (fn, ms) => window.setTimeout(fn, reducedMotion ? 0 : ms);
    let leftSplash = false;
    const allChecked = () => checks.every((c) => c.checked);

    function show(step, enter) {
      steps.setAttribute("data-step", step);
      welcome.hidden = step !== "welcome";
      consent.hidden = step !== "consent";
      (step === "welcome" ? welcome : consent).setAttribute("data-enter", enter);
      next.textContent = step === "welcome" ? "متابعة" : "ابدأ المحادثة";
      next.disabled = step === "consent" && !allChecked();
    }

    function go(step, enter) {
      steps.classList.add("leaving");
      later(() => {
        steps.classList.remove("leaving");
        show(step, enter);
      }, 190);
    }

    function leaveSplash() {
      if (leftSplash) return;
      leftSplash = true;
      window.clearTimeout(timer);
      splash.classList.add("out");
      later(() => {
        splash.hidden = true;
        steps.hidden = false;
        show("welcome", "up");
        next.focus();
      }, 850);
    }

    const timer = INTRO ? 0 : window.setTimeout(leaveSplash, reducedMotion ? 1500 : 5200);
    splash.addEventListener("click", leaveSplash);
    document.getElementById("consent-back").addEventListener("click", () => go("welcome", "back"));
    checks.forEach((c) => c.addEventListener("change", () => {
      c.closest(".check").classList.toggle("on", c.checked);
      next.disabled = !allChecked();
      next.classList.toggle("glow", allChecked());
    }));
    next.addEventListener("click", () => {
      if (steps.getAttribute("data-step") === "welcome") {
        go("consent", "fwd");
        return;
      }
      if (!allChecked()) return;
      try {
        window.localStorage.setItem(CONSENT_KEY, "1");
      } catch (e) { /* private window: asked again next visit */ }
      onboarding.classList.add("leaving");
      later(() => showApp(true), 260);
    });
  }

  if (consentGiven() && !INTRO) {
    showApp(false);
  } else {
    startOnboarding();
  }
})();
