# Asfi Tech YT — Standalone YouTube Automation Project

This is a **separate project** from Asfi Blog on purpose — different repo, different Vercel
project, different resource limits. It only shares two things with the blog conceptually:
the same OpenRouter account (for AI generation) and the same overall automation philosophy.

Nothing here can break or slow down the blog, and vice versa.

---

## What's built so far (Step 1 of 3)

`api/generate-video-script.js` — pulls a real trending topic from YouTube, writes a
faceless-explainer script (voiceover + visual notes + on-screen text), and generates
SEO + AEO + GEO optimized title/description/tags/chapters — in simple, clear English.

**Not built yet** (next steps, in order):
- Step 2: Text-to-speech + video assembly (turns the script into an actual .mp4)
- Step 3: Auto-upload to YouTube (YouTube Data API `videos.insert`)

Step 2 cannot run on Vercel serverless (video rendering needs more time/memory than
serverless functions allow) — it needs a different host. Covered below.

---

## Required APIs / accounts (all have free tiers)

| # | Service | What it's for | Free tier limit | Get it from |
|---|---|---|---|---|
| 1 | **YouTube Data API v3** | Fetch trending videos + later, upload videos | 10,000 units/day (trending fetch ≈1 unit, one upload ≈1600 units → ~6 uploads/day free) | [Google Cloud Console](https://console.cloud.google.com/) → enable "YouTube Data API v3" → Credentials → API key |
| 2 | **OpenRouter** | AI script + metadata generation (same account as the blog) | Free models available; paid models are pennies per script | [openrouter.ai](https://openrouter.ai/) |
| 3 | **GitHub (new, separate repo)** | Stores this project's code + `data/video-topics-history.json` (used-topic memory, so we never repeat a topic) | Free | github.com — create a **new** repo, e.g. `asfi-tech-yt`, don't reuse the blog repo |
| 4 | **Vercel (new, separate project)** | Hosts `api/generate-video-script.js`, runs it on a schedule | Free (Hobby plan) | vercel.com — import the new GitHub repo as a **new** project |
| 5 | **ElevenLabs** *(Step 2, not yet needed)* | Text-to-speech voiceover, natural-sounding, free tier | 10,000 characters/month free (~2-3 videos worth) | elevenlabs.io |
| 6 | **Pexels API** *(Step 2, not yet needed)* | Free stock footage/B-roll for the video visuals | Free, generous limit | pexels.com/api |

---

## Where things get stored

- **Code**: new GitHub repo (`asfi-tech-yt` or whatever you name it) — completely separate
  from the `Asfi_blog-main` repo.
- **Used-topic history**: `data/video-topics-history.json` inside that same new repo —
  this is how the system remembers which trending topics it already made a video about,
  so it doesn't repeat itself. Same idea as `posts-data.js` dedupe on the blog, just for videos.
- **Generated scripts**: for now, the API just *returns* the script as JSON (you'll see it
  in the response). Once Step 2 (video render) exists, we'll decide whether to also save
  scripts to the repo or pass them straight through to rendering.
- **Rendered videos** *(Step 2)*: NOT on Vercel or GitHub (too large/slow for both) —
  will use a dedicated render host once we build that step (options: GitHub Actions
  runner with ffmpeg, or a small free-tier VM). We'll pick this together when we get there.

---

## Environment variables to set (Vercel → Settings → Environment Variables)

```
CRON_SECRET          = choose any long random string yourself
OPENROUTER_API_KEY   = from openrouter.ai
YOUTUBE_API_KEY      = from Google Cloud Console
GITHUB_TOKEN         = a GitHub Personal Access Token (repo scope) for the NEW repo
GITHUB_OWNER         = your GitHub username
GITHUB_REPO          = the new repo name (e.g. asfi-tech-yt)
```

Then in `vercel.json`, replace `YOUR_SECRET_HERE` in both cron paths with your real
`CRON_SECRET` value.

---

## Upload frequency reality check (2-4 videos/day goal)

- Vercel's free Hobby plan allows cron jobs, but each cron entry can only run **once
  per day** (not multiple times per day) under current free-tier rules. That's why
  `vercel.json` above defines **two separate cron entries** at different times (8 AM
  and 2 PM) — each fires once daily — giving you 2 auto-generated scripts/day for free.
- To reach 3-4/day, either add 1-2 more cron entries the same way, or trigger the
  endpoint manually (just visit the URL with your secret) for the extra ones.
- Remember: this endpoint only makes the **script**, not the final video. Actual
  upload frequency depends on Step 2 (render) and Step 3 (upload) being built and
  fast enough to keep up — we'll size that realistically once we get there, so the
  2-4/day promise doesn't turn into rushed, low-quality videos. Quality first, volume second.

---

## Honest monetization timeline (no shortcuts, no fake traffic)

YouTube Partner Program requirements (as of now): **1,000 subscribers** AND
**4,000 valid public watch hours in the last 12 months** (or 10M Shorts views in 90 days
as an alternative path). There is no legitimate way to skip this — and trying to fake it
(bots, purchased views, auto-refresh) risks permanent channel termination, covered earlier
in this chat. Realistic path: consistent daily uploads in one niche, genuine SEO/AEO
optimization (already built into the script generator), and patience — most channels that
hit monetization doing this properly take **2-6 months** of consistent uploads, not days.
