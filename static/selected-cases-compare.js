const SELECTED_COMPARE_KEY = 'sclaw_support_selected_cases_v1';
const SELECTED_COMPARE_CHANNEL = 'sclaw_support_selected_cases_channel_v1';
let selectedCompareAnnotationsOn = true;
let scCurrentItems = [];
let scSyncChannel = null;

function scEsc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function scText(value, fallback = '') {
  const s = String(value == null ? '' : value).trim();
  return s || fallback;
}

function scUrlList(value, limit = 12) {
  const out = [];
  const push = (v) => {
    const s = String(v || '').trim();
    if (!s || /^javascript:/i.test(s)) return;
    if (!/^https?:\/\//i.test(s) && !s.startsWith('/')) return;
    if (!out.includes(s)) out.push(s);
  };
  if (Array.isArray(value)) value.forEach(push);
  else String(value || '').split(/[\n\r\t,]+/).forEach(push);
  return out.slice(0, limit);
}

function scStringList(value, limit = 12) {
  const out = [];
  const push = (v) => {
    const s = scText(v);
    if (s && !out.includes(s)) out.push(s);
  };
  if (Array.isArray(value)) value.forEach(push);
  else String(value || '').split(/[\n\r,;；]+/).forEach(push);
  return out.slice(0, limit);
}

function scCaseKey(raw) {
  const sid = Number(raw && raw.source_item_id);
  if (sid > 0) return `sid:${sid}`;
  const cid = Number(raw && raw.content_id);
  if (cid > 0) return `cid:${cid}`;
  const url = String((raw && raw.item_url) || '').trim();
  if (url) return `url:${url}`;
  const title = String((raw && (raw.title || raw.title_zh_hant || raw.title_original)) || '').trim();
  return title ? `title:${title}` : '';
}

function scNormalize(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const title = scText(raw.title || raw.title_zh_hant || raw.title_original);
  const itemUrl = scText(raw.item_url);
  if (!title && !itemUrl) return null;
  const region = scText(raw.region || raw.jp_region_display_zh);
  const transit = scText(raw.transit || raw.transit_line_zh || raw.access_line_jp);
  return {
    case_key: scText(raw.case_key || scCaseKey(raw)),
    source_item_id: Number(raw.source_item_id || 0),
    content_id: Number(raw.content_id || 0),
    title,
    source_name: scText(raw.source_name),
    item_url: itemUrl,
    article_url: scText(raw.article_url),
    region,
    transit,
    address_hint_zh: scText(raw.address_hint_zh || raw.address || [region, transit].filter(Boolean).join('｜')),
    price_text_hant: scText(raw.price_text_hant || raw.price_hint || raw.price_text || raw.price_label || raw.price || raw.rent_text || raw.monthly_rent),
    price_fx_hant: scText(raw.price_fx_hant || raw.price_fx),
    layout_text_hant: scText(raw.layout_text_hant),
    layout_line_jp: scText(raw.layout_line_jp),
    area_text_hant: scText(raw.area_text_hant),
    exclusive_area_jp: scText(raw.exclusive_area_jp),
    other_area_jp: scText(raw.other_area_jp),
    age_text_hant: scText(raw.age_text_hant),
    built_ym_jp: scText(raw.built_ym_jp),
    transaction_label_zh: scText(raw.transaction_label_zh),
    building_type_zh: scText(raw.building_type_zh),
    property_rights_jp: scText(raw.property_rights_jp || raw.property_rights_text),
    land_rights_jp: scText(raw.land_rights_jp || raw.land_rights_text),
    house_use_jp: scText(raw.house_use_jp || raw.house_use_text),
    title_age_jp: scText(raw.title_age_jp || raw.title_age_text),
    layout_structure_jp: scText(raw.layout_structure_jp || raw.layout_structure_text),
    elevator_ratio_jp: scText(raw.elevator_ratio_jp || raw.elevator_ratio_text),
    elevator_jp: scText(raw.elevator_jp || raw.elevator_text),
    heating_jp: scText(raw.heating_jp || raw.heating_text),
    mortgage_jp: scText(raw.mortgage_jp || raw.mortgage_text),
    community_info_jp: scText(raw.community_info_jp || raw.community_info_text),
    building_area_jp: scText(raw.building_area_jp || raw.building_area_text),
    land_area_jp: scText(raw.land_area_jp || raw.land_area_text),
    management_company_jp: scText(raw.management_company_jp || raw.management_company_text),
    management_form_jp: scText(raw.management_form_jp || raw.management_form_text),
    use_district_jp: scText(raw.use_district_jp || raw.use_district_text),
    urban_planning_jp: scText(raw.urban_planning_jp || raw.urban_planning_text),
    land_category_jp: scText(raw.land_category_jp || raw.land_category_text),
    road_access_jp: scText(raw.road_access_jp || raw.road_access_text),
    private_road_jp: scText(raw.private_road_jp || raw.private_road_text),
    building_coverage_jp: scText(raw.building_coverage_jp || raw.building_coverage_text),
    floor_area_ratio_jp: scText(raw.floor_area_ratio_jp || raw.floor_area_ratio_text),
    transaction_agent_jp: scText(raw.transaction_agent_jp || raw.transaction_agent_text),
    building_confirm_jp: scText(raw.building_confirm_jp || raw.building_confirm_text),
    contact_summary_jp: scText(raw.contact_summary_jp || raw.contact_summary_text),
    building_name_jp: scText(raw.building_name_jp),
    address_line_jp: scText(raw.address_line_jp),
    access_line_jp: scText(raw.access_line_jp),
    floor_text_hant: scText(raw.floor_text_hant),
    floor_structure_jp: scText(raw.floor_structure_jp),
    structure_jp: scText(raw.structure_jp),
    balcony_line_jp: scText(raw.balcony_line_jp),
    manage_fee_jp: scText(raw.manage_fee_jp),
    reserve_fee_jp: scText(raw.reserve_fee_jp),
    parking_jp: scText(raw.parking_jp),
    orientation_jp: scText(raw.orientation_jp || raw.orientation_text || raw.direction_text),
    decoration_jp: scText(raw.decoration_jp || raw.renovation_text || raw.interior_text),
    total_units_jp: scText(raw.total_units_jp),
    sales_units_jp: scText(raw.sales_units_jp),
    status_jp: scText(raw.status_jp),
    handover_jp: scText(raw.handover_jp),
    property_no_jp: scText(raw.property_no_jp),
    info_open_jp: scText(raw.info_open_jp),
    next_update_jp: scText(raw.next_update_jp),
    related_links_jp: scText(raw.related_links_jp),
    company_guide_jp: scText(raw.company_guide_jp),
    staff_message_jp: scText(raw.staff_message_jp),
    inquiry_contact_jp: scText(raw.inquiry_contact_jp),
    homes_site_trail_jp: scText(raw.homes_site_trail_jp),
    nearby_summary: scText(raw.nearby_summary),
    nearby_2km_summary: scText(raw.nearby_2km_summary),
    feature_tags_hant: scStringList(raw.feature_tags_hant, 12),
    thumb_url: scText(raw.thumb_url),
    gallery_urls: scUrlList(raw.gallery_urls || raw.image_urls, 12),
    floorplan_urls: scUrlList(raw.floorplan_urls || raw.floor_plan_urls, 4),
    image_count: Number(raw.image_count || 0),
    case_time_at: scText(raw.case_time_at),
    data_time_at: scText(raw.data_time_at),
    sort_time_at: scText(raw.sort_time_at),
    source_listing_time_jp: scText(raw.source_listing_time_jp),
    case_time_label_hant: scText(raw.case_time_label_hant),
    published_at: scText(raw.published_at),
    crawled_at: scText(raw.crawled_at),
    last_checked_at: scText(raw.last_checked_at),
    updated_at: scText(raw.updated_at),
  };
}

function scLoadCases() {
  try {
    const raw = JSON.parse(localStorage.getItem(SELECTED_COMPARE_KEY) || '[]');
    const seen = new Set();
    const out = [];
    for (const item of Array.isArray(raw) ? raw : []) {
      const n = scNormalize(item);
      if (!n) continue;
      const key = String(n.case_key || scCaseKey(n)).toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(n);
      if (out.length >= 20) break;
    }
    return out;
  } catch (_) {
    return [];
  }
}

function scMerge(base, extra) {
  const next = { ...base };
  for (const [key, val] of Object.entries(extra || {})) {
    const oldVal = next[key];
    const oldEmpty = oldVal == null || String(oldVal).trim() === '' || (Array.isArray(oldVal) && !oldVal.length);
    const newHasValue = val != null && String(val).trim() !== '';
    if ((oldEmpty || key === 'price_text_hant' || key === 'price_fx_hant' || key === 'gallery_urls' || key === 'floorplan_urls' || key === 'image_count') && newHasValue) {
      next[key] = val;
    }
  }
  return scNormalize(next) || next;
}

function scSaveCases(items) {
  const payload = (Array.isArray(items) ? items : []).map((it) => ({
    case_key: scText(it.case_key || scCaseKey(it)),
    source_item_id: Number(it.source_item_id || 0),
    content_id: Number(it.content_id || 0),
    title: scText(it.title),
    source_name: scText(it.source_name),
    item_url: scText(it.item_url),
    article_url: scText(it.article_url),
    transaction_label_zh: scText(it.transaction_label_zh),
    region: scText(it.region),
    transit: scText(it.transit),
    address_hint_zh: scText(it.address_hint_zh),
    price_text_hant: scText(it.price_text_hant),
    price_fx_hant: scText(it.price_fx_hant),
    layout_text_hant: scText(it.layout_text_hant),
    area_text_hant: scText(it.area_text_hant),
    building_type_zh: scText(it.building_type_zh),
    thumb_url: scText(it.thumb_url),
    gallery_urls: scUrlList(it.gallery_urls, 12),
    floorplan_urls: scUrlList(it.floorplan_urls, 4),
    image_count: Number(it.image_count || 0),
  }));
  try {
    localStorage.setItem(SELECTED_COMPARE_KEY, JSON.stringify(payload));
  } catch (_) {}
  scNotifySelectedCasesChanged(payload);
}

function scNotifySelectedCasesChanged(payload) {
  try {
    if (!scSyncChannel && 'BroadcastChannel' in window) scSyncChannel = new BroadcastChannel(SELECTED_COMPARE_CHANNEL);
    if (scSyncChannel) scSyncChannel.postMessage({ type: 'selected-cases-updated', source: 'compare', items: payload || [] });
  } catch (_) {}
}

function removeSelectedCompareCase(index) {
  if (index < 0 || index >= scCurrentItems.length) return;
  scCurrentItems = scCurrentItems.filter((_, idx) => idx !== index);
  scSaveCases(scCurrentItems);
  renderSelectedCompare(scCurrentItems);
}

window.removeSelectedCompareCase = removeSelectedCompareCase;

async function scEnrich(items) {
  if (!items.length) return items;
  try {
    const res = await fetch('/api/support/selected-cases-enrich', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cases: items }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !Array.isArray(data.items)) return items;
    const byKey = new Map();
    data.items.map(scNormalize).filter(Boolean).forEach((it) => byKey.set(String(it.case_key || scCaseKey(it)).toLowerCase(), it));
    return items.map((it) => scMerge(it, byKey.get(String(it.case_key || scCaseKey(it)).toLowerCase()) || {}));
  } catch (_) {
    return items;
  }
}

function scThumb(it) {
  const thumb = scText(it.thumb_url);
  if (/^https?:\/\//i.test(thumb) || thumb.startsWith('/')) return thumb;
  return scUrlList(it.gallery_urls, 1)[0] || '';
}

function scLocalLink(it) {
  const sid = Number(it.source_item_id || 0);
  if (sid > 0) return `/case/${sid}?return_to=${encodeURIComponent('/selected-cases-compare')}`;
  const article = scText(it.article_url);
  if (article && article.startsWith('/')) return article;
  return '';
}

function scExternalLink(it) {
  const item = scText(it.item_url);
  return /^https?:\/\//i.test(item) ? item : '';
}

function scNum(text) {
  const s = scText(text).replace(/,/g, '');
  const m = s.match(/([0-9]+(?:\.[0-9]+)?)/);
  return m ? Number(m[1]) : NaN;
}

function scPriceScore(it) {
  const s = scText(it.price_text_hant || it.price_fx_hant).replace(/,/g, '');
  const m = s.match(/([0-9]+(?:\.[0-9]+)?)\s*(億|万|萬)?/);
  if (!m) return NaN;
  let n = Number(m[1]);
  if (m[2] === '億') n *= 10000;
  return n;
}

function scPricePerSqmScore(it) {
  const price = scPriceScore(it);
  const area = scAreaScore(it);
  if (!Number.isFinite(price) || !Number.isFinite(area) || area <= 0) return NaN;
  return price / area;
}

function scPricePerSqmLabel(it) {
  const score = scPricePerSqmScore(it);
  if (!Number.isFinite(score)) return '';
  const unit = /租|賃貸|rent|月/i.test([it.transaction_label_zh, it.price_text_hant].join(' ')) ? '萬日圓 / ㎡' : '萬日圓 / ㎡';
  return `${score.toFixed(score >= 100 ? 0 : 1)} ${unit}`;
}

function scAreaScore(it) {
  const s = [it.exclusive_area_jp, it.area_text_hant, it.other_area_jp].filter(Boolean).join(' ');
  const sqm = s.match(/([0-9]+(?:\.[0-9]+)?)\s*(?:㎡|m²|m2)/i);
  if (sqm) return Number(sqm[1]);
  const tsubo = s.match(/([0-9]+(?:\.[0-9]+)?)\s*坪/);
  return tsubo ? Number(tsubo[1]) * 3.3058 : NaN;
}

function scWalkScore(it) {
  const s = [it.access_line_jp, it.transit, it.address_hint_zh].filter(Boolean).join(' ');
  const nums = [...s.matchAll(/(?:徒歩|步行|徒步)?\s*([0-9]{1,3})\s*分/g)].map((m) => Number(m[1])).filter(Number.isFinite);
  return nums.length ? Math.min(...nums) : NaN;
}

function scWalkLabel(it) {
  const walk = scWalkScore(it);
  return Number.isFinite(walk) ? `${walk} 分鐘` : '';
}

function scCompactText(value, max = 56) {
  let s = scText(value)
    .replace(/\s+/g, ' ')
    .replace(/[（(]\s*(?:匯率|汇率|換算|换算)[^）)]*[）)]/g, '')
    .replace(/[（(]\s*(?:僅供參考|仅供参考)[^）)]*[）)]/g, '')
    .replace(/(?:，|,)?\s*(?:匯率|汇率)[^，。,.)）]*?(?:參考|参考)/g, '')
    .trim();
  if (!s) return '';
  const firstLine = s.split(/\n+/)[0].trim();
  s = firstLine || s;
  return s.length > max ? `${s.slice(0, Math.max(1, max - 1)).trim()}…` : s;
}

function scPriceFxCompact(it) {
  return scCompactText(it.price_fx_hant, 34);
}

function scTrafficSummary(it) {
  const sourceParts = [it.access_line_jp, it.transit]
    .map((x) => scText(x))
    .filter(Boolean)
    .filter((x, idx, arr) => arr.findIndex((y) => scNormalizeCompareValue(y) === scNormalizeCompareValue(x)) === idx);
  const s = scText(sourceParts.join(' '));
  if (!s) return '';
  const walks = [...s.matchAll(/(?:徒歩|步行|徒步|歩)\s*([0-9]{1,3})\s*分/g)]
    .map((m) => Number(m[1]))
    .filter(Number.isFinite);
  const numberedRoutes = [...s.matchAll(/[（(]\s*[0-9]{1,2}\s*[）)]/g)].length;
  const stationRoutes = [...s.matchAll(/[「『][^」』]{1,24}[」』]\s*駅/g)].length;
  const routeCount = numberedRoutes || stationRoutes || walks.length || (s ? 1 : 0);
  const nearest = walks.length ? Math.min(...walks) : NaN;
  const parts = [`${routeCount}條`];
  if (Number.isFinite(nearest)) {
    parts.push(`最近 ${nearest}分鐘`);
    parts.push(`約 ${nearest * 80}m`);
  }
  return parts.join(' / ');
}

function scAgeScore(it) {
  const s = [it.built_ym_jp, it.age_text_hant, it.status_jp].filter(Boolean).join(' ');
  if (/新築|新建|未入居/.test(s)) return 0;
  const m = s.match(/築\s*([0-9]{1,3})\s*年|([0-9]{1,3})\s*年/);
  return m ? Number(m[1] || m[2]) : NaN;
}

function scAgeLabel(it) {
  const age = scAgeScore(it);
  if (!Number.isFinite(age)) return '';
  return age === 0 ? '新築 / 新建' : `約 ${age} 年`;
}

function scFeeScore(value) {
  return scNum(value);
}

function scMonthlyCostScore(it) {
  const fees = [it.manage_fee_jp, it.reserve_fee_jp].map(scFeeScore).filter(Number.isFinite);
  return fees.length ? fees.reduce((sum, n) => sum + n, 0) : NaN;
}

function scMonthlyCostLabel(it) {
  const cost = scMonthlyCostScore(it);
  return Number.isFinite(cost) ? `${Math.round(cost).toLocaleString('ja-JP')}円 / 月` : '';
}

function scFloorPlanCount(it) {
  return scUrlList(it.floorplan_urls, 4).length;
}

function scNearbyLineCount(value) {
  return scText(value).split(/\n+/).filter((x) => /｜/.test(x)).length;
}

const SC_NEARBY_CATEGORIES = [
  { id: 'subway', label: '地鐵 / 車站', keywords: /駅|站|地铁|地下鉄|地下铁|電車|电车|鉄道|鐵道|線|line/i },
  { id: 'bus', label: '公交', keywords: /バス停|公交|公車|巴士|bus/i },
  { id: 'school', label: '學校', keywords: /小学校|中学校|学校|幼稚園|保育園|學校|学校|大学|大學/i },
  { id: 'medical', label: '醫療', keywords: /病院|クリニック|医院|醫院|診所|薬局|藥局/i },
  { id: 'shop', label: '商超', keywords: /スーパー|超市|コンビニ|便利店|商店街|百貨|ショッピング|购物|購物|銀行|银行|菜場|菜场/i },
  { id: 'park', label: '公園', keywords: /公園|公园|緑地|綠地|河川|海岸|自然|スポーツ|運動/i },
];

function scNearbyDistanceMeters(value) {
  const s = scText(value).replace(/,/g, '');
  const km = s.match(/([0-9]+(?:\.[0-9]+)?)\s*(?:km|㎞|公里)/i);
  if (km) return Number(km[1]) * 1000;
  const meter = s.match(/([0-9]+(?:\.[0-9]+)?)\s*(?:m|ｍ|米|公尺)/i);
  if (meter) return Number(meter[1]);
  const walk = s.match(/(?:徒歩|步行|徒步|歩)\s*([0-9]{1,3})\s*分/i);
  return walk ? Number(walk[1]) * 80 : NaN;
}

function scNearbyDistanceLabel(meters) {
  if (!Number.isFinite(meters)) return '';
  if (meters >= 1000) return `${(meters / 1000).toFixed(meters >= 1950 ? 0 : 1)}km`;
  return `${Math.max(1, Math.round(meters))}m`;
}

function scNearbyCategoryId(line) {
  const s = scText(line);
  if (/バス停|公交|公車|巴士|bus/i.test(s)) return 'bus';
  const found = SC_NEARBY_CATEGORIES.find((cat) => cat.keywords.test(s));
  return found ? found.id : 'other';
}

function scNearbyStats(it) {
  const lines = scText(it.nearby_2km_summary || it.nearby_summary)
    .split(/\n+/)
    .map((line) => line.trim())
    .filter((line) => line.includes('｜'));
  const stats = {};
  SC_NEARBY_CATEGORIES.forEach((cat) => {
    stats[cat.id] = { count: 0, nearest: NaN };
  });
  for (const line of lines) {
    const categoryId = scNearbyCategoryId(line);
    if (!stats[categoryId]) continue;
    const dist = scNearbyDistanceMeters(line);
    if (Number.isFinite(dist) && dist > 2000) continue;
    stats[categoryId].count += 1;
    if (Number.isFinite(dist) && (!Number.isFinite(stats[categoryId].nearest) || dist < stats[categoryId].nearest)) {
      stats[categoryId].nearest = dist;
    }
  }
  return stats;
}

function scNearbyCategoryLabel(it, categoryId) {
  const stat = (scNearbyStats(it)[categoryId]) || { count: 0, nearest: NaN };
  const dist = scNearbyDistanceLabel(stat.nearest);
  return dist ? `${stat.count}個 / 最近 ${dist}` : `${stat.count}個`;
}

function scNearbyCategoryScore(categoryId) {
  return (it) => {
    const stat = (scNearbyStats(it)[categoryId]) || { count: 0, nearest: NaN };
    if (!stat.count) return NaN;
    const distanceBonus = Number.isFinite(stat.nearest) ? Math.max(0, (2000 - stat.nearest) / 2000) : 0;
    return stat.count * 10 + distanceBonus;
  };
}

function scSourceTransactionLabel(it) {
  return [it.source_name, it.transaction_label_zh].filter(Boolean).join(' / ');
}

function scDetailLinks(it) {
  const links = [];
  const local = scLocalLink(it);
  const external = scExternalLink(it);
  if (local) links.push(`站內詳情：${local}`);
  if (external) links.push(`查看原站：${external}`);
  return links.join('\n');
}

const SC_KEEP_WHEN_SAME = new Set([
  'price',
  'unitPrice',
  'layout',
  'area',
  'traffic',
  'nearby-subway',
  'nearby-bus',
  'nearby-school',
  'nearby-medical',
  'nearby-shop',
  'nearby-park',
  'imageCount',
  'links',
]);

const SC_FIELD_DUPLICATE_PRIORITY = [
  'price',
  'unitPrice',
  'layout',
  'area',
  'traffic',
  'walkMinute',
  'region',
  'address',
  'map',
  'built',
  'floor',
  'orientation',
  'decoration',
  'elevator',
  'kind',
  'structure',
  'propertyRights',
  'landRights',
  'houseUse',
];

function scDisplayValue(field, item) {
  return scText(field.get(item));
}

function scNormalizeCompareValue(value) {
  return scText(value)
    .replace(/<[^>]*>/g, '')
    .replace(/\s+/g, '')
    .replace(/[，,。．、|｜/／:：()（）\[\]【】「」『』\-—_]/g, '')
    .replace(/約|大約|左右|附近|最近|可確認|待確認/g, '')
    .toLowerCase();
}

function scFieldPriority(field) {
  const idx = SC_FIELD_DUPLICATE_PRIORITY.indexOf(field.id);
  return idx >= 0 ? idx : 999;
}

function scIsEmptyLikeCompareValue(value) {
  const raw = scText(value);
  const normalized = scNormalizeCompareValue(raw);
  if (!normalized) return true;
  if (/^(?:0|0個|0个|無|无|なし|無し|沒有|没有|none|n\/a|na|未提供|待確認|待确认|不明|暫無|暂无)$/i.test(raw)) return true;
  if (/^0(?:個|个|件|張|张|條|条|分鐘|分钟|分|m|米|km|公里)?$/i.test(normalized)) return true;
  if (/^0(?:個|个)(?:最近)?$/i.test(normalized)) return true;
  return false;
}

function scShouldDropSameRow(field, values) {
  if (SC_KEEP_WHEN_SAME.has(field.id)) return false;
  const filled = values
    .filter((value) => !scIsEmptyLikeCompareValue(value))
    .map(scNormalizeCompareValue)
    .filter(Boolean);
  if (filled.length < 2) return false;
  return new Set(filled).size === 1;
}

function scDedupeCompareSections(sections, items) {
  const seen = new Map();
  return sections
    .map((section) => {
      const candidates = section.fields
        .map((field) => ({ field, values: items.map((it) => scDisplayValue(field, it)) }))
        .filter(({ field, values }) => !scShouldDropSameRow(field, values))
        .sort((a, b) => scFieldPriority(a.field) - scFieldPriority(b.field));
      const fields = [];
      for (const row of candidates) {
        const normalizedValues = row.values.map(scNormalizeCompareValue);
        const hasComparableContent = row.values.some((value) => !scIsEmptyLikeCompareValue(value));
        const signature = normalizedValues.join('||');
        if (hasComparableContent && signature && seen.has(signature)) continue;
        if (hasComparableContent && signature) seen.set(signature, row.field.id);
        fields.push(row.field);
      }
      fields.sort((a, b) => section.fields.indexOf(a) - section.fields.indexOf(b));
      return { ...section, fields };
    })
    .filter((section) => section.fields.length);
}

function scCompareSections() {
  return [
    {
      id: 'basic',
      title: '基本',
      fields: [
        { id: 'building', label: '小區 / 建物', get: (it) => it.building_name_jp || it.community_info_jp },
        { id: 'kind', label: '物件類型', get: (it) => it.building_type_zh },
        { id: 'houseUse', label: '房屋用途', get: (it) => it.house_use_jp },
        { id: 'propertyRights', label: '產權', get: (it) => it.property_rights_jp || it.title_age_jp },
        { id: 'landRights', label: '土地權利', get: (it) => it.land_rights_jp },
        { id: 'mortgage', label: '抵押', get: (it) => it.mortgage_jp },
      ],
    },
    {
      id: 'price',
      title: '價格 / 面積',
      fields: [
        { id: 'price', label: '總價 / 租金', get: (it) => it.price_text_hant || it.price_fx_hant, compare: 'low', score: scPriceScore, badge: '價格較低' },
        { id: 'unitPrice', label: '單價', get: scPricePerSqmLabel, compare: 'low', score: scPricePerSqmScore, badge: '單價較低' },
        { id: 'fx', label: '補充價格', get: scPriceFxCompact },
        { id: 'layout', label: '格局', get: (it) => it.layout_line_jp || it.layout_text_hant },
        { id: 'layoutStructure', label: '戶型', get: (it) => it.layout_structure_jp },
        { id: 'area', label: '面積', get: (it) => it.exclusive_area_jp || it.area_text_hant, compare: 'high', score: scAreaScore, badge: '面積較大' },
        { id: 'landArea', label: '土地面積', get: (it) => it.land_area_jp },
        { id: 'otherArea', label: '其他面積', get: (it) => it.other_area_jp || it.balcony_line_jp },
      ],
    },
    {
      id: 'location',
      title: '位置 / 交通',
      fields: [
        { id: 'region', label: '所在區域', get: (it) => it.region },
        { id: 'address', label: '地址 / 位置', get: (it) => scCompactText(it.address_line_jp || it.address_hint_zh, 34) },
        { id: 'map', label: '地圖查找', get: (it) => it.address_line_jp || it.address_hint_zh || it.region, type: 'maps' },
        { id: 'traffic', label: '交通', get: scTrafficSummary, compare: 'low', score: scWalkScore, badge: '交通較近' },
      ],
    },
    {
      id: 'buildingSpec',
      title: '建築 / 裝修',
      fields: [
        { id: 'built', label: '築年月 / 屋齡', get: (it) => it.built_ym_jp || it.age_text_hant, compare: 'low', score: scAgeScore, badge: '屋齡較新' },
        { id: 'floor', label: '所在階 / 階數', get: (it) => it.floor_structure_jp || it.floor_text_hant },
        { id: 'elevator', label: '電梯', get: (it) => it.elevator_jp },
        { id: 'orientation', label: '朝向 / 採光', get: (it) => it.orientation_jp },
        { id: 'decoration', label: '裝修 / 翻新', get: (it) => it.decoration_jp },
        { id: 'structure', label: '建物構造', get: (it) => it.structure_jp },
        { id: 'heating', label: '供暖方式', get: (it) => it.heating_jp },
        { id: 'tags', label: '設備 / 特色', get: (it) => (it.feature_tags_hant || []).join('、') },
      ],
    },
    {
      id: 'community',
      title: '小區 / 管理',
      fields: [
        { id: 'totalUnits', label: '總戶數', get: (it) => it.total_units_jp },
        { id: 'salesUnits', label: '在售 / 出租', get: (it) => it.sales_units_jp },
        { id: 'managementCompany', label: '管理公司', get: (it) => it.management_company_jp },
        { id: 'managementForm', label: '管理方式', get: (it) => it.management_form_jp },
        { id: 'manage', label: '管理費', get: (it) => it.manage_fee_jp, compare: 'low', score: (it) => scFeeScore(it.manage_fee_jp), badge: '管理費較低' },
        { id: 'reserve', label: '修繕積立金', get: (it) => it.reserve_fee_jp, compare: 'low', score: (it) => scFeeScore(it.reserve_fee_jp), badge: '修繕費較低' },
        { id: 'monthlyCost', label: '月固定費', get: scMonthlyCostLabel, compare: 'low', score: scMonthlyCostScore, badge: '月固定費較低' },
        { id: 'parking', label: '停車', get: (it) => it.parking_jp },
      ],
    },
    {
      id: 'landPlanning',
      title: '土地 / 規劃',
      fields: [
        { id: 'useDistrict', label: '用途地域', get: (it) => it.use_district_jp },
        { id: 'urbanPlanning', label: '都市計畫', get: (it) => it.urban_planning_jp },
        { id: 'landCategory', label: '地目', get: (it) => it.land_category_jp },
        { id: 'roadAccess', label: '接道狀況', get: (it) => it.road_access_jp },
        { id: 'privateRoad', label: '私道負擔', get: (it) => it.private_road_jp },
        { id: 'coverage', label: '建蔽率', get: (it) => it.building_coverage_jp },
        { id: 'floorAreaRatio', label: '容積率', get: (it) => it.floor_area_ratio_jp },
      ],
    },
    {
      id: 'status',
      title: '交易 / 更新',
      fields: [
        { id: 'status', label: '現況', get: (it) => it.status_jp },
        { id: 'handover', label: '交付', get: (it) => it.handover_jp },
        { id: 'transactionAgent', label: '取引態樣', get: (it) => it.transaction_agent_jp },
      ],
    },
    {
      id: 'nearby',
      title: '周邊配套（2公里內）',
      fields: [
        ...SC_NEARBY_CATEGORIES.map((cat) => ({
          id: `nearby-${cat.id}`,
          label: cat.label,
          get: (it) => scNearbyCategoryLabel(it, cat.id),
          compare: 'high',
          score: scNearbyCategoryScore(cat.id),
          badge: `${cat.label}較完整`,
          always: true,
        })),
      ],
    },
    {
      id: 'media',
      title: '圖片 / 資料完整度',
      fields: [
        { id: 'imageCount', label: '圖片數量', get: (it) => Number(it.image_count || it.gallery_urls.length || 0) ? `${Number(it.image_count || it.gallery_urls.length)} 張` : '', compare: 'high', score: (it) => Number(it.image_count || it.gallery_urls.length || 0), badge: '圖片較完整' },
        { id: 'floorPlanCount', label: '房型圖數量', get: (it) => scFloorPlanCount(it) ? `${scFloorPlanCount(it)} 張` : '', compare: 'high', score: scFloorPlanCount, badge: '房型圖較完整' },
        { id: 'links', label: '操作', get: scDetailLinks, type: 'multiLinks' },
      ],
    },
  ];
}

function scAllFields(sections) {
  return sections.flatMap((section) => section.fields.map((field) => ({ ...field, sectionId: section.id, sectionTitle: section.title })));
}

function scActiveSections(sections, items) {
  return sections
    .map((section) => ({
      ...section,
      fields: section.fields.filter((field) => field.always || items.some((it) => scText(field.get(it)))),
    }))
    .filter((section) => section.fields.length);
}

function scWinnerMap(items, fields) {
  const winners = new Map();
  for (const field of fields) {
    if (!field.compare || typeof field.score !== 'function') continue;
    const scored = items
      .map((it, idx) => ({ idx, value: Number(field.score(it)) }))
      .filter((x) => Number.isFinite(x.value));
    if (scored.length < 2) continue;
    const bestValue = field.compare === 'high'
      ? Math.max(...scored.map((x) => x.value))
      : Math.min(...scored.map((x) => x.value));
    const best = scored.filter((x) => Math.abs(x.value - bestValue) < 0.0001).map((x) => x.idx);
    if (best.length && best.length < items.length) winners.set(field.id, { best, badge: field.badge || '相對優勢' });
  }
  return winners;
}

function scCell(value, field, item, isBest) {
  const v = scText(value);
  if (!v) return `<span class="selected-compare-muted">${scEsc(field.emptyLabel || '—')}</span>`;
  let html = '';
  if (field.type === 'link') {
    const attrs = /^https?:\/\//i.test(v) ? ' target="_blank" rel="nofollow noopener"' : '';
    html = `<a class="selected-compare-link" href="${scEsc(v)}"${attrs}>${scEsc(field.linkLabel || v)}</a>`;
  } else if (field.type === 'maps') {
    const q = encodeURIComponent(v);
    html = `<span class="selected-compare-map-actions">
      <a class="selected-compare-map-link" href="https://www.google.com/maps/search/?api=1&query=${q}" target="_blank" rel="nofollow noopener">Google</a>
      <a class="selected-compare-map-link" href="https://www.amap.com/search?query=${q}" target="_blank" rel="nofollow noopener">高德</a>
      <a class="selected-compare-map-link" href="https://map.baidu.com/search?querytype=s&wd=${q}" target="_blank" rel="nofollow noopener">百度</a>
    </span>`;
  } else if (field.type === 'map') {
    const href = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(v)}`;
    html = `<a class="selected-compare-link" href="${scEsc(href)}" target="_blank" rel="nofollow noopener">Google 地圖</a><span class="selected-compare-cell-sub">${scEsc(scCompactText(v, 34))}</span>`;
  } else if (field.type === 'multiLinks') {
    html = v.split(/\n+/).map((line) => {
      const m = line.match(/^([^：:]+)[：:](.+)$/);
      if (!m) return `<span>${scEsc(line)}</span>`;
      const label = scText(m[1]);
      const href = scText(m[2]);
      const attrs = /^https?:\/\//i.test(href) ? ' target="_blank" rel="nofollow noopener"' : '';
      return `<a class="selected-compare-link selected-compare-action-link" href="${scEsc(href)}"${attrs}>${scEsc(label)}</a>`;
    }).join('');
  } else {
    html = `<span>${scEsc(scCompactText(v, 64)).replace(/\n/g, '<br>')}</span>`;
  }
  if (isBest) html += '<span class="selected-compare-best-badge">優勢</span>';
  return html;
}

function renderSelectedCompareSummary(items, fields) {
  document.getElementById('selected-compare-count').textContent = String(items.length);
  document.getElementById('selected-compare-field-count').textContent = String(fields.length);
}

function renderSelectedCompareEmpty(isEmpty) {
  const empty = document.getElementById('selected-compare-empty');
  empty.hidden = !isEmpty;
  if (isEmpty) document.getElementById('selected-compare-status').textContent = '無資料';
}

function renderSelectedCompareAdvantages(items, fields, winners) {
  const wrap = document.getElementById('selected-compare-advantage-list');
  if (!wrap) return;
  const section = wrap.closest('.selected-compare-advantage-section');
  const rows = [];
  for (const field of fields) {
    const win = winners.get(field.id);
    if (!win) continue;
    for (const idx of win.best) {
      const it = items[idx];
      rows.push({ idx, field, value: scText(field.get(it)), badge: win.badge });
    }
  }
  if (!rows.length) {
    if (section) section.hidden = true;
    wrap.innerHTML = '';
    return;
  }
  if (section) section.hidden = false;
  wrap.innerHTML = rows.slice(0, 12).map((row) => `<article class="selected-compare-advantage-card">
    <span>房源 ${String(row.idx + 1).padStart(2, '0')}</span>
    <strong>${scEsc(row.badge)}</strong>
    <p>${scEsc(row.field.label)}：${scEsc(row.value || '—')}</p>
  </article>`).join('');
}

function renderSelectedCompareCards(items) {
  const cards = document.getElementById('selected-compare-cards');
  cards.innerHTML = items.map((it, idx) => {
    const thumb = scThumb(it);
    const marker = `房源 ${String(idx + 1).padStart(2, '0')}`;
    const imageCount = Number(it.image_count || it.gallery_urls.length || 0);
    const imageLabel = imageCount ? `${imageCount} 張圖片` : (thumb ? '主圖' : '暫無配圖');
    return `<article class="selected-compare-card">
      <div class="selected-compare-card-index">${scEsc(marker)}</div>
      <button type="button" class="selected-compare-remove" onclick="removeSelectedCompareCase(${idx})" aria-label="移除${scEsc(marker)}">移除</button>
      <div class="selected-compare-card-media${thumb ? '' : ' is-empty'}">
        ${thumb ? `<img src="${scEsc(thumb)}" alt="${scEsc(it.title)}" loading="${idx < 4 ? 'eager' : 'lazy'}" decoding="async" referrerpolicy="no-referrer">` : ''}
        <span>${thumb ? '圖片載入中' : '暫無配圖'}</span>
      </div>
      <div class="selected-compare-image-meta">
        <span class="selected-compare-chip">${scEsc(it.source_name || '日本房源')}</span>
        <strong>${scEsc(imageLabel)}</strong>
      </div>
    </article>`;
  }).join('');
}

function renderSelectedCompareFloorplans(items) {
  const section = document.getElementById('selected-compare-floorplan-section');
  const wrap = document.getElementById('selected-compare-floorplans');
  if (!wrap) return;
  const hasAnyPlan = items.some((it) => scFloorPlanCount(it) > 0);
  if (section) section.hidden = !hasAnyPlan;
  if (!hasAnyPlan) {
    wrap.innerHTML = '';
    return;
  }
  wrap.innerHTML = items.map((it, idx) => {
    const plan = scUrlList(it.floorplan_urls, 1)[0] || '';
    const marker = `房源 ${String(idx + 1).padStart(2, '0')}`;
    return `<article class="selected-compare-floorplan-card">
      <div class="selected-compare-card-index">${scEsc(marker)}</div>
      <div class="selected-compare-floorplan-media${plan ? '' : ' is-empty'}">
        ${plan ? `<img src="${scEsc(plan)}" alt="${scEsc(marker)} 房型圖" loading="${idx < 4 ? 'eager' : 'lazy'}" decoding="async" referrerpolicy="no-referrer">` : ''}
        <span>${plan ? '房型圖載入中' : '原站未提供可識別房型圖'}</span>
      </div>
      <strong>${scEsc(it.layout_line_jp || it.layout_text_hant || '格局待確認')}</strong>
    </article>`;
  }).join('');
}

function renderSelectedCompareTables(items, sections, winners) {
  const wrap = document.getElementById('selected-compare-table-groups');
  const header = `<thead><tr><th scope="col">對比項目</th>${items.map((it, idx) => {
    const thumb = scThumb(it);
    return `<th scope="col">
      <div class="selected-compare-head-card">
        <span class="selected-compare-head-index">${String(idx + 1)}</span>
        <div class="selected-compare-head-thumb${thumb ? '' : ' is-empty'}">${thumb ? `<img src="${scEsc(thumb)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : ''}</div>
        <div class="selected-compare-head-text">
          <small>房源 ${String(idx + 1).padStart(2, '0')}</small>
          <strong>${scEsc(it.title || '案件')}</strong>
          <em>${scEsc(it.price_text_hant || it.region || '')}</em>
        </div>
        <button type="button" onclick="removeSelectedCompareCase(${idx})" aria-label="移除房源 ${idx + 1}">刪除</button>
      </div>
    </th>`;
  }).join('')}</tr></thead>`;
  const body = sections.map((section) => {
    const sectionRow = `<tr class="selected-compare-category-row"><th scope="row" colspan="${items.length + 1}">${scEsc(section.title)}</th></tr>`;
    const rows = section.fields.map((field) => {
      const win = winners.get(field.id);
      const cells = items.map((it, idx) => {
        const best = Boolean(selectedCompareAnnotationsOn && win && win.best.includes(idx));
        return `<td${best ? ' class="is-best"' : ''}>${scCell(field.get(it), field, it, best)}</td>`;
      }).join('');
      return `<tr><th scope="row">${scEsc(field.label)}</th>${cells}</tr>`;
    }).join('');
    return sectionRow + rows;
  }).join('');
  wrap.innerHTML = `<div class="selected-compare-table-wrap" tabindex="0" aria-label="已選房源詳細參數">
    <table class="selected-compare-table selected-compare-main-table">${header}<tbody>${body}</tbody></table>
  </div>`;
}

function renderSelectedCompare(items) {
  const sections = scCompareSections();
  const activeSections = items.length ? scDedupeCompareSections(sections, items) : sections;
  const fields = items.length ? scAllFields(activeSections) : [];
  const winners = scWinnerMap(items, fields);
  renderSelectedCompareSummary(items, fields);
  renderSelectedCompareEmpty(!items.length);
  const gallerySection = document.getElementById('selected-compare-gallery-section');
  const detailSection = document.getElementById('selected-compare-detail-section');
  if (gallerySection) gallerySection.hidden = !items.length;
  if (detailSection) detailSection.hidden = !items.length;
  if (!items.length) {
    document.getElementById('selected-compare-cards').innerHTML = '';
    document.getElementById('selected-compare-floorplans').innerHTML = '';
    document.getElementById('selected-compare-table-groups').innerHTML = '';
    renderSelectedCompareAdvantages([], fields, new Map());
    return;
  }
  renderSelectedCompareAdvantages(items, fields, winners);
  document.body.classList.toggle('selected-compare-annotations-off', !selectedCompareAnnotationsOn);
  renderSelectedCompareCards(items);
  renderSelectedCompareFloorplans(items);
  renderSelectedCompareTables(items, activeSections, winners);
  document.getElementById('selected-compare-status').textContent = '已載入';
}

function setupSelectedCompareControls() {
  const toggle = document.getElementById('selected-compare-annotation-toggle');
  if (!toggle) return;
  selectedCompareAnnotationsOn = Boolean(toggle.checked);
  document.body.classList.toggle('selected-compare-annotations-off', !selectedCompareAnnotationsOn);
  toggle.addEventListener('change', () => {
    selectedCompareAnnotationsOn = Boolean(toggle.checked);
    renderSelectedCompare(scCurrentItems);
  });
}

async function hydrateAndRenderSelectedCompare(force) {
  const btn = document.getElementById('selected-compare-refresh');
  if (btn) btn.disabled = true;
  document.getElementById('selected-compare-status').textContent = force ? '補齊中' : '讀取中';
  let items = scLoadCases();
  scCurrentItems = items;
  renderSelectedCompare(items);
  if (items.length) {
    items = await scEnrich(items);
    scCurrentItems = items;
    renderSelectedCompare(items);
  }
  if (btn) btn.disabled = false;
}

function setupSelectedCompareSync() {
  window.addEventListener('storage', (ev) => {
    if (ev.key !== SELECTED_COMPARE_KEY) return;
    hydrateAndRenderSelectedCompare(false);
  });
  try {
    if ('BroadcastChannel' in window) {
      scSyncChannel = scSyncChannel || new BroadcastChannel(SELECTED_COMPARE_CHANNEL);
      scSyncChannel.addEventListener('message', (ev) => {
        if (!ev || !ev.data || ev.data.type !== 'selected-cases-updated' || ev.data.source === 'compare') return;
        hydrateAndRenderSelectedCompare(false);
      });
    }
  } catch (_) {}
}

setupSelectedCompareControls();
setupSelectedCompareSync();
hydrateAndRenderSelectedCompare(false);
