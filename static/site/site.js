// «ليطمئنّ قلبي» showcase page: scroll reveals, the three lines, the recorded conversation with its steps, count-ups,
// a reading-progress line, and golden motes behind the hero. No libraries. Without JavaScript the page shows everything
// statically: the hidden start states in site.css apply only under the "js" class set here.
document.documentElement.classList.add("js");
(() => {
  "use strict";
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const raf = (fn) => { let queued = false; return () => { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; fn(); }); }; };

  // nav: glass once the page moves (the hero's scroll cue fades then too); the progress line follows the scroll;
  // and the link of the section in view lights (none for sections without a link)
  const nav = document.getElementById("nav");
  const bar = document.getElementById("progress");
  const onScrollNav = raf(() => {
    const y = window.scrollY, max = document.documentElement.scrollHeight - window.innerHeight;
    nav.classList.toggle("scrolled", y > 12);
    document.documentElement.classList.toggle("moved", y > 40);
    if (bar) bar.style.transform = `scaleX(${max > 0 ? Math.min(1, y / max) : 0})`;
  });
  onScrollNav();
  window.addEventListener("scroll", onScrollNav, { passive: true });
  window.addEventListener("resize", onScrollNav);
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
    // the first line lights as the section reaches mid-screen; the second and the third follow
    lineEls.forEach((el, i) => el.classList.toggle("on", i === 0 ? r.top < window.innerHeight * 0.45 : p >= i * 0.3));
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

  // how it works: the recorded conversation plays by itself while it is on screen, from the question to the sources,
  // and loops. The step it is at lights up, with a bar filling over that step's stretch (data-t, in seconds, up to the
  // next step, the last up to the video's data-end); tapping a step jumps there, and a fade hides each cut
  const video = document.getElementById("demo");
  const phone = document.querySelector(".phone");
  const playBtn = document.querySelector(".play");
  const howSec = document.getElementById("how");
  const steps = [...document.querySelectorAll("[data-step]")];
  const bars = [...document.querySelectorAll("[data-bar]")];
  if (video && phone && howSec && steps.length) {
    const starts = steps.map((s) => Number(s.dataset.t));
    const START = starts[0], END = Number(video.dataset.end) || 40.5;
    const fills = steps.map((s) => s.querySelector(".step-bar i"));
    const barFills = bars.map((b) => b.querySelector("i"));
    let at = -1, onScreen = false, frameId = 0, fadeTimer = 0;

    const show = (i) => {
      if (i === at) return;
      at = i;
      steps.forEach((s, k) => {
        s.classList.toggle("on", k === i);
        const b = s.querySelector("button");
        if (k === i) b.setAttribute("aria-current", "step"); else b.removeAttribute("aria-current");
      });
      bars.forEach((b, k) => b.classList.toggle("on", k === i));
    };
    const paint = () => {
      const t = video.currentTime;
      let i = 0;
      while (i + 1 < starts.length && t >= starts[i + 1]) i++;
      show(i);
      const end = i + 1 < starts.length ? starts[i + 1] : END;
      const p = Math.min(1, Math.max(0, (t - starts[i]) / (end - starts[i])));
      fills.forEach((f, k) => { if (f) f.style.transform = `scaleX(${k === i ? p : 0})`; });
      barFills.forEach((f, k) => { if (f) f.style.transform = `scaleX(${k < i ? 1 : k === i ? p : 0})`; });
    };
    const play = () => {
      if (reduce || !onScreen || !video.src) return;
      video.play().then(() => { playBtn.hidden = true; }).catch(() => { playBtn.hidden = false; });
    };
    const go = (t) => {
      const jump = () => {
        clearTimeout(fadeTimer);
        if (reduce) { try { video.currentTime = t; } catch (_) { /* stays where it is */ } paint(); return; }
        video.classList.add("jump");
        fadeTimer = setTimeout(() => {
          video.addEventListener("seeked", () => video.classList.remove("jump"), { once: true });
          setTimeout(() => video.classList.remove("jump"), 700);  // in case no seek happens
          try { video.currentTime = t; } catch (_) { /* stays where it is */ }
          paint();
        }, 200);
      };
      if (video.readyState >= 1) jump(); else video.addEventListener("loadedmetadata", jump, { once: true });
    };
    // keep the recording between the question and the sources
    const keepInside = () => {
      if (video.classList.contains("jump")) return;
      if (video.currentTime >= END || video.currentTime < START - 0.5) go(START);
    };
    const tick = () => { paint(); keepInside(); frameId = requestAnimationFrame(tick); };
    video.addEventListener("play", () => { cancelAnimationFrame(frameId); frameId = requestAnimationFrame(tick); });
    video.addEventListener("pause", () => cancelAnimationFrame(frameId));
    video.addEventListener("timeupdate", () => { paint(); keepInside(); });
    video.addEventListener("ended", () => { go(START); play(); });
    video.addEventListener("loadedmetadata", () => { if (video.currentTime < START) video.currentTime = START; paint(); });

    steps.forEach((s, k) => s.querySelector("button").addEventListener("click", () => { go(starts[k]); play(); }));
    bars.forEach((b, k) => b.addEventListener("click", () => { go(starts[k]); play(); }));
    playBtn.addEventListener("click", () => {
      playBtn.hidden = true;
      video.play().catch(() => { playBtn.hidden = false; });
    });
    show(0);
    if (reduce) playBtn.hidden = false;

    // the recording is fetched whole (about 2 MB) as the section nears, so every jump is instant and works on any
    // host, with or without range requests; if the fetch or the blob fails it streams from the file instead
    const file = video.dataset.src;
    const attach = (src) => {
      video.preload = "auto";
      video.src = src;
      video.load();
      play();
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
    // play while the phone is well in view, pause when it is not
    new IntersectionObserver(([e]) => {
      onScreen = e.isIntersecting;
      if (onScreen) play();
      else { video.pause(); if (reduce) playBtn.hidden = false; }
    }, { threshold: 0.35 }).observe(phone);
  }

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
