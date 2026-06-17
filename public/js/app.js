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

async function pollUntilUpdateDone() {
  for (let i = 0; i < 120; i++) {
    await sleep(2000);
    const res = await fetch('/api/status');
    const data = await res.json();
    const elapsed = data.elapsedSeconds ?? (i + 1) * 2;
    setProgressText(`正在采集50州新闻并翻译… ${elapsed}s / Fetching news…`);

    if (!data.updating) {
      if (data.updateError && !data.updateError.includes('timed out')) {
        throw new Error(data.updateError);
      }
      return;
    }
  }
  throw new Error('更新超时，请重试 · Update timed out, please retry');
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

async function updateBriefing() {
  updateBtn.disabled = true;
  progressBar.classList.remove('hidden');
  setProgressText('正在启动更新… Starting update…');

  try {
    const res = await fetch('/api/briefing/update', { method: 'POST' });
    const data = await res.json();

    if (res.status !== 202 && !res.ok) {
      alert(data.error || data.message || 'Update failed');
      return;
    }

    await pollUntilUpdateDone();
    await loadBriefing();
  } catch (err) {
    alert(err.message || '网络错误，请重试 · Network error, please retry');
    await loadBriefing();
  } finally {
    updateBtn.disabled = false;
    progressBar.classList.add('hidden');
    setProgressText('正在从50个州采集新闻并翻译… Fetching & translating…');
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
loadBriefing();
