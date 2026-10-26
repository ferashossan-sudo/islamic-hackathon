// «ليطمئنّ قلبي» showcase page: scroll reveals, the three lines, the steps driving the recorded conversation,
// count-ups, and golden motes behind the hero. No libraries. Without JavaScript the page shows everything statically:
// the hidden start states in site.css apply only under the "js" class set here.
document.documentElement.classList.add("js");
(() => {
  "use strict";
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const narrowMQ = window.matchMedia("(max-width: 760px)");
  const raf = (fn) => { let queued = false; return () => { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; fn(); }); }; };

  // nav: glass once the page moves, and the link of the section in view (none for sections without a link)
  const nav = document.getElementById("nav");
  const onScrollNav = () => nav.classList.toggle("scrolled", window.scrollY > 12);
  onScrollNav();
  window.addEventListener("scroll", onScrollNav, { passive: true });
  const links = [...document.querySelectorAll(".nav-links a")];
  const linkFor = new Map(links.map((a) => [a.getAttribute("href").slice(1), a]));
  const setNav = (id) => links.forEach((a) => {
    const on = a === linkFor.get(id);
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
  });
  const navSpy = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) setNav(e.target.id);
  }, { rootMargin: "-45% 0px -50% 0px" });
  document.querySelectorAll("main > section[id]").forEach((s) => navSpy.observe(s));

  // count-up for each number, once, when the number itself comes into view
  const countUp = (el) => {
    const end = Number(el.dataset.count);
    if (reduce || end === 0) { el.textContent = String(end); return; }
    const t0 = performance.now(), dur = 1300;
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / dur);
      el.textContent = String(Math.round(end * (1 - Math.pow(1 - k, 3))));
      if (k < 1) requestAnimationFrame(tick);
    };
    el.textContent = "0";
    requestAnimationFrame(tick);
  };
  const counter = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { countUp(e.target); counter.unobserve(e.target); }
  }, { rootMargin: "0px 0px -15% 0px" });
  document.querySelectorAll("[data-count]").forEach((el) => counter.observe(el));

  // reveal on scroll
  const reveal = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); reveal.unobserve(e.target); }
  }, { rootMargin: "0px 0px -5% 0px", threshold: .08 });
  document.querySelectorAll("[data-reveal]").forEach((el) => reveal.observe(el));

  // the three lines light up one by one while their tall section is on screen
  const linesSec = document.querySelector("[data-lines]");
  const lineEls = [...document.querySelectorAll("[data-line]")];
  const onScrollLines = raf(() => {
    const r = linesSec.getBoundingClientRect();
    const span = r.height - window.innerHeight;
    const p = Math.min(1, Math.max(0, -r.top / (span || 1)));
    // the first line lights as the section reaches mid-screen; the second, the third and the note follow
    lineEls.forEach((el, i) => el.classList.toggle("on", i === 0 ? r.top < window.innerHeight * 0.45 : p >= i * 0.26));
  });
  if (reduce) lineEls.forEach((el) => el.classList.add("on"));
  else {
    onScrollLines();
    new IntersectionObserver(([e]) => {
      if (e.isIntersecting) window.addEventListener("scroll", onScrollLines, { passive: true });
      else window.removeEventListener("scroll", onScrollLines);
      onScrollLines();
    }).observe(linesSec);
  }

  // steps: the step under the reading line is active (mid-screen on desktop, just below the recording on a phone),
  // and the recording plays that step's moment in a loop (data-t to data-end, in seconds); a fade hides each cut
  const video = document.getElementById("demo");
  const phone = document.querySelector(".phone");
  const stage = document.querySelector(".stage");
  const howSec = document.getElementById("how");
  const steps = [...document.querySelectorAll("[data-step]")];
  let current = null, fadeTimer = 0, howVisible = false;

  const seek = (t) => {
    const go = () => {
      clearTimeout(fadeTimer);
      const jump = () => {
        try { video.currentTime = t; } catch (_) { /* keeps playing where it is */ }
        if (!reduce && howVisible) video.play().catch(() => {});
      };
      if (reduce) { jump(); return; }
      video.classList.add("jump");
      fadeTimer = setTimeout(() => {
        video.addEventListener("seeked", () => video.classList.remove("jump"), { once: true });
        setTimeout(() => video.classList.remove("jump"), 600);  // in case no seek happens
        jump();
      }, 200);
    };
    if (video.readyState >= 1) go();
    else {
      video.addEventListener("loadedmetadata", go, { once: true });
      if (video.src && video.networkState !== HTMLMediaElement.NETWORK_LOADING) video.load();  // preload=none waits
      if (!reduce && howVisible) video.play().catch(() => {});  // iOS may hold the load back until play()
    }
  };
  const activate = (step) => {
    if (!step || step === current) return;
    current = step;
    steps.forEach((s) => s.classList.toggle("on", s === step));
    if (phone) phone.dataset.at = String(steps.indexOf(step));
    const t = Number(step.dataset.t);
    if (video && video.src && Number.isFinite(t)) seek(t);
  };
  if (video) {
    // keep the recording inside the active step's moment: past its end, or before its start (after the clip ends)
    video.addEventListener("timeupdate", () => {
      if (!current || video.classList.contains("jump")) return;
      const t = Number(current.dataset.t), end = Number(current.dataset.end);
      if ((Number.isFinite(end) && video.currentTime >= end) || video.currentTime < t - 0.5) seek(t);
    });
    video.addEventListener("ended", () => { if (current) seek(Number(current.dataset.t)); });
  }
  const pickStep = raf(() => {
    // phone: hand over when the next text nears the bottom of the one pinned under the recording, so they never
    // overlap (each threshold uses the previous text's height, so the choice only moves forward)
    if (narrowMQ.matches && stage) {
      const pin = stage.getBoundingClientRect().bottom + 10;
      let pick = steps[0];
      for (let i = 1; i < steps.length; i++) {
        const prev = steps[i - 1].querySelector(".step-body");
        if (steps[i].getBoundingClientRect().top <= pin + prev.offsetHeight + 24) pick = steps[i];
      }
      activate(pick);
      return;
    }
    const line = narrowMQ.matches && stage ? stage.getBoundingClientRect().bottom + 40 : window.innerHeight / 2;
    for (const s of steps) {
      const r = s.getBoundingClientRect();
      if (r.top <= line && r.bottom > line) { activate(s); return; }
    }
    if (!current && steps[0] && steps[0].getBoundingClientRect().top > line) activate(steps[0]);
  });

  if (video && howSec) {
    // the recording is fetched whole (about 2 MB) as the section nears, so every jump is instant and works on any
    // host, with or without range requests; if the fetch or the blob fails it streams from the file instead
    const file = video.dataset.src;
    const attach = (src) => {
      video.preload = "auto";
      video.src = src;
      video.load();
      if (current) { const step = current; current = null; activate(step); }
    };
    video.addEventListener("error", () => { if (video.src.startsWith("blob:")) attach(file); });
    const load = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return;
      load.disconnect();
      fetch(file).then((r) => (r.ok ? r.blob() : Promise.reject(r.status)))
        .then((b) => attach(URL.createObjectURL(new Blob([b], { type: "video/mp4" }))))
        .catch(() => attach(file));
    }, { rootMargin: "150% 0px" });
    load.observe(howSec);
    // follow the scroll only while the section is on screen, and pause the recording when it is not
    new IntersectionObserver(([e]) => {
      howVisible = e.isIntersecting;
      if (howVisible) { window.addEventListener("scroll", pickStep, { passive: true }); pickStep(); if (current && video.src && !reduce) video.play().catch(() => {}); }
      else {
        // leaving mid-fade must not strand the recording hidden; coming back re-activates the step
        window.removeEventListener("scroll", pickStep); clearTimeout(fadeTimer); video.pause();
        video.classList.remove("jump"); current = null;
      }
    }).observe(howSec);
    window.addEventListener("resize", pickStep);
  }
  if (steps[0]) steps[0].classList.add("on");

  // golden motes rising slowly behind the hero; paused when the hero is out of view
  const cv = document.getElementById("motes");
  if (cv && !reduce) {
    const ctx = cv.getContext("2d");
    let w = 0, h = 0, motes = [], running = false, frameId = 0, last = 0;
    const make = (fresh) => ({
      x: Math.random() * w, y: fresh ? Math.random() * h : h + 10,
      r: 0.6 + Math.random() * 1.8, v: 0.12 + Math.random() * 0.35, a: 0.15 + Math.random() * 0.5,
      ph: Math.random() * Math.PI * 2, dr: (Math.random() - 0.5) * 0.25,
    });
    const size = () => {
      const nw = cv.clientWidth, nh = cv.clientHeight;
      if (nw === w && Math.abs(nh - h) < 140 && motes.length) return;  // the phone URL bar showing or hiding
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const sx = w ? nw / w : 1, sy = h ? nh / h : 1;
      w = nw; h = nh;
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const n = Math.round(Math.min(90, (w * h) / 16000));
      motes = motes.length ? motes.map((m) => ({ ...m, x: m.x * sx, y: m.y * sy })) : [];
      while (motes.length < n) motes.push(make(true));
      motes.length = n;
    };
    const frame = (t) => {
      const dt = last ? Math.min(3, (t - last) / 16.67) : 1;
      last = t;
      ctx.clearRect(0, 0, w, h);
      for (const m of motes) {
        m.y -= m.v * dt; m.x += (m.dr + Math.sin(t / 1800 + m.ph) * 0.12) * dt;
        if (m.y < -10) Object.assign(m, make(false));
        const tw = 0.6 + 0.4 * Math.sin(t / 900 + m.ph);
        ctx.beginPath();
        ctx.fillStyle = `rgba(223, 205, 172, ${(m.a * tw).toFixed(3)})`;
        ctx.arc(m.x, m.y, m.r, 0, Math.PI * 2);
        ctx.fill();
      }
      if (running) frameId = requestAnimationFrame(frame);
    };
    size();
    new ResizeObserver(size).observe(cv);
    new IntersectionObserver((entries) => {
      running = entries[entries.length - 1].isIntersecting;
      cancelAnimationFrame(frameId);
      last = 0;
      if (running) frameId = requestAnimationFrame(frame);
    }).observe(cv);
  }
})();
