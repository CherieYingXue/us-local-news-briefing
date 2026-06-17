let currentFilter = 'all';
let briefingData = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const updateBtn = $('#updateBtn');
const lastUpdated = $('#lastUpdated');
const stateCount = $('#stateCount');
const progressBar = $('#progressBar');
const emptyState = $('#emptyState');
const statesGrid = $('#statesGrid');
const storyModal = $('#storyModal');
const mobileLink = $('#mobileLink');

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('zh-CN', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit'
  });
}

function formatBriefingDate(iso) {
  if (!iso) return '尚未更新 · Not updated yet';
  const d = new Date(iso);
  return `上次更新 · Updated: ${d.toLocaleString('zh-CN', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit'
  })}`;
}

async function loadStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    const link = (location.hostname !== 'localhost' && location.hostname !== '127.0.0.1')
      ? location.origin
      : (data.mobileUrl || location.origin);
    mobileLink.innerHTML = `📱 手机每日访问链接 · Daily phone link:<br><a href="${link}">${link}</a>`;
  } catch (_) {}
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function setProgressText(text) {
  const el = document.querySelector('.progress-text');
  if (el) el.textContent = text;
}

async function fetchStatus(retries = 3) {
  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      const res = await fetch('/api/status');
      if (res.ok) return await res.json();
    } catch (_) {}
    if (attempt < retries - 1) await sleep(1500);
  }
  return null;
}

async function pollUntilUpdateDone() {
  let consecutiveFailures = 0;
  let lastStates = briefingData?.statesWithNews || 0;

  for (let i = 0; i < 120; i++) {
    await sleep(2000);
    const data = await fetchStatus(5);
    if (!data) {
      consecutiveFailures++;
      if (consecutiveFailures >= 20) {
        throw new Error('服务器暂时无响应，请稍后重试 · Server unavailable, please retry');
      }
      setProgressText(`连接中断，重试中… (${consecutiveFailures}) / Retrying connection…`);
      continue;
    }

    consecutiveFailures = 0;
    const elapsed = data.elapsedSeconds ?? (i + 1) * 2;
    const states = data.statesWithNews ?? 0;
    const total = data.totalStates ?? 50;
    setProgressText(
      `正在采集 ${states}/${total} 州… ${elapsed}s / Fetching ${states}/${total} states…`
    );

    if (states > lastStates) {
      lastStates = states;
      await loadBriefing();
    }

    if (!data.updating) {
      if (data.updateError && !data.updateError.includes('timed out')) {
        throw new Error(data.updateError);
      }
      return;
    }
  }
  throw new Error('更新超时，请重试 · Update timed out, please retry');
}

async function startUpdateRequest() {
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      const res = await fetch('/api/briefing/update', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (res.status === 202 || res.ok) return data;
    } catch (_) {}
    if (attempt < 4) {
      setProgressText(`正在连接服务器… (${attempt + 1}/5) / Connecting…`);
      await sleep(3000);
    }
  }
  throw new Error('无法连接服务器，请稍后重试 · Could not reach server, please retry');
}

async function resumeUpdateIfNeeded() {
  const data = await fetchStatus();
  if (!data?.updating) return;

  updateBtn.disabled = true;
  progressBar.classList.remove('hidden');
  setProgressText('检测到更新进行中… Update in progress…');

  try {
    await pollUntilUpdateDone();
    await loadBriefing();
    await translateMissingTitles();
  } catch (err) {
    alert(err.message || '网络错误，请重试 · Network error, please retry');
    await loadBriefing();
  } finally {
    updateBtn.disabled = false;
    progressBar.classList.add('hidden');
    setProgressText('正在从50个州采集新闻… Fetching news…');
  }
}

async function loadBriefing() {
  try {
    const res = await fetch('/api/briefing');
    briefingData = await res.json();
    if (briefingData.states && briefingData.states.length > 0) {
      renderBriefing();
    }
    lastUpdated.textContent = formatBriefingDate(briefingData.updatedAt);
    if (briefingData.statesWithNews) {
      stateCount.textContent = `${briefingData.statesWithNews}/${briefingData.totalStates || 50} 州有新闻`;
    }
  } catch (err) {
    console.error(err);
  }
}

function renderBriefing() {
  if (!briefingData?.states?.length) {
    emptyState.classList.remove('hidden');
    statesGrid.classList.add('hidden');
    return;
  }

  emptyState.classList.add('hidden');
  statesGrid.classList.remove('hidden');

  statesGrid.innerHTML = briefingData.states.map(state => {
    const filteredStories = currentFilter === 'all'
      ? state.stories
      : state.stories.filter(s => s.category === currentFilter);

    if (filteredStories.length === 0 && currentFilter !== 'all') return '';

    const storiesHtml = filteredStories.length > 0
      ? filteredStories.map(story => `
          <li class="story-item" data-id="${story.id}">
            <div class="story-top">
              <a class="story-title" href="#" data-id="${story.id}">${escapeHtml(story.title)}</a>
              <span class="cat-badge cat-${story.category}">${story.categoryLabel?.zh || story.category}</span>
            </div>
            ${story.titleZh && story.titleZh !== story.title ? `<p class="story-title-zh">${escapeHtml(story.titleZh)}</p>` : ''}
            <div class="story-meta">
              <span>${story.source}</span>
              <span>${formatDate(story.pubDate)}</span>
            </div>
          </li>
        `).join('')
      : `<li class="no-stories">暂无24小时内新闻 · No stories in past 24h</li>`;

    const errorHtml = state.error
      ? `<div class="state-error">⚠ 获取失败: ${escapeHtml(state.error)}</div>`
      : '';

    return `
      <article class="state-card" data-state="${state.code}">
        <div class="state-header">
          <div>
            <div class="state-name">${state.name}</div>
            <div class="state-source">${state.source}</div>
          </div>
          <span class="state-code">${state.code}</span>
        </div>
        ${errorHtml}
        <ul class="story-list">${storiesHtml}</ul>
      </article>
    `;
  }).join('');

  attachStoryListeners();
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function attachStoryListeners() {
  $$('.story-item, .story-title').forEach(el => {
    el.addEventListener('click', (e) => {
      e.preventDefault();
      const id = el.dataset.id || el.closest('.story-item')?.dataset.id;
      if (id) openStory(id);
    });
  });
}

async function openStory(id) {
  try {
    const res = await fetch(`/api/story/${id}`);
    if (!res.ok) return;
    const story = await res.json();

    $('#modalState').textContent = `${story.state} (${story.stateCode})`;
    $('#modalCategory').textContent = `${story.categoryLabel?.zh || ''} · ${story.categoryLabel?.en || story.category}`;
    $('#modalDate').textContent = formatDate(story.pubDate);
    $('#modalTitle').textContent = story.title;
    $('#modalTitleZh').textContent =
      story.titleZh && story.titleZh !== story.title ? story.titleZh : '';
    $('#modalBody').textContent = story.description || '暂无摘要 · No summary available';
    $('#modalLink').href = story.link;

    storyModal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
  } catch (err) {
    console.error(err);
  }
}

function closeModal() {
  storyModal.classList.add('hidden');
  document.body.style.overflow = '';
}

async function translateMissingTitles() {
  setProgressText('正在翻译标题… Translating titles…');
  for (let round = 0; round < 20; round++) {
    const res = await fetch('/api/briefing/translate', { method: 'POST' });
    if (!res.ok) break;
    const data = await res.json();
    if (data.briefing) {
      briefingData = data.briefing;
      renderBriefing();
      lastUpdated.textContent = formatBriefingDate(briefingData.updatedAt);
    }
    setProgressText(`翻译中… ${data.remaining ?? 0} 条剩余 / ${data.remaining ?? 0} remaining`);
    if (!data.remaining || data.translated === 0) break;
    await sleep(500);
  }
}

async function updateBriefing() {
  updateBtn.disabled = true;
  progressBar.classList.remove('hidden');
  setProgressText('正在启动更新… Starting update…');

  try {
    await startUpdateRequest();
    await pollUntilUpdateDone();
    await loadBriefing();
    await translateMissingTitles();
  } catch (err) {
    alert(err.message || '网络错误，请重试 · Network error, please retry');
    await loadBriefing();
  } finally {
    updateBtn.disabled = false;
    progressBar.classList.add('hidden');
    setProgressText('正在从50个州采集新闻… Fetching news…');
  }
}

updateBtn.addEventListener('click', updateBriefing);

$$('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    $$('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentFilter = btn.dataset.filter;
    renderBriefing();
  });
});

$('.modal-close').addEventListener('click', closeModal);
$('.modal-backdrop').addEventListener('click', closeModal);

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeModal();
});

loadStatus();
loadBriefing()
  .then(() => resumeUpdateIfNeeded())
  .then(() => translateMissingTitles().catch(() => {}));
