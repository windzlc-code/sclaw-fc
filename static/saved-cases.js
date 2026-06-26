(function () {
  'use strict';

  const SAVED_KEY = 'sclaw_support_saved_cases_v1';

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
      age_text_hant: String(raw.age_text_hant || '').trim(),
      building_type_zh: String(raw.building_type_zh || raw.transaction_label_zh || '').trim(),
      thumb_url: String(raw.thumb_url || '').trim(),
      gallery_urls: urlList(raw.gallery_urls || raw.image_urls || [], 8),
      image_count: Number(raw.image_count || 0),
      updated_at: String(raw.updated_at || raw.last_checked_at || raw.crawled_at || '').trim(),
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
    try {
      localStorage.setItem(SAVED_KEY, JSON.stringify(items.slice().reverse()));
    } catch (_) {}
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

  function renderCard(it) {
    const href = primaryLink(it);
    const safeHref = href && !/^javascript:/i.test(href) ? href : '#';
    const isExternal = /^https?:\/\//i.test(safeHref);
    const target = isExternal ? ' target="_blank" rel="nofollow noopener"' : '';
    const title = esc(it.title || '日本不動產案件');
    const src = esc(it.source_name || '日本房產');
    const thumb = thumbUrl(it);
    const key = esc(stableKey(it));
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
          <a class="primary" href="${esc(safeHref)}"${target}>站內明細</a>
          <button type="button" class="secondary" data-saved-case-key="${key}">移除收藏</button>
        </div>
      </div>
    </article>`;
  }

  function renderSavedCasesPage() {
    const items = loadSavedCases();
    const countEl = document.getElementById('saved-cases-count');
    const statusEl = document.getElementById('saved-cases-status');
    const emptyEl = document.getElementById('saved-cases-empty');
    const listEl = document.getElementById('saved-cases-list');
    if (countEl) countEl.textContent = String(items.length);
    if (statusEl) statusEl.textContent = items.length ? '已載入' : '尚未收藏';
    if (emptyEl) emptyEl.hidden = items.length > 0;
    if (!listEl) return;
    listEl.innerHTML = items.map(renderCard).join('');
    listEl.querySelectorAll('[data-saved-case-key]').forEach((btn) => {
      btn.addEventListener('click', () => removeSavedCase(String(btn.getAttribute('data-saved-case-key') || '')));
    });
  }

  window.renderSavedCasesPage = renderSavedCasesPage;
  window.clearSavedCasesPage = clearSavedCases;
  document.addEventListener('DOMContentLoaded', renderSavedCasesPage);
})();
