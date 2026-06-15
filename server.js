const express = require('express');
const path = require('path');
const fs = require('fs');
const os = require('os');
const Parser = require('rss-parser');

const app = express();
const PORT = process.env.PORT || 3847;
const CACHE_FILE = path.join(__dirname, 'cache', 'briefing.json');
const STATES = JSON.parse(fs.readFileSync(path.join(__dirname, 'data', 'states.json'), 'utf8'));

const parser = new Parser({
  timeout: 15000,
  headers: { 'User-Agent': 'US-Local-News-Briefing/1.0' }
});

const CATEGORY_KEYWORDS = {
  political: [
    'politic', 'election', 'governor', 'legisl', 'congress', 'senate', 'house',
    'mayor', 'vote', 'ballot', 'democrat', 'republican', 'policy', 'capitol',
    'lawmaker', 'bill', ' veto', 'campaign', 'primary', 'caucus', 'immigration',
    'supreme court', 'attorney general', 'secretary of state', 'regulation'
  ],
  economic: [
    'econom', 'business', 'job', 'market', 'inflation', 'housing', 'trade',
    'finance', 'budget', 'tax', 'wage', 'unemployment', 'gdp', 'bank',
    'investment', 'startup funding', 'real estate', 'cost of living', 'tariff',
    'minimum wage', 'workforce', 'industry', 'agriculture', 'energy price'
  ],
  social: [
    'community', 'education', 'school', 'health', 'hospital', 'crime',
    'police', 'fire', 'weather', 'disaster', 'flood', 'wildfire', 'housing crisis',
    'homeless', 'family', 'child', 'university', 'public safety', 'court',
    'prison', 'mental health', 'environment', 'water', 'climate'
  ],
  tech: [
    'tech', 'technology', 'digital', 'cyber', ' ai ', 'artificial intelligence',
    'software', 'data privacy', 'internet', 'broadband', 'semiconductor',
    'innovation', 'startup', 'robot', 'electric vehicle', 'renewable energy tech',
    'social media', 'blockchain', 'automation'
  ]
};

const CATEGORY_LABELS = {
  political: { en: 'Political', zh: '政治' },
  economic: { en: 'Economic', zh: '经济' },
  social: { en: 'Social', zh: '社会' },
  tech: { en: 'Tech', zh: '科技' }
};

let isUpdating = false;

function stripHtml(html) {
  if (!html) return '';
  return html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#039;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

function categorize(text) {
  const lower = ` ${text.toLowerCase()} `;
  const scores = {};
  for (const [cat, keywords] of Object.entries(CATEGORY_KEYWORDS)) {
    scores[cat] = keywords.reduce((sum, kw) => sum + (lower.includes(kw) ? 1 : 0), 0);
  }
  const best = Object.entries(scores).sort((a, b) => b[1] - a[1])[0];
  return best[1] > 0 ? best[0] : 'social';
}

function isWithin24Hours(dateStr) {
  if (!dateStr) return true;
  const date = new Date(dateStr);
  if (Number.isNaN(date.getTime())) return true;
  const hours = (Date.now() - date.getTime()) / (1000 * 60 * 60);
  return hours <= 24;
}

async function translateToChinese(text) {
  if (!text || text.length < 2) return '';
  const trimmed = text.slice(0, 450);
  try {
    const url = `https://api.mymemory.translated.net/get?q=${encodeURIComponent(trimmed)}&langpair=en|zh-CN`;
    const res = await fetch(url);
    const data = await res.json();
    if (data.responseStatus === 200 && data.responseData?.translatedText) {
      return data.responseData.translatedText;
    }
  } catch (_) {}
  return '';
}

async function translateBatch(texts) {
  const results = [];
  for (const text of texts) {
    results.push(await translateToChinese(text));
    await new Promise(r => setTimeout(r, 350));
  }
  return results;
}

async function fetchStateNews(state) {
  try {
    const feed = await parser.parseURL(state.feed);
    const items = (feed.items || [])
      .filter(item => isWithin24Hours(item.isoDate || item.pubDate))
      .slice(0, 8)
      .map(item => {
        const title = item.title || 'Untitled';
        const description = stripHtml(item.contentSnippet || item.content || item.summary || '');
        const category = categorize(`${title} ${description}`);
        return {
          id: Buffer.from(`${state.code}-${item.link || title}`).toString('base64url'),
          title,
          titleZh: '',
          description: description.slice(0, 500),
          descriptionZh: '',
          link: item.link || '',
          pubDate: item.isoDate || item.pubDate || new Date().toISOString(),
          category,
          categoryLabel: CATEGORY_LABELS[category],
          source: state.source
        };
      });

    if (items.length === 0 && feed.items?.length) {
      const fallback = feed.items.slice(0, 3).map(item => {
        const title = item.title || 'Untitled';
        const description = stripHtml(item.contentSnippet || item.content || item.summary || '');
        const category = categorize(`${title} ${description}`);
        return {
          id: Buffer.from(`${state.code}-${item.link || title}`).toString('base64url'),
          title,
          titleZh: '',
          description: description.slice(0, 500),
          descriptionZh: '',
          link: item.link || '',
          pubDate: item.isoDate || item.pubDate || new Date().toISOString(),
          category,
          categoryLabel: CATEGORY_LABELS[category],
          source: state.source
        };
      });
      return { state, stories: fallback, error: null };
    }

    return { state, stories: items.slice(0, 5), error: null };
  } catch (err) {
    return { state, stories: [], error: err.message };
  }
}

async function addTranslations(briefing) {
  const allStories = briefing.states.flatMap(s => s.stories);
  const titles = allStories.map(s => s.title);
  const titleTranslations = await translateBatch(titles);

  let idx = 0;
  for (const stateData of briefing.states) {
    for (const story of stateData.stories) {
      story.titleZh = titleTranslations[idx] || story.title;
      idx++;
    }
  }
  return briefing;
}

async function buildBriefing() {
  const batchSize = 10;
  const results = [];

  for (let i = 0; i < STATES.length; i += batchSize) {
    const batch = STATES.slice(i, i + batchSize);
    const batchResults = await Promise.all(batch.map(fetchStateNews));
    results.push(...batchResults);
  }

  const states = results.map(({ state, stories, error }) => ({
    code: state.code,
    name: state.name,
    source: state.source,
    feed: state.feed,
    stories,
    error
  }));

  let briefing = {
    updatedAt: new Date().toISOString(),
    totalStates: STATES.length,
    statesWithNews: states.filter(s => s.stories.length > 0).length,
    states
  };

  briefing = await addTranslations(briefing);
  return briefing;
}

function readCache() {
  try {
    if (fs.existsSync(CACHE_FILE)) {
      return JSON.parse(fs.readFileSync(CACHE_FILE, 'utf8'));
    }
  } catch (_) {}
  return null;
}

function writeCache(briefing) {
  const dir = path.dirname(CACHE_FILE);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(CACHE_FILE, JSON.stringify(briefing, null, 2), 'utf8');
}

function getLocalIP() {
  const nets = os.networkInterfaces();
  for (const name of Object.keys(nets)) {
    for (const net of nets[name]) {
      if (net.family === 'IPv4' && !net.internal) {
        return net.address;
      }
    }
  }
  return 'localhost';
}

app.use(express.static(path.join(__dirname, 'public')));
app.use(express.json());

app.get('/api/briefing', (req, res) => {
  const cached = readCache();
  if (cached) {
    return res.json({ ...cached, fromCache: true, updating: isUpdating });
  }
  res.json({
    updatedAt: null,
    states: [],
    message: 'No briefing yet. Click "Update Daily Briefing" to fetch news.',
    updating: isUpdating
  });
});

app.post('/api/briefing/update', async (req, res) => {
  if (isUpdating) {
    return res.status(409).json({ error: 'Update already in progress', updating: true });
  }

  isUpdating = true;
  try {
    const briefing = await buildBriefing();
    writeCache(briefing);
    res.json({ ...briefing, fromCache: false, updating: false });
  } catch (err) {
    res.status(500).json({ error: err.message, updating: false });
  } finally {
    isUpdating = false;
  }
});

app.get('/api/status', (req, res) => {
  const cached = readCache();
  res.json({
    updating: isUpdating,
    lastUpdated: cached?.updatedAt || null,
    statesWithNews: cached?.statesWithNews || 0,
    totalStates: STATES.length,
    mobileUrl: `http://${getLocalIP()}:${PORT}`,
    localUrl: `http://localhost:${PORT}`
  });
});

app.get('/api/story/:id', (req, res) => {
  const cached = readCache();
  if (!cached) return res.status(404).json({ error: 'No briefing loaded' });

  for (const state of cached.states) {
    const story = state.stories.find(s => s.id === req.params.id);
    if (story) {
      return res.json({ ...story, state: state.name, stateCode: state.code });
    }
  }
  res.status(404).json({ error: 'Story not found' });
});

app.listen(PORT, '0.0.0.0', () => {
  const ip = getLocalIP();
  console.log('');
  console.log('  US Local News Daily Briefing');
  console.log('  =============================');
  console.log(`  Computer:  http://localhost:${PORT}`);
  console.log(`  Phone:     http://${ip}:${PORT}`);
  console.log('');
  console.log('  Add the phone link to your home screen for daily access.');
  console.log('');
});
