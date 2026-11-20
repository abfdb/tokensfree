'use strict';

const KEY = 'shiguang-ai-records-v1';
const DEFAULT_STATUS = '未申请';
const STATUSES = [DEFAULT_STATUS, '已申请', '已启用', '已过期'];
const CATEGORIES = ['全部', ...new Set(RESOURCES.map(({ type }) => type))];
const IDS = new Set(RESOURCES.map(({ id }) => id));
const $ = id => document.getElementById(id);
const search = $('search');
const mine = $('mine');
const reset = $('reset');
const filters = $('filters');
const cards = new Map();
const categoryButtons = new Map();
let records = {};
let category = CATEGORIES[0];

function cleanRecords(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw Error('记录格式不正确');
  }
  const out = {};
  for (const [id, entry] of Object.entries(value)) {
    if (!IDS.has(id) || !entry || typeof entry !== 'object') continue;
    if (!STATUSES.includes(entry.status)) throw Error('存在无效状态');
    const expiry = entry.expiry || '';
    if (expiry && (typeof expiry !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(expiry)
        || Number.isNaN(Date.parse(expiry)) || new Date(expiry).toISOString().slice(0, 10) !== expiry)) {
      throw Error('存在无效日期');
    }
    out[id] = { status: entry.status, expiry };
  }
  return out;
}

function message(text) {
  $('message').textContent = text;
}

try {
  records = cleanRecords(JSON.parse(localStorage.getItem(KEY) || '{}'));
} catch {
  message('无法读取本地记录，当前以空记录展示。可以导入有效备份。');
}

function persist() {
  try {
    localStorage.setItem(KEY, JSON.stringify(records));
    message('已保存到当前浏览器。');
  } catch {
    message('浏览器无法保存，改动只保留在当前页面。请导出备份。');
  }
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function officialLink(text, url, className) {
  const anchor = el('a', text, className);
  const icon = el('span', ' ↗');
  icon.setAttribute('aria-hidden', 'true');
  anchor.append(icon);
  anchor.href = url;
  anchor.target = '_blank';
  anchor.rel = 'noopener noreferrer';
  return anchor;
}

function isRecorded(entry) {
  return Boolean(entry && (entry.status !== DEFAULT_STATUS || entry.expiry));
}

function updateSummary() {
  const values = Object.values(records);
  const deadline = new Date();
  deadline.setDate(deadline.getDate() + 7);
  const end = [
    String(deadline.getFullYear()).padStart(4, '0'),
    String(deadline.getMonth() + 1).padStart(2, '0'),
    String(deadline.getDate()).padStart(2, '0'),
  ].join('-');
  const due = values.filter(({ status, expiry }) => expiry && status !== '已过期' && expiry <= end).length;
  $('tracked').textContent = values.filter(isRecorded).length;
  $('due').textContent = due ? `${due} 项已到期或将在 7 天内到期` : '暂无临近到期记录';
}

function filterCards() {
  const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  let visible = 0;
  for (const [id, { node, type, terms }] of cards) {
    const show = (category === CATEGORIES[0] || type === category)
      && (!mine.checked || isRecorded(records[id]))
      && words.every(word => terms.includes(word));
    if (!show && node.contains(document.activeElement)) mine.focus();
    node.hidden = !show;
    visible += Number(show);
  }
  $('count').textContent = `显示 ${visible} / ${RESOURCES.length} 项资源`;
  $('empty').hidden = visible > 0;
  reset.hidden = !words.length && !mine.checked && category === CATEGORIES[0];
}

function updateRecordControls() {
  for (const [id, { status, expiry, badge }] of cards) {
    const entry = records[id];
    status.value = entry?.status || DEFAULT_STATUS;
    expiry.value = entry?.expiry || '';
    badge.textContent = isRecorded(entry) ? (entry.status === DEFAULT_STATUS ? '已记录' : entry.status) : '';
    badge.hidden = !badge.textContent;
  }
  updateSummary();
  filterCards();
}

function makeCard(resource) {
  const { id, name, type, use, offer, eligibility, risk, checked, url, source } = resource;
  const node = el('article', undefined, 'card');
  const top = el('div', undefined, 'card-top');
  const chips = el('div', undefined, 'chips');
  const tag = el('span', type, 'tag');
  if (type === '付费订阅权益') tag.classList.add('tag-paid');
  chips.append(tag, el('span', use, 'use'));
  top.append(chips, el('h3', name));

  const links = el('div', undefined, 'links');
  links.append(officialLink('前往官网', url, 'primary'), officialLink('规则与来源', source, 'source'));

  const details = el('details', undefined, 'record');
  const toggle = el('summary', '手动记录');
  const badge = el('span', '', 'record-badge');
  toggle.append(badge);

  const controls = el('div', undefined, 'record-fields');
  const statusLabel = el('label', '申请状态');
  const status = el('select');
  status.setAttribute('aria-label', `${name}申请状态`);
  for (const value of STATUSES) {
    const option = el('option', value);
    option.value = value;
    status.append(option);
  }
  statusLabel.append(status);
  const expiryLabel = el('label', '到期日（可选）');
  const expiry = el('input');
  expiry.type = 'date';
  expiry.setAttribute('aria-label', `${name}到期日`);
  expiryLabel.append(expiry);
  controls.append(statusLabel, expiryLabel);
  details.append(toggle, controls);
  node.append(
    top, el('p', offer, 'offer'), el('p', eligibility, 'detail'),
    el('p', `注意 · ${risk}`, 'risk'), links, details, el('p', `资料核验 ${checked}`, 'checked'),
  );
  const save = () => {
    records[id] = { status: status.value, expiry: expiry.value };
    persist();
    updateRecordControls();
  };
  status.addEventListener('change', save);
  expiry.addEventListener('change', save);

  return { node, status, expiry, badge, type, terms: [name, type, use, offer, eligibility, risk].join(' ').toLowerCase() };
}

function selectCategory(value) {
  category = value;
  for (const [type, button] of categoryButtons) {
    button.setAttribute('aria-pressed', String(type === category));
  }
  filterCards();
}

for (const type of CATEGORIES) {
  const button = el('button', type);
  button.type = 'button';
  button.addEventListener('click', () => selectCategory(type));
  categoryButtons.set(type, button);
  filters.append(button);
}
for (const resource of RESOURCES) {
  const card = makeCard(resource);
  cards.set(resource.id, card);
  $('cards').append(card.node);
}
search.addEventListener('input', filterCards);
mine.addEventListener('change', filterCards);
reset.addEventListener('click', () => {
  search.value = '';
  mine.checked = false;
  selectCategory(CATEGORIES[0]);
  search.focus();
});
selectCategory(category);
updateRecordControls();

$('export').addEventListener('click', () => {
  const payload = { version: 1, exportedAt: new Date().toISOString(), records };
  const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }));
  const anchor = el('a');
  anchor.href = url;
  anchor.download = 'shiguang-ai-records.json';
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  message('已导出本地记录。');
});

const fileInput = $('import');
$('import-trigger').addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', async () => {
  const file = fileInput.files[0];
  if (!file) return;
  try {
    if (file.size > 200000) throw Error('文件超过 200 KB 限制');
    const data = JSON.parse(await file.text());
    if (data?.version !== 1) throw Error('不支持此文件版本');
    const incoming = cleanRecords(data.records);
    const size = Object.keys(incoming).length;
    if (!size) throw Error('没有可导入的有效资源记录');
    if (!confirm(`导入 ${size} 项记录？同一资源的记录会被覆盖，其他记录保留。`)) return;
    records = { ...records, ...incoming };
    persist();
    updateRecordControls();
  } catch (error) {
    message(`导入失败：${error instanceof SyntaxError ? '不是有效的 JSON 文件' : error.message}`);
  } finally {
    fileInput.value = '';
  }
});
