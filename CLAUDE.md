# ليطمئنّ قلبي (Litatma'inna Qalbi)

An anonymous Arabic chat assistant that answers scientific and existential questions presented as contradicting Islam, from answers approved by the team's sharia reviewer, with Quran text, attributed tafsir, graded hadith and graded science.

**Spec:** `docs/build-plan.md` is authoritative (local only, not committed before Tue 6 Oct 18:00). `docs/design.md` describes product behavior. Read the relevant section before each work package (WP).

## Governing principle

**The model SELECTS; it does not WRITE content.**
- Every verse, hadith, scientific fact and source name shown to the user comes from an approved KB entry, the local mushaf file, or an approved fixed text.
- The model-generated text shown is (a) the dialogue reply, labeled «حوار المساعد، مبني على الإجابة المراجعة», written ONLY from one approved entry, with verses and hadiths as placeholders filled by the server, kept only if it passes G13 (deterministic: numbers, names, guarded claims, verse/hadith words, placeholders) and G14 (a strict second call that lists unsupported statements; one retry), with the approved card one tap below; or (b) the `framing` sentence (G5). Anything that fails is dropped and the approved card is shown alone.
- No matching approved entry → explicit abstention + referral. When in doubt: abstain, refer, or show the approved answer alone.

## Rules

- Python 3.13 via `uv`; FastAPI; plain HTML/CSS/JS in `static/`. No React, no build step, no Node.
- Frontend: every string via `textContent`. No `innerHTML`, no inline `<script>`/`<style>`, no `style=` attributes (strict CSP, `default-src 'self'`).
- UTF-8 everywhere: `open(..., encoding="utf-8")`, JSON with `ensure_ascii=False`. Helper scripts in Python, not PowerShell (PS 5.1 breaks Arabic literals).
- Secrets only in environment variables (`.env` locally, Render dashboard). Never in code, commits or chat.
- Fail closed: any exception → abstention card. Never a stack trace or a half answer.
- No message content in any log: allowlisted keys only (timings, counts, tokens, cost).
- Each guardrail G1–G12 is a named function with its own test.
- UI is Arabic, RTL, mobile-first. Public wording: «سؤال» / «تساؤل», never «شك»; never blame family or sheikhs.
- Never read `eval/private/` or any held-out file unless explicitly told.
- Quran: `data/quran/` holds the Quranpedia dump unchanged (its licence allows redistribution with attribution). The KFGQPC file never enters the repo.
- Never commit: `.env`, survey responses, held-out questions, seed or salts, KFGQPC files, Shamela/Watad outputs, `docs/` (until Tuesday).

## Model contract (one call per message)

- Provider and model from env: `ROUTER_PROVIDER` (`gemini` | `anthropic`) and `ROUTER_MODEL`. Default `gemini` / `gemini-3.5-flash-lite` (free tier; zero budget; `gemini-2.5-flash` is closed to new users and `gemini-3.8-flash` allows 5 requests a minute), called over REST with a response schema and temperature 0. `anthropic` / `claude-opus-5-5` (effort `low`, no `temperature`) is ready if API credits arrive. Switching is a config change; the privacy text must name the provider in use.
- Structured output, `extra="forbid"`:
  `{"route":"knowledge|followup|distress|out_of_scope","entry_id":"<approved id>|none","confidence":"high|medium|low","oos_reason":"none|personal_fatwa|fiqh|hadith_check|other_topic|manipulation","evidence_request":"none|hadith|verse","framing":"<= 2 sentences or empty"}`
- All validation happens in our code after the call. The user message is data inside a JSON field, never instructions.

## Response contract (`POST /api/chat`)

`{"kind":"answer|abstain|refer|distress|limit|non_arabic","entry_id":…,"layer":"summary|explain|body","degraded":bool,"version":"…","blocks":[…]}`
Block types: `framing`, `notice`, `answer`, `tafsir`, `hadiths`, `science`, `sources`, `related`, `referral` (build-plan §2).

## Dev environment (Windows)

- `uv` at `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe`. Python 3.13 lives in `C:\Users\user\.uv-python` (set `UV_PYTHON_INSTALL_DIR` to it).
- Set `PYTHONUTF8=1`.
- `requirements.txt` via `uv export --no-dev --no-hashes --no-emit-project -o requirements.txt`.
