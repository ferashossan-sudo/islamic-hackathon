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

  // each number waits at 0 and counts up to its value once it is well in view, a moment after its block has faded
  // in, slowly enough to be seen climbing
  const countUp = (el) => {
    const end = Number(el.dataset.count);
    if (reduce || end === 0) { el.textContent = String(end); return; }
    const delay = 250, dur = 1800;
    let t0 = 0;
    const tick = (t) => {
      if (!t0) t0 = t + delay;
      const k = Math.min(1, Math.max(0, (t - t0) / dur));
      el.textContent = String(Math.round(end * (1 - Math.pow(1 - k, 2))));
      if (k < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };
  const counts = [...document.querySelectorAll("[data-count]")];
  if (!reduce) counts.forEach((el) => { el.textContent = "0"; });
  const counter = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { countUp(e.target); counter.unobserve(e.target); }
  }, { rootMargin: "0px 0px -12% 0px", threshold: 0.6 });  // not 1: Safari can report a fully shown number at 0.99
  counts.forEach((el) => counter.observe(el));

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

  // how it works: the stage stays pinned while the scroll walks through the steps, one equal stretch of the track
  // each, and the recording follows the scroll with no cut: each step's stretch runs that step's part of the
  // conversation (from its data-t to the next step's, the last to the video's data-end), forward when scrolling down
  // and back when scrolling up. The step in view is lit and its bar fills towards the next; tapping a step scrolls
  // there. The recording is encoded with a key frame every few frames, so each seek is quick
  const video = document.getElementById("demo");
  const track = document.querySelector(".how-track");
  const stageEl = document.querySelector(".how-stage");
  const steps = [...document.querySelectorAll("[data-step]")];
  const bars = [...document.querySelectorAll("[data-bar]")];
  if (video && track && stageEl && steps.length) {
    const N = steps.length;
    const starts = steps.map((s) => Number(s.dataset.t));
    const END = Number(video.dataset.end) || 29.5;
    const ends = starts.map((t, k) => (k + 1 < N ? starts[k + 1] : END));
    const fills = steps.map((s) => s.querySelector(".step-bar i"));
    const barFills = bars.map((b) => b.querySelector("i"));
    let at = -1, target = starts[0], shown = starts[0], scrubId = 0;

    const current = (el, on) => { if (on) el.setAttribute("aria-current", "step"); else el.removeAttribute("aria-current"); };
    const show = (i) => {
      if (i === at) return;
      at = i;
      const focused = document.activeElement;
      steps.forEach((s, k) => { s.classList.toggle("on", k === i); current(s.querySelector("button"), k === i); });
      bars.forEach((b, k) => current(b, k === i));
      // on a phone only the current step shows: keep keyboard and screen-reader focus from falling off a hidden one
      if (focused && bars[i] && bars[i].offsetParent !== null && steps.some((s, k) => k !== i && s.contains(focused))) bars[i].focus({ preventScroll: true });
    };
    // ease the picture towards the scroll's moment, one seek at a time, so it glides instead of stepping
    const scrub = () => {
      scrubId = 0;
      if (video.readyState < 1) return;
      const d = target - shown;
      shown = reduce || Math.abs(d) < 0.04 ? target : shown + d * 0.3;
      if (!video.seeking && Math.abs(video.currentTime - shown) > 0.03) {
        try { video.currentTime = shown; } catch (_) { /* stays where it is */ }
      }
      if (shown !== target || video.seeking) scrubId = requestAnimationFrame(scrub);
    };
    const want = (t) => { target = t; if (!scrubId) scrubId = requestAnimationFrame(scrub); };
    video.addEventListener("loadedmetadata", () => { shown = video.currentTime; want(target); });
    video.addEventListener("seeked", () => { if (!scrubId && Math.abs(video.currentTime - target) > 0.03) want(target); });

    // the scroll's place in the track (the pinned stretch is the track minus the stage, not minus the window, which
    // grows when a phone's toolbar hides): which step, how far towards the next, and the moment of the recording
    const follow = raf(() => {
      const r = track.getBoundingClientRect();
      const range = r.height - stageEl.offsetHeight;
      const x = range > 0 ? Math.min(N - 0.001, Math.max(0, (-r.top / range) * N)) : 0;
      const i = Math.floor(x), q = x - i;
      show(i);
      fills.forEach((f, k) => { if (f) f.style.transform = `scaleX(${k === i ? q : 0})`; });
      barFills.forEach((f, k) => { if (f) f.style.transform = `scaleX(${k < i ? 1 : k === i ? q : 0})`; });
      want(starts[i] + q * (ends[i] - starts[i]));
    });
    const scrollToStep = (k) => {
      const r = track.getBoundingClientRect();
      const range = r.height - stageEl.offsetHeight;
      window.scrollTo({ top: window.scrollY + r.top + (k * range) / N + 2, behavior: reduce ? "auto" : "smooth" });
    };
    steps.forEach((s, k) => s.querySelector("button").addEventListener("click", () => scrollToStep(k)));
    bars.forEach((b, k) => b.addEventListener("click", () => scrollToStep(k)));
    follow();

    // the recording (about 4 MB) is fetched whole as the section nears, so every seek is instant on any host; if the
    // fetch or the blob fails it streams from the file instead. A muted play and pause primes it, since some phones
    // paint a seeked frame only once the video has played
    const file = video.dataset.src;
    const attach = (src) => {
      video.preload = "auto";
      video.src = src;
      video.load();
      video.addEventListener("loadeddata", () => {
        const p = video.play();
        if (p) p.then(() => { video.pause(); want(target); }).catch(() => want(target));
      }, { once: true });
    };
    video.addEventListener("error", () => { if (video.src.startsWith("blob:")) attach(file); });
    const load = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return;
      load.disconnect();
      fetch(file).then((r) => (r.ok ? r.blob() : Promise.reject(r.status)))
        .then((b) => attach(URL.createObjectURL(new Blob([b], { type: "video/mp4" }))))
        .catch(() => attach(file));
    }, { rootMargin: "150% 0px" });
    load.observe(track);
    // follow the scroll only while the track is near
    new IntersectionObserver(([e]) => {
      if (e.isIntersecting) { window.addEventListener("scroll", follow, { passive: true }); follow(); }
      else window.removeEventListener("scroll", follow);
    }, { rootMargin: "50% 0px" }).observe(track);
    window.addEventListener("resize", follow);
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
