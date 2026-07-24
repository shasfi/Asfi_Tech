// api/generate-video-script.js
// STANDALONE PROJECT — separate from the Asfi Blog repo/Vercel project on purpose.
// Vercel Serverless Function
// URL after deploy: https://YOUR-YT-PROJECT.vercel.app/api/generate-video-script?secret=YOUR_SECRET
//
// STEP 1 of the YouTube automation pipeline (free stack):
// 1. Fetch REAL trending videos from YouTube Data API v3 (mostPopular chart, free quota)
// 2. Pick a topic not already used before (de-duped against a small history file
//    kept in the GitHub repo, same pattern as posts-data.js for the blog)
// 3. Generate a faceless-explainer video script (voice-over only, no on-camera talent)
//    using OpenRouter, optimized for:
//      - SEO   -> YouTube/Google search (title, description, tags)
//      - AEO   -> Answer Engines (ChatGPT/Perplexity) via a clear Q&A block
//      - GEO   -> Generative Engines via structured, factual, citable phrasing
// 4. Return JSON only — this endpoint does NOT do TTS, video assembly, or upload.
//    Those are separate future steps (api/render-video.js, api/upload-youtube.js)
//    that will consume this endpoint's output.
//
// CUSTOM TOPIC (manual override):
//   /api/generate-video-script?secret=YOUR_SECRET&topic=iPhone%2017%20review
//
// REQUIRED ENV VARS (Vercel > Settings > Environment Variables):
//   CRON_SECRET          - same secret used by generate-blog.js
//   OPENROUTER_API_KEY   - same key already used by generate-blog.js
//   YOUTUBE_API_KEY       - free from Google Cloud Console (enable "YouTube Data API v3")
//   GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO - same as generate-blog.js, used to
//                          store used-topic history at data/video-topics-history.json

export default async function handler(req, res) {
  function extractJSON(text) {
    const start = text.indexOf("{");
    const end = text.lastIndexOf("}");
    if (start === -1 || end === -1) return text;
    return text.slice(start, end + 1);
  }

  try {
    const authHeader = req.headers.authorization || "";
    const headerOk = authHeader === `Bearer ${process.env.CRON_SECRET}`;
    const queryOk = req.query.secret === process.env.CRON_SECRET;
    if (!headerOk && !queryOk) {
      return res.status(401).json({ error: "Unauthorized" });
    }

    const OPENROUTER_KEY = process.env.OPENROUTER_API_KEY;
    const YOUTUBE_KEY = process.env.YOUTUBE_API_KEY;
    const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
    const GITHUB_OWNER = process.env.GITHUB_OWNER;
    const GITHUB_REPO = process.env.GITHUB_REPO;

    if (!OPENROUTER_KEY) return res.status(500).json({ error: "Missing OPENROUTER_API_KEY" });

    const customTopic = (req.query.topic || req.body?.topic || "").trim();
    const regionCode = (req.query.region || "US").trim();

    // ---------------------------------------------------------------------
    // STEP 1: get a topic — either the custom one, or pull YouTube trending
    // ---------------------------------------------------------------------
    let candidateTopics = [];

    if (customTopic) {
      candidateTopics = [{ title: customTopic, description: "" }];
    } else {
      if (!YOUTUBE_KEY) {
        return res.status(500).json({ error: "Missing YOUTUBE_API_KEY (needed for trending fetch — or pass ?topic= to skip it)" });
      }
      // videoCategoryId=28 = "Science & Technology" on YouTube. Without this filter,
      // chart=mostPopular returns the SITE-WIDE trending chart (music, sports,
      // entertainment, diss tracks, etc.) — completely unrelated to an AI/Tech channel.
      // Allow override via ?category=<id> if you ever want a different niche.
      const categoryId = (req.query.category_id || "28").trim();
      const trendingUrl = `https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&chart=mostPopular&regionCode=${regionCode}&videoCategoryId=${categoryId}&maxResults=25&key=${YOUTUBE_KEY}`;
      const trendingRes = await fetch(trendingUrl);
      const trendingData = await trendingRes.json();
      if (trendingData.error) {
        return res.status(500).json({ error: "YouTube API error", detail: trendingData.error });
      }
      candidateTopics = (trendingData.items || []).map((v) => ({
        title: v.snippet?.title || "",
        description: (v.snippet?.description || "").slice(0, 200),
        category: v.snippet?.categoryId,
        views: v.statistics?.viewCount,
      }));

      // Safety net: YouTube's mostPopular + videoCategoryId filter can occasionally
      // still include off-topic items (movie trailers, music, etc. that got
      // mis-tagged, or a sparse regional tech chart). Drop anything that doesn't
      // actually look tech/AI-related before trusting the list.
      const techPattern = /\b(ai|artificial intelligence|tech|technology|software|app|coding|programming|robot|chip|gadget|smartphone|iphone|android|computer|gpu|processor|startup|saas|cyber|data|cloud|automation)\b/i;
      candidateTopics = candidateTopics.filter(
        (t) => techPattern.test(t.title) || techPattern.test(t.description)
      );

      // The channel is specifically AI-focused ("AI With Asfi"), not general tech —
      // so prefer AI-specific topics over things like hardware repair guides when
      // both are available. This is a stable sort, so within each group we keep
      // YouTube's own trending order (mostPopular is already ranked by trending rank,
      // so item 0 is more "trending" than item 10 — we don't want to lose that).
      const aiPattern = /\b(ai|artificial intelligence|chatgpt|gpt|llm|machine learning|openai|anthropic|claude|gemini|copilot|neural|generative ai)\b/i;
      candidateTopics = candidateTopics
        .map((t, i) => ({ ...t, _rank: i, _isAI: aiPattern.test(t.title) || aiPattern.test(t.description) }))
        .sort((a, b) => (b._isAI - a._isAI) || (a._rank - b._rank));

      // Science & Technology trending is a much smaller list than the general chart
      // and can occasionally come back empty for a region — fall back to a keyword
      // search instead of failing outright.
      if (!candidateTopics.length) {
        const searchUrl = `https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&order=viewCount&publishedAfter=${new Date(Date.now() - 7 * 86400000).toISOString()}&q=${encodeURIComponent("AI|technology|tech news|AI tools")}&regionCode=${regionCode}&maxResults=25&key=${YOUTUBE_KEY}`;
        const searchRes = await fetch(searchUrl);
        const searchData = await searchRes.json();
        candidateTopics = (searchData.items || [])
          .map((v) => ({
            title: v.snippet?.title || "",
            description: (v.snippet?.description || "").slice(0, 200),
            category: "28",
          }))
          .filter((t) => techPattern.test(t.title) || techPattern.test(t.description));
      }
    }

    if (!candidateTopics.length) {
      return res.status(500).json({ error: "No candidate topics found" });
    }

    // ---------------------------------------------------------------------
    // De-dupe against previously used topics (stored in the repo, same idea
    // as posts-data.js dedupe for the blog) so we don't make the same video twice.
    // ---------------------------------------------------------------------
    let usedTitles = [];
    let historyFile = null;
    const historyApi = GITHUB_OWNER && GITHUB_REPO
      ? `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/data/video-topics-history.json`
      : null;

    if (historyApi && GITHUB_TOKEN) {
      const histGet = await fetch(historyApi, { headers: { Authorization: `Bearer ${GITHUB_TOKEN}` } });
      if (histGet.ok) {
        historyFile = await histGet.json();
        try {
          usedTitles = JSON.parse(Buffer.from(historyFile.content, "base64").toString("utf-8"));
        } catch (e) {
          usedTitles = [];
        }
      }
    }

    function normalize(s) {
      return s.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
    }
    const usedSet = new Set(usedTitles.map(normalize));

    // ---------------------------------------------------------------------
    // Quality gate + TIERED selection. Previously this only accepted trending
    // items with "AI" literally in the title, which was too strict — real
    // trending Sci&Tech topics (new phones, apps, gadgets) rarely say "AI" in
    // the title even when relevant to a tech channel, so almost everything
    // fell through to the small curated list, causing repetitive LLM-heavy
    // topics. Now we try three tiers, in order, before giving up to curated:
    //   Tier 1: trending + AI-specific title (best — real trending AI news)
    //   Tier 2: trending + general tech title (still genuinely trending, just
    //           not AI-specific — phones, apps, gadgets, software launches)
    //   Tier 3: curated evergreen AI topics (only if trending has nothing usable)
    // ---------------------------------------------------------------------
    const aiPattern2 = /\b(ai|artificial intelligence|chatgpt|gpt|llm|machine learning|openai|anthropic|claude|gemini|copilot|neural|generative ai)\b/i;
    const techPattern2 = /\b(ai|tech|technology|software|app|coding|programming|robot|chip|gadget|smartphone|iphone|android|computer|gpu|processor|startup|saas|cyber|data|cloud|automation|update|feature|review|launch)\b/i;
    const isThin = (t) => (t.description || "").trim().length < 60;
    const isHashtagSpam = (t) => (t.title.match(/#/g) || []).length >= 2;
    const isUsable = (t) => !usedSet.has(normalize(t.title)) && !isThin(t) && !isHashtagSpam(t);

    let fresh =
      candidateTopics.find((t) => isUsable(t) && aiPattern2.test(t.title)) ||
      candidateTopics.find((t) => isUsable(t) && techPattern2.test(t.title));

    const CURATED_AI_TOPICS = [
      { title: "How Large Language Models Actually Work", description: "An evergreen explainer on the mechanics behind models like ChatGPT and Claude — tokens, training, and inference, explained simply." },
      { title: "5 AI Tools That Save You Hours Every Week", description: "A practical roundup of free/low-cost AI tools for productivity, writing, and research." },
      { title: "ChatGPT vs Claude vs Gemini: Which AI Should You Actually Use", description: "A practical comparison of the major AI assistants — strengths, weaknesses, and which one fits which task." },
      { title: "What Is a Neural Network, Really?", description: "A simple, visual explanation of how neural networks learn, for a non-technical audience." },
      { title: "How AI Image Generators Turn Text Into Pictures", description: "An explainer on diffusion models and how tools like Midjourney and DALL-E work under the hood." },
      { title: "The Biggest AI Myths People Still Believe", description: "Debunking common misconceptions about how AI models think, learn, and what they can and can't do." },
      { title: "How to Write Better AI Prompts (Beginner Guide)", description: "A practical, example-driven guide to getting better results from ChatGPT, Claude, and other AI tools." },
      { title: "AI Agents Explained: What They Are and Why Everyone's Talking About Them", description: "A clear breakdown of what an 'AI agent' actually means and how it differs from a regular chatbot." },
      { title: "How Does Face Recognition Actually Work?", description: "An explainer on the computer vision techniques behind face ID and photo tagging." },
      { title: "Why Every Big Tech Company Is Racing to Build AI Chips", description: "A look at why GPUs and custom AI chips became the most valuable hardware in tech." },
      { title: "What Is Edge AI and Why Does It Matter?", description: "An explainer on running AI models directly on phones and devices instead of the cloud." },
      { title: "How AI Voice Cloning Actually Works", description: "A breakdown of the technology behind realistic AI voice generation and its risks." },
      { title: "The Difference Between Machine Learning and Deep Learning", description: "A simple explainer clarifying two terms people often use interchangeably but mean different things." },
      { title: "How Self-Driving Cars 'See' the Road", description: "An explainer on the sensors and AI models that let autonomous vehicles navigate." },
      { title: "What Is a GPU and Why Does AI Need So Many?", description: "A beginner-friendly explainer on why graphics chips became essential for AI training." },
      { title: "How Recommendation Algorithms Actually Decide What You See", description: "An explainer on how platforms like YouTube and Netflix use AI to personalize your feed." },
      { title: "Are AI Detectors Actually Accurate?", description: "A factual look at how AI-content detection tools work and their real-world reliability." },
      { title: "How Much Energy Does Training an AI Model Actually Use?", description: "A factual breakdown of the real computing and energy cost behind large AI models." },
      { title: "What Is Prompt Injection and Why Should You Care?", description: "An explainer on a real security risk in AI systems, in plain language." },
      { title: "How AI Is Changing Software Development", description: "A look at how AI coding assistants are changing what it means to be a programmer." },
    ];

    if (!fresh) {
      const freshCurated = CURATED_AI_TOPICS.find((t) => !usedSet.has(normalize(t.title)));
      fresh = freshCurated || CURATED_AI_TOPICS[0]; // last resort: reuse is better than failing the whole run
    }

    const chosenTopic = fresh;

    // ---------------------------------------------------------------------
    // STEP 2: generate the faceless-explainer script + SEO/AEO/GEO metadata
    // ---------------------------------------------------------------------
    const prompt = `You are writing a FACELESS EXPLAINER YouTube video script (voice-over only, no
on-camera host, paired with stock footage/B-roll and text-on-screen). The video is about:

TOPIC: ${chosenTopic.title}
CONTEXT: ${chosenTopic.description || "No extra context — research from your own knowledge and produce an accurate, well-structured explainer."}

Return ONLY valid JSON, no markdown fences, no commentary, matching EXACTLY this shape:
{
  "video_title": "...",
  "hook_line": "...",
  "script": [
    {"scene": 1, "voiceover": "...", "visual_note": "...", "on_screen_text": "..."},
    {"scene": 2, "voiceover": "...", "visual_note": "...", "on_screen_text": "..."}
  ],
  "cta_line": "...",
  "description": "...",
  "tags": ["...", "..."],
  "chapters": [{"time": "0:00", "label": "..."}, {"time": "0:20", "label": "..."}],
  "aeo_qa_block": [{"q": "...", "a": "..."}, {"q": "...", "a": "..."}, {"q": "...", "a": "..."}],
  "thumbnail_text_ideas": ["...", "...", "..."]
}

STRICT RULES:
- LANGUAGE LEVEL: write in SIMPLE, CLEAR English — short sentences, common everyday words,
  no complex vocabulary or idioms. Target reading/listening level: someone who learned English
  as a second language should follow it easily on first listen, same as a native speaker would.
  This does NOT mean dumbing down the information — the facts and depth stay full quality, only
  the sentence complexity and word choice get simpler. Avoid words like "utilize", "leverage",
  "paradigm", "myriad" — use "use", "many", "way" instead.
- QUALITY FLOOR (non-negotiable): every fact must be accurate and traceable to the given topic/
  context. No filler sentences. No two scenes repeating the same point in different words. If a
  scene doesn't add new information, cut it rather than padding runtime.
- "script": 5-6 scenes by default (never fewer than 5). Only go up to 7-8 scenes
  if the topic genuinely needs that many distinct visual beats (each scene will
  use either a video clip OR a still image as its visual, so more scenes means
  more variety, not padding) — 5-6 is the target, 7-8 is the exception, not the norm.
  ~500-750 words of voiceover TOTAL across however many scenes (roughly 3.5-5
  minutes spoken).
  Each scene's voiceover is 3-6 sentences, written for natural TEXT-TO-SPEECH delivery: no
  stage directions inside the voiceover text itself, no emoji, plain spoken sentences.
- "visual_note": a short concrete instruction for what stock footage/B-roll to show
  (e.g. "wide shot of a stock exchange floor, fast cuts of tickers"), not vague ("something related").
- "on_screen_text": short punchy on-screen caption/keyword for that scene (3-6 words), or "" if none needed.
- "video_title": under 70 characters (YouTube truncates longer titles in search/suggested results).
  Put the PRIMARY keyword as close to the FRONT of the title as possible (YouTube's search and
  "AIO" — its AI-driven recommendation system that decides what to surface for a query — both
  weight early keyword placement heavily). No clickbait ALL CAPS spam, no misleading claims —
  YouTube actively suppresses reach on titles that don't match the actual video content.
- "description": 200-350 words (longer, keyword-rich descriptions rank better on YouTube than short
  ones). Structure it for FOUR things at once:
    (1) SEO — the first 2-3 sentences (the part visible before "Show more") must naturally include
        the primary keyword AND 1-2 close variants, since this snippet is what YouTube search and
        Google search both index most heavily.
    (2) AIO (YouTube's own AI ranking/recommendation system) — write naturally but keyword-dense
        throughout, not just the opening; YouTube's system matches full description content against
        both the video's actual spoken transcript and search queries, so keyword variety beyond the
        title genuinely helps discovery.
    (3) AEO — include a clearly-labeled "Key facts:" bullet list of 3-5 concrete, citable facts,
        written so an answer engine (ChatGPT, Perplexity, Google AI Overview) could quote them directly.
    (4) GEO — write in a factual, structured, third-person tone (not hype/marketing language) so
        generative engines treat this as a reliable source to cite.
  End the description with: the chapters list inline as plain text lines ("0:00 Intro" etc), then
  3-5 relevant hashtags on their own line (e.g. "#AI #MachineLearning #TechExplained") — YouTube
  surfaces the first 3 hashtags above the title itself, which is free extra discoverability.
- "tags": 12-15 tags (use the full allowance — more relevant tags genuinely help YouTube's
  matching system), ordered from most to least important: 2-3 broad single/two-word tags first
  (the biggest search volume), then long-tail 3-5 word tags, then close variants/misspellings
  people actually search. No hashtags here (tags field is separate from description hashtags),
  no duplicates, no irrelevant tags just for reach — mismatched tags hurt watch-time signals.
- "hook_line": the FIRST 3 seconds of spoken script — this is the single highest-leverage line
  for YouTube's algorithm, since audience-retention in the first few seconds is a major ranking
  signal. Must create curiosity/stakes immediately, no throat-clearing ("Hey guys, welcome back").
- "cta_line": natural subscribe/engagement prompt, not generic ("like and subscribe" alone is banned —
  tie it to the topic, e.g. "Subscribe if you want the next update on this before it breaks anywhere else").
- "chapters": timestamps must be strictly increasing starting at 0:00, one per major script beat,
  spacing estimated from voiceover length (roughly 150 spoken words per minute).
- "aeo_qa_block": exactly 3-5 question/answer pairs a viewer might ask an AI assistant about this
  topic — answers must be self-contained (understandable without watching the video), 1-3 sentences each.
- "thumbnail_text_ideas": 3 short (2-5 word) punchy thumbnail text options, no clickbait lies.
- Everything must be factually grounded in the given topic/context — no fabricated statistics,
  no invented quotes, no invented sources. If context is thin, stay general rather than inventing specifics.
- No AI-filler phrasing anywhere ("in today's fast-paced world", "let's dive in", etc).`;

    async function tryGenerate(model) {
      const aiRes = await fetch("https://openrouter.ai/api/v1/chat/completions", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${OPENROUTER_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model,
          messages: [{ role: "user", content: prompt }],
          temperature: 0.7,
        }),
      });
      console.log(`OpenRouter [${model}] status:`, aiRes.status);
      const aiData = await aiRes.json();
      let rawText = aiData.choices?.[0]?.message?.content || "";
      rawText = extractJSON(rawText.replace(/```json|```/g, "").trim());
      try {
        return JSON.parse(rawText);
      } catch (e) {
        console.log(`OpenRouter [${model}] gave unparseable output:`, rawText.slice(0, 300));
        return null;
      }
    }

    // Same fast-then-fallback model order style as generate-blog.js.
    const MODELS = [
      "anthropic/claude-3.5-sonnet",
      "openai/gpt-4o-mini",
      "meta-llama/llama-3.1-70b-instruct:free",
    ];

    let script = null;
    for (const model of MODELS) {
      script = await tryGenerate(model);
      if (script && script.script && script.script.length) break;
    }

    if (!script) {
      return res.status(500).json({ error: "All models failed to generate a usable script" });
    }

    // ---------------------------------------------------------------------
    // Record this topic as used, so future runs don't repeat it. Previously
    // this write was never checked for success — if it silently failed
    // (stale sha, e.g. from overlapping runs triggered close together), the
    // topic never actually got recorded as used, so the same curated topic
    // kept getting picked run after run. Now: check success, retry once with
    // a freshly-fetched sha if it failed, matching the blog's posts-data.js fix.
    // ---------------------------------------------------------------------
    async function putHistory(list, shaToUse) {
      return fetch(historyApi, {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${GITHUB_TOKEN}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message: `Record used video topic: ${chosenTopic.title}`,
          content: Buffer.from(JSON.stringify(list, null, 2)).toString("base64"),
          sha: shaToUse,
        }),
      });
    }

    if (historyApi && GITHUB_TOKEN) {
      const updated = [chosenTopic.title, ...usedTitles].slice(0, 500); // cap history size
      let putRes = await putHistory(updated, historyFile ? historyFile.sha : undefined);

      if (!putRes.ok) {
        // Likely a stale/missing sha — re-fetch current content and retry once.
        const retryGet = await fetch(historyApi, { headers: { Authorization: `Bearer ${GITHUB_TOKEN}` } });
        if (retryGet.ok) {
          const retryFile = await retryGet.json();
          let freshList = [];
          try {
            freshList = JSON.parse(Buffer.from(retryFile.content, "base64").toString("utf-8"));
          } catch (e) {
            freshList = [];
          }
          const mergedUpdate = [chosenTopic.title, ...freshList].slice(0, 500);
          putRes = await putHistory(mergedUpdate, retryFile.sha);
        }
      }

      if (!putRes.ok) {
        console.error("Failed to record topic history after retry:", await putRes.text());
      }
    }

    return res.status(200).json({
      success: true,
      source_topic: chosenTopic,
      ...script,
    });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: err.message });
  }
}
