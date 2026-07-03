(function () {
  'use strict';

  const SAVED_KEY = 'sclaw_support_saved_cases_v1';
  const SELECTED_KEY = 'sclaw_support_selected_cases_v1';
  const SELECTED_CHANNEL = 'sclaw_support_selected_cases_channel_v1';
  const SAVED_CHANNEL = 'sclaw_support_saved_cases_channel_v1';
  const FILTER_PREF_KEY = 'sclaw_saved_cases_filter_prefs_v1';
  const PHONE_KEY = 'sclaw_support_phone_login';
  const LEGACY_PHONE_PROFILE_KEY = 'sclaw_support_phone_profile_v1';
  let selectedSyncChannel = null;
  let savedSyncChannel = null;
  let selectedLocalWritePending = false;
  const FILTER_DEFAULTS = {
    keyword: '',
    region: '',
    source: '',
    type: '',
    priceMin: '',
    priceMax: '',
    layoutMin: '',
    sort: 'saved_desc',
    hasImage: false,
  };

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch] || ch));
  }

  function urlList(value, limit = 8) {
    const out = [];
    const push = (v) => {
      const s = String(v || '').trim();
      if (!s || /^javascript:/i.test(s)) return;
      if (!/^https?:\/\//i.test(s) && !s.startsWith('/')) return;
      if (!out.includes(s)) out.push(s);
    };
    if (Array.isArray(value)) {
      value.forEach(push);
    } else {
      String(value || '').split(/[\n\r\t,]+/).forEach(push);
    }
    return out.slice(0, Math.max(1, Number(limit) || 8));
  }

  function text(value) {
    return String(value == null ? '' : value).trim();
  }

  function currentPhoneBucket() {
    try {
      const phoneRaw = text(localStorage.getItem(PHONE_KEY));
      let phone = phoneRaw;
      if (phoneRaw && /^[{[]/.test(phoneRaw)) {
        try {
          const data = JSON.parse(phoneRaw);
          phone = text(data && (data.phone || data.normalizedPhone || data.phone_e164 || data.mobile));
        } catch (_) {
          phone = phoneRaw;
        }
      }
      if (phone) return `phone:${phone.replace(/[^\d+]/g, '') || phone}`;
      const raw = localStorage.getItem(LEGACY_PHONE_PROFILE_KEY) || '';
      if (raw) {
        const data = JSON.parse(raw);
        const legacyPhone = text(data && (data.phone || data.normalizedPhone));
        if (legacyPhone) return `phone:${legacyPhone.replace(/[^\d+]/g, '') || legacyPhone}`;
      }
    } catch (_) {}
    return 'guest';
  }

  function filterPrefsKey() {
    return `${FILTER_PREF_KEY}:${currentPhoneBucket()}`;
  }

  function readFilterPrefs() {
    try {
      const raw = JSON.parse(localStorage.getItem(filterPrefsKey()) || '{}');
      return { ...FILTER_DEFAULTS, ...(raw && typeof raw === 'object' ? raw : {}) };
    } catch (_) {
      return { ...FILTER_DEFAULTS };
    }
  }

  function writeFilterPrefs(prefs) {
    try {
      localStorage.setItem(filterPrefsKey(), JSON.stringify({ ...FILTER_DEFAULTS, ...(prefs || {}) }));
    } catch (_) {}
  }

  function readNumber(value) {
    const n = Number(String(value || '').replace(/,/g, '').trim());
    return Number.isFinite(n) ? n : 0;
  }

  function priceMan(value) {
    const raw = String(value || '').replace(/,/g, '').trim();
    if (!raw) return 0;
    const man = raw.match(/([0-9]+(?:\.[0-9]+)?)\s*万/);
    if (man) return Number(man[1]) || 0;
    const oku = raw.match(/([0-9]+(?:\.[0-9]+)?)\s*億/);
    if (oku) return (Number(oku[1]) || 0) * 10000;
    const yen = raw.match(/([0-9]+(?:\.[0-9]+)?)\s*日/);
    if (yen) return (Number(yen[1]) || 0) / 10000;
    return Number(raw.match(/[0-9]+(?:\.[0-9]+)?/)?.[0] || 0) || 0;
  }

  function layoutCount(value) {
    const raw = String(value || '').toUpperCase();
    const nums = [...raw.matchAll(/([0-9]{1,2})\s*(?:LDK|DK|K|房|室)/g)].map((m) => Number(m[1]));
    return nums.filter(Number.isFinite)[0] || 0;
  }

  function timeValue(value) {
    const raw = String(value || '').trim();
    if (!raw) return 0;
    const t = Date.parse(raw.replace(/\./g, '-').replace(/\//g, '-'));
    return Number.isFinite(t) ? t : 0;
  }

  function searchableText(it) {
    return [
      it.title,
      it.source_name,
      it.region,
      it.transit,
      it.address_hint_zh,
      it.building_type_zh,
      it.layout_text_hant,
      it.area_text_hant,
      it.price_text_hant,
      it.feature_tags_hant,
    ].flat().filter(Boolean).join(' ').toLowerCase();
  }

  function caseKeyFromItem(it) {
    const sid = Number(it && it.source_item_id);
    if (sid > 0) return `sid:${sid}`;
    const cid = Number(it && it.content_id);
    if (cid > 0) return `cid:${cid}`;
    const url = String((it && it.item_url) || '').trim();
    if (url) return `url:${url}`;
    const title = String((it && (it.title || it.title_zh_hant || it.title_original)) || '').trim();
    return title ? `title:${title}` : '';
  }

  function normalize(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const title = String(raw.title || raw.title_zh_hant || raw.title_original || '').trim();
    const itemUrl = String(raw.item_url || '').trim();
    const sid = Number(raw.source_item_id || 0);
    if (!title && !itemUrl && sid <= 0) return null;
    return {
      case_key: String(raw.case_key || caseKeyFromItem(raw)).trim(),
      source_item_id: sid,
      content_id: Number(raw.content_id || 0),
      title: title || '日本不動產案件',
      source_name: String(raw.source_name || '日本房產').trim(),
      item_url: itemUrl,
      article_url: String(raw.article_url || '').trim(),
      region: String(raw.region || raw.jp_region_display_zh || '').trim(),
      transit: String(raw.transit || raw.transit_line_zh || '').trim(),
      address_hint_zh: String(raw.address_hint_zh || raw.address || '').trim(),
      price_text_hant: String(raw.price_text_hant || raw.price_fx_hant || raw.price_hint || raw.price_text || raw.price_label || raw.price || raw.rent_text || raw.monthly_rent || '').trim(),
      layout_text_hant: String(raw.layout_text_hant || raw.layout_text || '').trim(),
      area_text_hant: String(raw.area_text_hant || raw.area_text || '').trim(),
      exclusive_area_jp: String(raw.exclusive_area_jp || '').trim(),
      age_text_hant: String(raw.age_text_hant || '').trim(),
      case_time_at: String(raw.case_time_at || '').trim(),
      data_time_at: String(raw.data_time_at || '').trim(),
      sort_time_at: String(raw.sort_time_at || '').trim(),
      source_listing_time_jp: String(raw.source_listing_time_jp || '').trim(),
      case_time_label_hant: String(raw.case_time_label_hant || '').trim(),
      published_at: String(raw.published_at || '').trim(),
      crawled_at: String(raw.crawled_at || '').trim(),
      last_checked_at: String(raw.last_checked_at || '').trim(),
      building_type_zh: String(raw.building_type_zh || raw.transaction_label_zh || '').trim(),
      transaction_label_zh: String(raw.transaction_label_zh || '').trim(),
      feature_tags_hant: Array.isArray(raw.feature_tags_hant) ? raw.feature_tags_hant.map(text).filter(Boolean) : String(raw.feature_tags_hant || '').split(/[,，、\n\r]+/).map(text).filter(Boolean),
      thumb_url: String(raw.thumb_url || '').trim(),
      gallery_urls: urlList(raw.gallery_urls || raw.image_urls || [], 8),
      image_count: Number(raw.image_count || 0),
      updated_at: String(raw.updated_at || raw.last_checked_at || raw.crawled_at || '').trim(),
      saved_at: String(raw.saved_at || raw.added_at || raw.updated_at || raw.last_checked_at || raw.crawled_at || '').trim(),
      price_man: priceMan(raw.price_text_hant || raw.price_fx_hant || raw.price_hint || raw.price_text || raw.price_label || raw.price || raw.rent_text || raw.monthly_rent),
      layout_count: layoutCount(raw.layout_text_hant || raw.layout_text || ''),
      sort_time: timeValue(raw.saved_at || raw.added_at || raw.updated_at || raw.last_checked_at || raw.crawled_at),
    };
  }

  function stableKey(it) {
    return String((it && it.case_key) || caseKeyFromItem(it)).trim();
  }

  function loadSavedCases() {
    try {
      const arr = JSON.parse(localStorage.getItem(SAVED_KEY) || '[]');
      if (!Array.isArray(arr)) return [];
      const seen = new Set();
      const out = [];
      for (const raw of arr) {
        const item = normalize(raw);
        if (!item) continue;
        const key = stableKey(item);
        if (key && seen.has(key)) continue;
        if (key) seen.add(key);
        out.push(item);
      }
      return out.slice(-60).reverse();
    } catch (_) {
      return [];
    }
  }

  function saveSavedCases(items) {
    const payload = (Array.isArray(items) ? items : []).map(normalize).filter(Boolean);
    let oldValue = '';
    let newValue = '';
    try {
      oldValue = localStorage.getItem(SAVED_KEY) || '';
      newValue = JSON.stringify(payload.slice().reverse());
      localStorage.setItem(SAVED_KEY, newValue);
    } catch (_) {}
    try {
      window.dispatchEvent(new CustomEvent('sclaw:saved-cases-updated', { detail: { items: payload, source: 'saved-cases' } }));
    } catch (_) {}
    try {
      window.dispatchEvent(new StorageEvent('storage', { key: SAVED_KEY, oldValue, newValue, storageArea: localStorage }));
    } catch (_) {}
    try {
      if (!savedSyncChannel && 'BroadcastChannel' in window) savedSyncChannel = new BroadcastChannel(SAVED_CHANNEL);
      if (savedSyncChannel) savedSyncChannel.postMessage({ type: 'saved-cases-updated', source: 'saved-cases', items: payload });
    } catch (_) {}
  }

  function loadSelectedCases() {
    try {
      const arr = JSON.parse(localStorage.getItem(SELECTED_KEY) || '[]');
      return Array.isArray(arr) ? arr : [];
    } catch (_) {
      return [];
    }
  }

  function saveSelectedCases(items) {
    const payload = (Array.isArray(items) ? items : []).map(normalize).filter(Boolean).slice(0, 20);
    let oldValue = '';
    let newValue = '';
    try {
      oldValue = localStorage.getItem(SELECTED_KEY) || '';
      newValue = JSON.stringify(payload);
      localStorage.setItem(SELECTED_KEY, newValue);
    } catch (_) {}
    try {
      window.dispatchEvent(new CustomEvent('sclaw:selected-cases-updated', { detail: { items: payload, source: 'saved-cases' } }));
    } catch (_) {}
    try {
      selectedLocalWritePending = true;
      window.dispatchEvent(new StorageEvent('storage', { key: SELECTED_KEY, oldValue, newValue, storageArea: localStorage }));
    } catch (_) {}
    window.setTimeout(() => {
      selectedLocalWritePending = false;
    }, 0);
    try {
      if (!selectedSyncChannel && 'BroadcastChannel' in window) selectedSyncChannel = new BroadcastChannel(SELECTED_CHANNEL);
      if (selectedSyncChannel) selectedSyncChannel.postMessage({ type: 'selected-cases-updated', source: 'saved-cases', items: payload });
    } catch (_) {}
  }

  function bindSelectedCaseSync() {
    window.addEventListener('storage', (ev) => {
      if (ev && ev.key && ev.key !== SELECTED_KEY) return;
      if (selectedLocalWritePending) return;
      refreshCompareButtonStates();
    });
    window.addEventListener('sclaw:selected-cases-updated', (ev) => {
      if (ev && ev.detail && ev.detail.source === 'saved-cases') return;
      refreshCompareButtonStates();
    });
    try {
      if (!selectedSyncChannel && 'BroadcastChannel' in window) selectedSyncChannel = new BroadcastChannel(SELECTED_CHANNEL);
      if (selectedSyncChannel) {
        selectedSyncChannel.addEventListener('message', (ev) => {
          if (ev && ev.data && ev.data.type === 'selected-cases-updated' && ev.data.source !== 'saved-cases') {
            refreshCompareButtonStates();
          }
        });
      }
    } catch (_) {}
  }

  function bindSavedCaseSync() {
    window.addEventListener('storage', (ev) => {
      if (ev && ev.key && ev.key !== SAVED_KEY) return;
      renderSavedCasesPage();
    });
    window.addEventListener('sclaw:saved-cases-updated', (ev) => {
      if (ev && ev.detail && ev.detail.source === 'saved-cases') return;
      renderSavedCasesPage();
    });
    window.addEventListener('focus', () => {
      renderSavedCasesPage();
    });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) renderSavedCasesPage();
    });
    try {
      if (!savedSyncChannel && 'BroadcastChannel' in window) savedSyncChannel = new BroadcastChannel(SAVED_CHANNEL);
      if (savedSyncChannel) {
        savedSyncChannel.addEventListener('message', (ev) => {
          if (ev && ev.data && ev.data.type === 'saved-cases-updated' && ev.data.source !== 'saved-cases') {
            renderSavedCasesPage();
          }
        });
      }
    } catch (_) {}
  }

  function markCompareButton(btn, exists) {
    if (!btn) return;
    btn.disabled = true;
    btn.classList.add('is-added');
    btn.textContent = exists ? '已在對比' : '已加入對比';
    btn.setAttribute('aria-live', 'polite');
  }

  function refreshCompareButtonStates() {
    const selectedKeys = new Set(loadSelectedCases().map((it) => String(stableKey(normalize(it) || it)).toLowerCase()).filter(Boolean));
    document.querySelectorAll('[data-add-compare-key]').forEach((btn) => {
      const key = String(btn.getAttribute('data-add-compare-key') || '').toLowerCase();
      const on = key && selectedKeys.has(key);
      btn.disabled = !!on;
      btn.classList.toggle('is-added', !!on);
      btn.textContent = on ? '已加入對比' : '加入對比';
      btn.setAttribute('aria-live', 'polite');
    });
  }

  function addSavedCaseToCompare(key, btn) {
    const item = loadSavedCases().find((it) => stableKey(it) === key);
    if (!item) return;
    const selected = loadSelectedCases();
    const nextKey = String(stableKey(item)).toLowerCase();
    const exists = selected.some((it) => String(stableKey(normalize(it) || it)).toLowerCase() === nextKey);
    if (!exists) selected.unshift({ ...item, added_at: new Date().toISOString() });
    markCompareButton(btn, exists);
    saveSelectedCases(selected);
    refreshCompareButtonStates();
    const statusEl = document.getElementById('saved-cases-status');
    if (statusEl) statusEl.textContent = exists ? '已在對比清單' : '已加入對比';
  }

  function primaryLink(it) {
    const sid = Number((it && it.source_item_id) || 0);
    if (sid > 0) return `/case/${sid}?return_to=${encodeURIComponent('/saved-cases')}`;
    const article = String((it && it.article_url) || '').trim();
    if (article && !/^javascript:/i.test(article)) return article;
    return String((it && it.item_url) || '').trim();
  }

  function thumbUrl(it) {
    const thumb = String((it && it.thumb_url) || '').trim();
    if (/^https?:\/\//i.test(thumb) || thumb.startsWith('/')) return thumb;
    return urlList(it && (it.gallery_urls || it.image_urls), 8)[0] || '';
  }

  function metaText(it) {
    return [
      [it.layout_text_hant, it.area_text_hant].filter(Boolean).join('｜'),
      [it.region, it.transit].filter(Boolean).join('｜'),
      it.address_hint_zh,
      it.building_type_zh,
    ].filter(Boolean).join(' ｜ ') || it.source_name || '案件資料';
  }

  function getFilterControls() {
    return {
      keyword: document.getElementById('saved-filter-keyword'),
      region: document.getElementById('saved-filter-region'),
      source: document.getElementById('saved-filter-source'),
      type: document.getElementById('saved-filter-type'),
      priceMin: document.getElementById('saved-filter-price-min'),
      priceMax: document.getElementById('saved-filter-price-max'),
      layoutMin: document.getElementById('saved-filter-layout-min'),
      sort: document.getElementById('saved-filter-sort'),
      hasImage: document.getElementById('saved-filter-has-image'),
    };
  }

  function readFiltersFromControls() {
    const els = getFilterControls();
    return {
      keyword: text(els.keyword && els.keyword.value),
      region: text(els.region && els.region.value),
      source: text(els.source && els.source.value),
      type: text(els.type && els.type.value),
      priceMin: text(els.priceMin && els.priceMin.value),
      priceMax: text(els.priceMax && els.priceMax.value),
      layoutMin: text(els.layoutMin && els.layoutMin.value),
      sort: text(els.sort && els.sort.value) || FILTER_DEFAULTS.sort,
      hasImage: !!(els.hasImage && (els.hasImage.checked || els.hasImage.value === '1')),
    };
  }

  function applyFiltersToControls(filters) {
    const els = getFilterControls();
    const f = { ...FILTER_DEFAULTS, ...(filters || {}) };
    if (els.keyword) els.keyword.value = f.keyword || '';
    if (els.region) els.region.value = f.region || '';
    if (els.source) els.source.value = f.source || '';
    if (els.type) els.type.value = f.type || '';
    if (els.priceMin) els.priceMin.value = f.priceMin || '';
    if (els.priceMax) els.priceMax.value = f.priceMax || '';
    if (els.layoutMin) els.layoutMin.value = f.layoutMin || '';
    if (els.sort) els.sort.value = f.sort || FILTER_DEFAULTS.sort;
    if (els.hasImage) {
      if ('checked' in els.hasImage && String(els.hasImage.tagName || '').toUpperCase() === 'INPUT') els.hasImage.checked = !!f.hasImage;
      else els.hasImage.value = f.hasImage ? '1' : '';
    }
  }

  function optionHtml(value) {
    return `<option value="${esc(value)}">${esc(value)}</option>`;
  }

  function fillSelect(select, values, current, emptyLabel) {
    if (!select) return;
    const clean = Array.from(new Set(values.map(text).filter(Boolean))).sort((a, b) => a.localeCompare(b, 'zh-Hant'));
    select.innerHTML = `<option value="">${esc(emptyLabel)}</option>${clean.map(optionHtml).join('')}`;
    select.value = clean.includes(current) ? current : '';
  }

  function updateFilterOptions(items, filters) {
    const els = getFilterControls();
    fillSelect(els.region, items.map((it) => it.region || it.address_hint_zh).filter(Boolean), filters.region, '全部地區');
    fillSelect(els.source, items.map((it) => it.source_name).filter(Boolean), filters.source, '全部來源');
    fillSelect(els.type, items.map((it) => it.building_type_zh || it.transaction_label_zh).filter(Boolean), filters.type, '全部類型');
  }

  function matchFilters(it, filters) {
    const f = { ...FILTER_DEFAULTS, ...(filters || {}) };
    if (f.keyword && !searchableText(it).includes(f.keyword.toLowerCase())) return false;
    if (f.region && ![it.region, it.address_hint_zh].filter(Boolean).join(' ').includes(f.region)) return false;
    if (f.source && it.source_name !== f.source) return false;
    if (f.type && (it.building_type_zh || it.transaction_label_zh) !== f.type) return false;
    const minPrice = readNumber(f.priceMin);
    const maxPrice = readNumber(f.priceMax);
    if (minPrice > 0 && (!it.price_man || it.price_man < minPrice)) return false;
    if (maxPrice > 0 && (!it.price_man || it.price_man > maxPrice)) return false;
    const minLayout = readNumber(f.layoutMin);
    if (minLayout > 0 && (!it.layout_count || it.layout_count < minLayout)) return false;
    if (f.hasImage && !thumbUrl(it)) return false;
    return true;
  }

  function sortItems(items, sortKey) {
    const key = sortKey || FILTER_DEFAULTS.sort;
    const arr = items.slice();
    const byTime = (it) => timeValue(it.saved_at) || it.sort_time || timeValue(it.updated_at);
    arr.sort((a, b) => {
      if (key === 'saved_asc') return byTime(a) - byTime(b);
      if (key === 'case_desc') return timeValue(b.updated_at) - timeValue(a.updated_at);
      if (key === 'case_asc') return timeValue(a.updated_at) - timeValue(b.updated_at);
      if (key === 'price_desc') return (b.price_man || 0) - (a.price_man || 0);
      if (key === 'price_asc') return (a.price_man || 0) - (b.price_man || 0);
      return byTime(b) - byTime(a);
    });
    return arr;
  }

  function removeSavedCase(key) {
    const keep = loadSavedCases().filter((it) => stableKey(it) !== key);
    saveSavedCases(keep);
    renderSavedCasesPage();
  }

  function clearSavedCases() {
    try {
      localStorage.setItem(SAVED_KEY, '[]');
    } catch (_) {}
    renderSavedCasesPage();
  }

  function renderCard(it, selectedKeys) {
    const href = primaryLink(it);
    const safeHref = href && !/^javascript:/i.test(href) ? href : '#';
    const isExternal = /^https?:\/\//i.test(safeHref);
    const target = isExternal ? ' target="_blank" rel="nofollow noopener"' : '';
    const title = esc(it.title || '日本不動產案件');
    const src = esc(it.source_name || '日本房產');
    const thumb = thumbUrl(it);
    const key = esc(stableKey(it));
    const isCompared = selectedKeys && selectedKeys.has(String(stableKey(it)).toLowerCase());
    const imageCount = Number(it.image_count || (Array.isArray(it.gallery_urls) ? it.gallery_urls.length : 0) || 0);
    const thumbInner = thumb
      ? `<img src="${esc(thumb)}" alt="${title}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onload="this.parentNode.classList.add('is-loaded');" onerror="this.style.display='none';this.parentNode.classList.add('is-empty');">`
      : '';
    return `<article class="saved-cases-card">
      <a class="saved-cases-card-media${thumb ? '' : ' is-empty'}" href="${esc(safeHref)}"${target}>
        ${thumbInner}
        <span>${thumb ? '圖片載入中' : '暫無物件縮圖'}</span>
      </a>
      <div class="saved-cases-card-main">
        <div class="saved-cases-card-source">${src}</div>
        <h2><a href="${esc(safeHref)}"${target}>${title}</a></h2>
        <p class="saved-cases-card-meta">${esc(metaText(it))}</p>
        <p class="saved-cases-card-price">${esc(it.price_text_hant || '價格待確認')}</p>
        ${imageCount ? `<p class="saved-cases-card-note">含 ${esc(String(imageCount))} 張原站圖片</p>` : ''}
        <div class="saved-cases-card-actions">
          <button type="button" class="primary" data-add-compare-key="${key}"${isCompared ? ' disabled' : ''}>${isCompared ? '已加入對比' : '加入對比'}</button>
          <button type="button" class="secondary" data-saved-case-key="${key}">移除收藏</button>
        </div>
      </div>
    </article>`;
  }

  function renderSavedCasesPage() {
    const items = loadSavedCases();
    const filters = readFiltersFromControls();
    updateFilterOptions(items, filters);
    applyFiltersToControls(filters);
    const activeFilters = readFiltersFromControls();
    writeFilterPrefs(activeFilters);
    const filteredItems = sortItems(items.filter((it) => matchFilters(it, activeFilters)), activeFilters.sort);
    const countEl = document.getElementById('saved-cases-count');
    const filterCountEl = document.getElementById('saved-cases-filter-count');
    const statusEl = document.getElementById('saved-cases-status');
    const ownerEl = document.getElementById('saved-cases-filter-owner');
    const emptyEl = document.getElementById('saved-cases-empty');
    const listEl = document.getElementById('saved-cases-list');
    if (countEl) countEl.textContent = String(items.length);
    if (filterCountEl) filterCountEl.textContent = String(filteredItems.length);
    if (statusEl) statusEl.textContent = items.length ? (filteredItems.length ? '已載入' : '無符合條件') : '尚未收藏';
    if (ownerEl) ownerEl.textContent = currentPhoneBucket() === 'guest' ? '未登入時使用訪客篩選；號碼登入後會記住個人習慣' : '已依目前登入號碼記住篩選習慣';
    if (emptyEl) emptyEl.hidden = items.length > 0;
    if (!listEl) return;
    if (items.length && !filteredItems.length) {
      listEl.innerHTML = '<div class="saved-cases-empty saved-cases-empty--inline"><strong>沒有符合條件的收藏案件</strong><p>請放寬地區、價格、房數或關鍵字條件。</p></div>';
      return;
    }
    const selectedKeys = new Set(loadSelectedCases().map((it) => String(stableKey(normalize(it) || it)).toLowerCase()).filter(Boolean));
    listEl.innerHTML = filteredItems.map((it) => renderCard(it, selectedKeys)).join('');
    listEl.querySelectorAll('[data-add-compare-key]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        addSavedCaseToCompare(String(btn.getAttribute('data-add-compare-key') || ''), btn);
      });
    });
    listEl.querySelectorAll('[data-saved-case-key]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        removeSavedCase(String(btn.getAttribute('data-saved-case-key') || ''));
      });
    });
  }

  function bindFilters() {
    applyFiltersToControls(readFilterPrefs());
    Object.values(getFilterControls()).forEach((el) => {
      if (!el) return;
      el.addEventListener(el.type === 'search' || el.type === 'number' ? 'input' : 'change', renderSavedCasesPage);
    });
    const reset = document.getElementById('saved-cases-filter-reset');
    if (reset) {
      reset.addEventListener('click', () => {
        writeFilterPrefs({ ...FILTER_DEFAULTS });
        applyFiltersToControls({ ...FILTER_DEFAULTS });
        renderSavedCasesPage();
      });
    }
  }

  window.renderSavedCasesPage = renderSavedCasesPage;
  window.clearSavedCasesPage = clearSavedCases;
  document.addEventListener('DOMContentLoaded', () => {
    bindSelectedCaseSync();
    bindSavedCaseSync();
    bindFilters();
    renderSavedCasesPage();
  });
})();
