// Shared purchase decision tool behavior for /purchase-tools and the floating widget modal.
function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function closeFloatingPanelsExcept() {}

const PURCHASE_SELECTED_CASES_KEY = 'sclaw_support_selected_cases_v1';
const PURCHASE_SELECTED_CASES_CHANNEL_KEY = 'sclaw_support_selected_cases_channel_v1';

function purchaseToolNumber(id, fallback = 0) {
  const el = document.getElementById(id);
  const raw = el ? String(el.value || '').replace(/,/g, '').trim() : '';
  if (!raw) return fallback;
  const n = Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

function purchaseToolText(id, fallback = '') {
  const el = document.getElementById(id);
  const text = el ? String(el.value || '').trim() : '';
  return text || fallback;
}

function purchaseToolSetValue(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.value = String(value);
  if (el.tagName === 'INPUT' && String(el.type || '').toLowerCase() === 'hidden') {
    el.setAttribute('value', String(value));
  }
}

function purchaseToolSetDefaultIfEmpty(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  const current = String(el.value || '').trim();
  if (current && !(id === 'purchase-tool-cash-ratio' && Number(current) === 0)) return;
  purchaseToolSetValue(id, value);
}

function purchaseToolParseMan(value) {
  const raw = String(value == null ? '' : value).replace(/,/g, '').trim();
  if (!raw) return 0;
  const match = raw.match(/([0-9]+(?:\.[0-9]+)?)\s*(億|万|萬)?/);
  if (!match) return 0;
  const n = Number(match[1]);
  if (!Number.isFinite(n)) return 0;
  return match[2] === '億' ? n * 10000 : n;
}

function purchaseToolParseSqm(value) {
  const raw = String(value == null ? '' : value).replace(/,/g, '').trim();
  if (!raw) return 0;
  const sqm = raw.match(/([0-9]+(?:\.[0-9]+)?)\s*(?:㎡|m²|m2|平米)/i);
  if (sqm) return Number(sqm[1]) || 0;
  const tsubo = raw.match(/([0-9]+(?:\.[0-9]+)?)\s*坪/);
  return tsubo ? (Number(tsubo[1]) || 0) * 3.3058 : Number(raw.match(/([0-9]+(?:\.[0-9]+)?)/)?.[1] || 0);
}

function purchaseToolParsePercent(value) {
  const raw = String(value == null ? '' : value).replace(/,/g, '').trim();
  const n = Number(raw.match(/([0-9]+(?:\.[0-9]+)?)/)?.[1] || 0);
  return Number.isFinite(n) ? n : 0;
}

function purchaseToolMedian(values) {
  const list = values.map(Number).filter((n) => Number.isFinite(n) && n > 0).sort((a, b) => a - b);
  if (!list.length) return 0;
  const mid = Math.floor(list.length / 2);
  return list.length % 2 ? list[mid] : (list[mid - 1] + list[mid]) / 2;
}

function formatPurchaseToolMan(value) {
  const n = Number(value) || 0;
  return `${Math.round(n).toLocaleString('zh-Hant')} 萬日圓`;
}

function formatPurchaseToolYen(value) {
  const n = Number(value) || 0;
  return `${Math.round(n).toLocaleString('zh-Hant')} 日圓`;
}

function formatPurchaseToolTwdFromYen(yen, ratePer100Yen) {
  const twd = (Number(yen) || 0) * (Number(ratePer100Yen) || 0) / 100;
  return `約 NT$${Math.round(twd).toLocaleString('zh-Hant')}`;
}

function formatPurchasePercent(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '--';
  return `${n.toFixed(digits)}%`;
}

function purchaseToolRentFromYield(priceMan, yieldPct) {
  const price = Number(priceMan) || 0;
  const yieldValue = Number(yieldPct) || 0;
  return price > 0 && yieldValue > 0 ? (price * yieldValue / 100) / 12 : 0;
}

function clampPurchaseValue(value, min = 0, max = 100) {
  const n = Number(value);
  if (!Number.isFinite(n)) return min;
  return Math.max(min, Math.min(max, n));
}

function setPurchaseToolActivePane(key) {
  const modal = document.getElementById('purchase-tool-modal');
  const dialog = modal ? modal.querySelector('.purchase-tool-dialog') : document.querySelector('.purchase-tool-dialog');
  const paneKey = String(key || 'case');
  if (dialog) dialog.dataset.activePane = paneKey;
  document.querySelectorAll('[data-purchase-tab]').forEach((item) => {
    item.classList.toggle('is-active', String(item.dataset.purchaseTab || '') === paneKey);
    item.setAttribute('aria-selected', String(item.dataset.purchaseTab || '') === paneKey ? 'true' : 'false');
  });
  document.querySelectorAll('[data-purchase-pane]').forEach((pane) => {
    const active = String(pane.dataset.purchasePane || '') === paneKey;
    pane.classList.toggle('is-active', active);
    pane.setAttribute('aria-hidden', active ? 'false' : 'true');
  });
  const prevBtn = document.getElementById('purchase-tool-prev-question');
  if (prevBtn) prevBtn.style.display = paneKey === 'eligibility' ? '' : 'none';
  if (paneKey === 'eligibility') renderPurchaseEligibilityProgress();
}

function purchaseEligibilityPane() {
  return document.querySelector('[data-purchase-pane="eligibility"]');
}

function purchaseEligibilityQuestions() {
  return Array.from(document.querySelectorAll('.purchase-tool-question[data-purchase-step]'));
}

function purchaseEligibilityCurrentStep() {
  const pane = purchaseEligibilityPane();
  const questions = purchaseEligibilityQuestions();
  const raw = Number(pane?.dataset.purchaseStep || 0);
  return Math.max(0, Math.min(Math.max(0, questions.length - 1), Number.isFinite(raw) ? raw : 0));
}

function purchaseEligibilityIsComplete() {
  return purchaseEligibilityPane()?.dataset.purchaseComplete === 'true';
}

function purchaseEligibilityStepTitle(step) {
  const question = purchaseEligibilityQuestions()[step];
  return String(question?.querySelector('strong')?.textContent || `第 ${step + 1} 題`).replace(/^Q\d+\s*[｜|]\s*/, '');
}

function setPurchaseQuestionStep(step, options = {}) {
  const pane = purchaseEligibilityPane();
  const questions = purchaseEligibilityQuestions();
  if (!pane || !questions.length) return;
  const maxStep = questions.length - 1;
  const nextStep = Math.max(0, Math.min(maxStep, Number(step) || 0));
  const complete = !!(options && options.complete);
  pane.dataset.purchaseStep = String(nextStep);
  pane.dataset.purchaseComplete = complete ? 'true' : 'false';
  pane.classList.toggle('is-complete', complete);
  questions.forEach((question, index) => {
    const active = !complete && index === nextStep;
    question.classList.toggle('is-active', active);
    question.setAttribute('aria-hidden', active ? 'false' : 'true');
  });
  renderPurchaseEligibilityProgress();
}

function renderPurchaseEligibilityProgress() {
  const questions = purchaseEligibilityQuestions();
  if (!questions.length) return;
  const step = purchaseEligibilityCurrentStep();
  const complete = purchaseEligibilityIsComplete();
  const stepLabel = document.getElementById('purchase-tool-step-label');
  const stepTitle = document.getElementById('purchase-tool-step-title');
  const progressBar = document.getElementById('purchase-tool-step-progress-bar');
  const prevBtn = document.getElementById('purchase-tool-prev-question');
  const progress = complete ? 100 : ((step + 1) / questions.length) * 100;
  if (stepLabel) stepLabel.textContent = complete ? '評估完成' : `第 ${step + 1} 題 / 共 ${questions.length} 題`;
  if (stepTitle) stepTitle.textContent = complete ? '查看結果與文件準備' : purchaseEligibilityStepTitle(step);
  if (progressBar) progressBar.style.width = `${Math.max(8, Math.min(100, progress))}%`;
  if (prevBtn) prevBtn.disabled = !complete && step <= 0;
}

function goPurchaseQuestionStep(delta) {
  const questions = purchaseEligibilityQuestions();
  if (!questions.length) return;
  const current = purchaseEligibilityCurrentStep();
  if (purchaseEligibilityIsComplete()) {
    setPurchaseQuestionStep(questions.length - 1);
  } else {
    setPurchaseQuestionStep(current + (Number(delta) || 0));
  }
  calculatePurchaseTool();
}

function purchaseQuestionSelectedButton(key) {
  return document.querySelector(`[data-purchase-answer="${String(key || '')}"].is-selected`);
}

function purchaseQuestionValue(key, fallback = '') {
  const btn = purchaseQuestionSelectedButton(key);
  if (btn && Object.prototype.hasOwnProperty.call(btn.dataset, 'purchaseValue')) return String(btn.dataset.purchaseValue || '');
  return String(fallback || '');
}

function syncPurchaseQuestionButtons(answerKey, value) {
  const key = String(answerKey || '');
  const val = String(value || '');
  if (!key) return;
  document.querySelectorAll(`[data-purchase-answer="${key}"]`).forEach((btn) => {
    const btnValue = String(btn.dataset.purchaseValue || '');
    const incomeValue = String(btn.dataset.purchaseIncome || '');
    const debtValue = String(btn.dataset.purchaseDebt || '');
    let selected = btnValue === val;
    if (key === 'income-profile') {
      selected =
        incomeValue === String(document.getElementById('purchase-tool-income')?.value || '') &&
        debtValue === String(document.getElementById('purchase-tool-debt')?.value || '');
    }
    btn.classList.toggle('is-selected', selected);
    btn.setAttribute('aria-pressed', selected ? 'true' : 'false');
  });
}

function syncPurchaseHiddenFieldsFromSelectedQuestions() {
  const fieldMap = {
    'buyer-status': 'purchase-tool-buyer-status',
    purpose: 'purchase-tool-purpose',
    'payment-path': 'purchase-tool-payment-path',
    'cash-ratio': 'purchase-tool-cash-ratio',
    timeline: 'purchase-tool-timeline',
  };
  Object.entries(fieldMap).forEach(([key, fieldId]) => {
    const selected = purchaseQuestionSelectedButton(key);
    if (selected && Object.prototype.hasOwnProperty.call(selected.dataset, 'purchaseValue')) {
      purchaseToolSetValue(fieldId, selected.dataset.purchaseValue || '');
    }
  });
  const incomeSelected = purchaseQuestionSelectedButton('income-profile');
  if (incomeSelected) {
    purchaseToolSetValue('purchase-tool-income', incomeSelected.dataset.purchaseIncome || 0);
    purchaseToolSetValue('purchase-tool-debt', incomeSelected.dataset.purchaseDebt || 0);
  }
}

function setPurchaseQuestionAnswer(btn) {
  if (!btn) return;
  const key = String(btn.dataset.purchaseAnswer || '');
  if (!key) return;
  document.querySelectorAll(`[data-purchase-answer="${key}"]`).forEach((item) => {
    const selected = item === btn;
    item.classList.toggle('is-selected', selected);
    item.setAttribute('aria-pressed', selected ? 'true' : 'false');
  });
  syncPurchaseHiddenFieldsFromSelectedQuestions();
  const questions = purchaseEligibilityQuestions();
  const current = purchaseEligibilityCurrentStep();
  if (questions.length) {
    if (current < questions.length - 1) setPurchaseQuestionStep(current + 1);
    else setPurchaseQuestionStep(current, { complete: true });
  }
  calculatePurchaseTool();
}

function clearPurchaseQuestionSelections() {
  document.querySelectorAll('[data-purchase-answer]').forEach((btn) => {
    btn.classList.remove('is-selected');
    btn.setAttribute('aria-pressed', 'false');
  });
}

function refreshPurchaseQuestionAnswers(options = {}) {
  if (options && options.fromHidden) {
    syncPurchaseQuestionButtons('buyer-status', document.getElementById('purchase-tool-buyer-status')?.value || '');
    syncPurchaseQuestionButtons('purpose', document.getElementById('purchase-tool-purpose')?.value || '');
    syncPurchaseQuestionButtons('payment-path', document.getElementById('purchase-tool-payment-path')?.value || '');
    syncPurchaseQuestionButtons('cash-ratio', document.getElementById('purchase-tool-cash-ratio')?.value || '');
    syncPurchaseQuestionButtons('timeline', document.getElementById('purchase-tool-timeline')?.value || '');
    syncPurchaseQuestionButtons('income-profile', '');
  } else {
    clearPurchaseQuestionSelections();
  }
  renderPurchaseEligibilityProgress();
}

function buildPurchaseToolLoanPlan(principalMan, annualRate, years, type) {
  const principalYen = Math.max(0, Number(principalMan) || 0) * 10000;
  const months = Math.max(1, Math.round((Number(years) || 0) * 12));
  const monthlyRate = Math.max(0, Number(annualRate) || 0) / 100 / 12;
  const schedule = [];
  let totalPayment = 0;
  let firstPayment = 0;
  let lastPayment = 0;
  let monthlyDecline = 0;
  if (type === 'equal-principal') {
    const principalPart = principalYen / months;
    for (let i = 1; i <= months; i += 1) {
      const balanceBefore = Math.max(0, principalYen - principalPart * (i - 1));
      const interest = balanceBefore * monthlyRate;
      const payment = principalPart + interest;
      totalPayment += payment;
      if (i === 1) firstPayment = payment;
      if (i === months) lastPayment = payment;
      if (i <= 6) schedule.push({ month: i, payment, principal: principalPart, interest });
    }
    monthlyDecline = principalPart * monthlyRate;
  } else {
    let payment = principalYen / months;
    if (monthlyRate > 0) {
      const pow = Math.pow(1 + monthlyRate, months);
      payment = principalYen * monthlyRate * pow / (pow - 1);
    }
    let balance = principalYen;
    for (let i = 1; i <= months; i += 1) {
      const interest = balance * monthlyRate;
      const principal = Math.min(balance, payment - interest);
      balance = Math.max(0, balance - principal);
      totalPayment += payment;
      if (i <= 6) schedule.push({ month: i, payment, principal, interest });
    }
    firstPayment = payment;
    lastPayment = payment;
  }
  return {
    type,
    months,
    firstPayment,
    averagePayment: totalPayment / months,
    lastPayment,
    monthlyDecline,
    totalPayment,
    totalInterest: Math.max(0, totalPayment - principalYen),
    schedule,
  };
}

function renderPurchaseToolSchedule(plan) {
  const wrap = document.getElementById('purchase-tool-schedule-list');
  if (!wrap) return;
  const rows = Array.isArray(plan && plan.schedule) ? plan.schedule : [];
  wrap.innerHTML = rows.map((row) => `
    <div class="purchase-tool-schedule-row">
      <strong>第 ${row.month} 期</strong>
      <span>月付 ${formatPurchaseToolYen(row.payment)}</span>
      <span>本金 ${formatPurchaseToolYen(row.principal)}</span>
      <span>利息 ${formatPurchaseToolYen(row.interest)}</span>
    </div>
  `).join('') || '<p class="muted">請先輸入貸款條件。</p>';
}

function readPurchaseToolContext() {
  const mode = String(document.getElementById('purchase-tool-mode')?.value || 'house');
  const priceMan = Math.max(0, purchaseToolNumber('purchase-tool-price', 0));
  const downRate = Math.min(100, Math.max(0, purchaseToolNumber('purchase-tool-down-rate', 30)));
  const manualLoanMan = Math.max(0, purchaseToolNumber('purchase-tool-loan', 0));
  const loanMan = mode === 'loan' ? manualLoanMan : Math.max(0, priceMan * (100 - downRate) / 100);
  const derivedDownMan = Math.max(0, priceMan - loanMan);
  const areaSqm = Math.max(0, purchaseToolNumber('purchase-tool-area', 0));
  const rentMan = Math.max(0, purchaseToolNumber('purchase-tool-rent', 0));
  const monthlyFeeMan = Math.max(0, purchaseToolNumber('purchase-tool-monthly-fee', 0));
  const reserveRate = Math.min(100, Math.max(0, purchaseToolNumber('purchase-tool-reserve-rate', 0)));
  const annualTaxMan = Math.max(0, purchaseToolNumber('purchase-tool-annual-tax', 0));
  const costRate = Math.max(0, purchaseToolNumber('purchase-tool-cost-rate', 7));
  const brokerRate = Math.max(0, purchaseToolNumber('purchase-tool-broker-rate', 3.3));
  const taxRate = Math.max(0, purchaseToolNumber('purchase-tool-tax-rate', 2.1));
  const legalFeeMan = Math.max(0, purchaseToolNumber('purchase-tool-legal-fee', 0));
  const selectedType = String(document.getElementById('purchase-tool-repay')?.value || 'equal-payment');
  const years = Math.max(1, purchaseToolNumber('purchase-tool-years', 30));
  const annualRate = Math.max(0, purchaseToolNumber('purchase-tool-rate', 1.8));
  const equalPayment = buildPurchaseToolLoanPlan(loanMan, annualRate, years, 'equal-payment');
  const equalPrincipal = buildPurchaseToolLoanPlan(loanMan, annualRate, years, 'equal-principal');
  const selectedPlan = selectedType === 'equal-principal' ? equalPrincipal : equalPayment;
  const unitPrice = areaSqm > 0 ? priceMan / areaSqm : 0;
  const grossYield = priceMan > 0 ? (rentMan * 12 / priceMan) * 100 : 0;
  const reserveMan = rentMan * reserveRate / 100;
  const netAnnualIncomeMan = Math.max(0, rentMan * 12 - monthlyFeeMan * 12 - reserveMan * 12 - annualTaxMan);
  const netYield = priceMan > 0 ? (netAnnualIncomeMan / priceMan) * 100 : 0;
  const cashflowYen = rentMan * 10000 - selectedPlan.firstPayment - monthlyFeeMan * 10000 - reserveMan * 10000 - (annualTaxMan / 12) * 10000;
  const brokerFeeMan = priceMan * brokerRate / 100;
  const taxFeeMan = priceMan * taxRate / 100;
  const explicitCostMan = priceMan * costRate / 100;
  const detailedCostMan = brokerFeeMan + taxFeeMan + legalFeeMan;
  const costMan = Math.max(explicitCostMan, detailedCostMan);
  const cashNeedMan = derivedDownMan + costMan;
  const marketUnitPrice = Math.max(0, purchaseToolNumber('purchase-tool-market-unit-price', 0));
  const marketYield = Math.max(0, purchaseToolNumber('purchase-tool-market-yield', 0));
  const targetWalk = Math.max(0, purchaseToolNumber('purchase-tool-target-walk', 10));
  const targetAge = Math.max(0, purchaseToolNumber('purchase-tool-target-age', 25));
  const walk = Math.max(0, purchaseToolNumber('purchase-tool-walk', 0));
  const age = Math.max(0, purchaseToolNumber('purchase-tool-age', 0));
  const annualIncomeMan = Math.max(0, purchaseToolNumber('purchase-tool-income', 0));
  const monthlyDebtMan = Math.max(0, purchaseToolNumber('purchase-tool-debt', 0));
  const liquidAssetsMan = Math.max(0, purchaseToolNumber('purchase-tool-assets', 0));
  const existingCount = Math.max(0, purchaseToolNumber('purchase-tool-existing-property', 0));
  const assetConvertedIncomeMan = years > 0 ? (liquidAssetsMan * 0.9) / years : 0;
  const recognizedIncomeMan = annualIncomeMan + assetConvertedIncomeMan;
  return {
    mode,
    title: purchaseToolText('purchase-tool-case-title', '未命名案件'),
    region: purchaseToolText('purchase-tool-region', ''),
    station: purchaseToolText('purchase-tool-station', ''),
    propertyType: String(document.getElementById('purchase-tool-property-type')?.value || ''),
    priceMan,
    downRate,
    loanMan,
    derivedDownMan,
    years,
    annualRate,
    twdRate: Math.max(0, purchaseToolNumber('purchase-tool-twd-rate', 20.8)),
    selectedType,
    equalPayment,
    equalPrincipal,
    selectedPlan,
    areaSqm,
    unitPrice,
    rentMan,
    monthlyFeeMan,
    reserveRate,
    reserveMan,
    annualTaxMan,
    grossYield,
    netAnnualIncomeMan,
    netYield,
    cashflowYen,
    costRate,
    brokerFeeMan,
    taxFeeMan,
    legalFeeMan,
    costMan,
    cashNeedMan,
    marketUnitPrice,
    marketYield,
    targetWalk,
    targetAge,
    walk,
    age,
    annualIncomeMan,
    monthlyDebtMan,
    liquidAssetsMan,
    assetConvertedIncomeMan,
    recognizedIncomeMan,
    existingCount,
  };
}

function evaluatePurchaseComparables(ctx) {
  const reasons = [];
  let score = 100;
  let band = '待輸入';
  if (ctx.marketUnitPrice > 0 && ctx.unitPrice > 0) {
    const diffPct = ((ctx.unitPrice - ctx.marketUnitPrice) / ctx.marketUnitPrice) * 100;
    if (diffPct > 12) {
      score -= 24;
      band = '高於同區';
      reasons.push(`單價高於同區均價約 ${diffPct.toFixed(1)}%，需確認樓層、裝修或稀缺性。`);
    } else if (diffPct < -12) {
      score -= 6;
      band = '低於同區';
      reasons.push(`單價低於同區均價約 ${Math.abs(diffPct).toFixed(1)}%，應檢查權利、屋況與出租限制。`);
    } else {
      band = '接近同區';
      reasons.push('單價落在同區均價附近，具備進一步比較基礎。');
    }
  } else {
    score -= 12;
    reasons.push('尚未輸入同區均價，價格帶只能做內部估算。');
  }
  if (ctx.marketYield > 0 && ctx.grossYield > 0) {
    const yieldGap = ctx.grossYield - ctx.marketYield;
    if (yieldGap < -0.6) {
      score -= 14;
      reasons.push(`毛收益率低於同區約 ${Math.abs(yieldGap).toFixed(1)} 個百分點。`);
    } else if (yieldGap > 0.6) {
      reasons.push(`毛收益率高於同區約 ${yieldGap.toFixed(1)} 個百分點，需核對租金是否保守。`);
    } else {
      reasons.push('租金收益率接近同區水平。');
    }
  }
  if (ctx.walk > ctx.targetWalk) {
    score -= Math.min(18, (ctx.walk - ctx.targetWalk) * 2);
    reasons.push(`徒歩 ${ctx.walk} 分超過偏好上限 ${ctx.targetWalk} 分。`);
  } else {
    reasons.push(`徒歩 ${ctx.walk} 分在偏好範圍內。`);
  }
  if (ctx.age > ctx.targetAge) {
    score -= Math.min(16, (ctx.age - ctx.targetAge) * 0.8);
    reasons.push(`屋齡 ${ctx.age} 年高於偏好上限 ${ctx.targetAge} 年，需看修繕履歷。`);
  } else {
    reasons.push(`屋齡 ${ctx.age} 年在偏好範圍內。`);
  }
  return { score: Math.max(0, Math.min(100, Math.round(score))), band, reasons };
}

function evaluatePurchaseEligibility(context = {}) {
  const ctx = context.priceMan ? context : readPurchaseToolContext();
  const selectedPaymentYen = Math.max(0, Number(ctx.selectedPlan?.firstPayment) || 0);
  const status = purchaseQuestionValue('buyer-status', document.getElementById('purchase-tool-buyer-status')?.value || 'overseas');
  const purpose = purchaseQuestionValue('purpose', document.getElementById('purchase-tool-purpose')?.value || 'investment');
  const paymentPath = purchaseQuestionValue('payment-path', document.getElementById('purchase-tool-payment-path')?.value || 'cash');
  const cashRatio = Math.min(100, Math.max(0, Number(purchaseQuestionValue('cash-ratio', document.getElementById('purchase-tool-cash-ratio')?.value || 0)) || 0));
  const annualIncomeMan = Math.max(0, Number(ctx.annualIncomeMan) || 0);
  const debtMan = Math.max(0, Number(ctx.monthlyDebtMan) || 0);
  const recognizedIncomeMan = Math.max(0, Number(ctx.recognizedIncomeMan) || annualIncomeMan);
  const existingCount = Math.max(0, Number(ctx.existingCount) || 0);
  const timeline = purchaseQuestionValue('timeline', document.getElementById('purchase-tool-timeline')?.value || 'research');
  const docs = Array.from(document.querySelectorAll('[data-purchase-doc]'));
  const docsReady = docs.filter((item) => item.checked).length;
  const requiredCashRatio = ctx.priceMan > 0 ? (ctx.cashNeedMan / ctx.priceMan) * 100 : 0;
  const annualPaymentMan = selectedPaymentYen * 12 / 10000;
  const annualDebtMan = debtMan * 12;
  const dti = recognizedIncomeMan > 0 ? ((annualPaymentMan + annualDebtMan) / recognizedIncomeMan) * 100 : 999;
  const notes = [];
  let score = 100;

  if (cashRatio + 0.001 < requiredCashRatio) {
    score -= 28;
    notes.push(`自備資金約 ${cashRatio.toFixed(0)}%，低於含購置費用約 ${requiredCashRatio.toFixed(0)}% 的需求。`);
  } else {
    notes.push('自備資金比例已覆蓋頭期款與估算購置成本。');
  }
  if (paymentPath === 'unknown') {
    score -= 22;
    notes.push('付款路徑尚未確認，應先釐清全款、海外貸款或日本房貸。');
  } else if (paymentPath === 'japan-loan' && status === 'overseas') {
    score -= 24;
    notes.push('海外買家直接申請日本房貸通常需更嚴格審查，建議先做銀行預審或準備替代資金。');
  } else if (paymentPath === 'cash') {
    notes.push('全款或高比例自備款路徑清晰，成交可行性較高。');
  } else {
    notes.push('貸款路徑已初步指定，下一步需補銀行條件與文件。');
  }
  if (paymentPath !== 'cash') {
    if (recognizedIncomeMan <= 0) {
      score -= 18;
      notes.push('未填可證明收入，無法判斷貸款月付承受度。');
    } else if (dti > 45) {
      score -= 18;
      notes.push(`估算債務負擔約 ${dti.toFixed(0)}%，偏高，需降低貸款額或延長準備。`);
    } else if (dti > 35) {
      score -= 8;
      notes.push(`估算債務負擔約 ${dti.toFixed(0)}%，需由銀行進一步確認。`);
    } else {
      notes.push(`估算債務負擔約 ${dti.toFixed(0)}%，具備進一步預審基礎。`);
    }
  }
  if (ctx.liquidAssetsMan > 0) {
    notes.push(`金融資產 ${formatPurchaseToolMan(ctx.liquidAssetsMan)} 已按貸款年限折算約 ${formatPurchaseToolMan(ctx.assetConvertedIncomeMan)}/年作為承受力輔助。`);
  }
  if (docsReady < docs.length) {
    score -= Math.min(20, (docs.length - docsReady) * 5);
    notes.push(`文件準備 ${docsReady}/${docs.length}，仍需補齊身分、資金、收入或管理相關資料。`);
  } else {
    notes.push('文件與流程項目已勾選完成，可進入顧問初審。');
  }
  if (existingCount >= 2) {
    score -= 5;
    notes.push('已有多戶日本物件，需另行確認稅務、融資與管理安排。');
  }
  if (purpose === 'business') notes.push('若搭配經營管理或事業需求，需把公司、簽證與用途文件分開確認。');
  if (timeline === 'ready' && score < 75) notes.push('時程較近但條件未齊，建議先安排顧問盤點資料。');

  score = Math.max(0, Math.min(100, Math.round(score)));
  let title = '可進入顧問初審';
  let advice = '條件完整度高，可開始比對物件與付款流程。';
  if (score < 60) {
    title = '暫不建議直接出價';
    advice = '需先補齊資金、付款路徑或文件。';
  } else if (score < 78) {
    title = '需補件後再推進';
    advice = '可看房比較，但應同步完成資金與貸款確認。';
  }

  const titleEl = document.getElementById('purchase-tool-eligibility-title');
  const summaryEl = document.getElementById('purchase-tool-eligibility-summary');
  const listEl = document.getElementById('purchase-tool-eligibility-list');
  const questions = purchaseEligibilityQuestions();
  const currentStep = purchaseEligibilityCurrentStep();
  const complete = purchaseEligibilityIsComplete();
  if (!complete && questions.length) {
    const selectedAnswers = Array.from(document.querySelectorAll('[data-purchase-answer].is-selected'))
      .slice(0, currentStep + 1)
      .map((item) => String(item.textContent || '').trim())
      .filter(Boolean);
    if (titleEl) titleEl.textContent = '資格評估進行中';
    if (summaryEl) summaryEl.textContent = `已完成 ${currentStep}/${questions.length} 題。`;
    if (listEl) listEl.innerHTML = selectedAnswers.length
      ? selectedAnswers.map((answer) => `<li>${esc(answer)}</li>`).join('')
      : '<li>尚未選擇。</li>';
    return { score, title: '資格評估進行中', advice: '請完成目前題目。', notes, dti };
  }
  if (titleEl) titleEl.textContent = title;
  if (summaryEl) summaryEl.textContent = advice;
  if (listEl) listEl.innerHTML = notes.slice(0, 6).map((note) => `<li>${esc(note)}</li>`).join('');
  return { score, title, advice, notes, dti };
}

function buildPurchaseDecision(ctx, comparables, eligibility) {
  let score = 100;
  const rows = [];
  if (!ctx.priceMan || !ctx.areaSqm) {
    score -= 18;
    rows.push(['案件完整度', '待補', '房價與面積是比較單價、成本與現金流的核心欄位。']);
  } else {
    rows.push(['案件完整度', '已填', `${ctx.region || '區域未填'}｜${ctx.station || '車站未填'}｜${ctx.areaSqm}㎡`]);
  }
  if (ctx.netYield < 2.5) score -= 18;
  else if (ctx.netYield < 3.5) score -= 8;
  rows.push(['淨收益率', formatPurchasePercent(ctx.netYield), ctx.netYield >= 3.5 ? '出租收益具備進一步評估空間。' : '淨收益偏保守，需確認租金或壓低成本。']);
  if (ctx.cashflowYen < 0) score -= 15;
  rows.push(['月現金流', formatPurchaseToolYen(ctx.cashflowYen), ctx.cashflowYen >= 0 ? '貸款後仍有正現金流。' : '貸款後月現金流為負，需看自住目的或增值空間。']);
  rows.push([
    '貸款承受力',
    Number.isFinite(eligibility.dti) && eligibility.dti < 900 ? `DTI ${eligibility.dti.toFixed(0)}%` : '待補收入',
    ctx.recognizedIncomeMan > 0
      ? `可證明收入與金融資產折算後約 ${formatPurchaseToolMan(ctx.recognizedIncomeMan)}/年。`
      : '需補年收入、資產或貸款路徑。',
  ]);
  score = Math.min(score, Math.round((score + comparables.score + eligibility.score) / 3));
  rows.push(['可比評分', `${comparables.score} 分`, comparables.reasons[0] || '請補同區均價。']);
  rows.push(['資格文件', `${eligibility.score} 分`, eligibility.title]);
  return {
    score: Math.max(0, Math.min(100, Math.round(score))),
    rows,
    title: score >= 78 ? '可進入顧問初審' : score >= 60 ? '需補資料後再推進' : '暫不建議直接出價',
  };
}

function buildPurchaseDecisionAdvice(ctx, comparables, eligibility, decision) {
  if (!purchaseToolHasPrimaryCase(ctx)) return '尚未形成綜合建議。請先帶入已選案件，或補齊房價、面積與租金。';
  const level =
    decision.score >= 80 ? '高於基準，可進入顧問初審' :
    decision.score >= 65 ? '中高水平，適合持續比較並補資料' :
    decision.score >= 50 ? '中等水平，需校準價格、租金或資金條件' :
    '偏弱，暫不建議直接出價';
  const yieldText = ctx.netYield >= 3.5 ? '淨收益具備支撐' : ctx.netYield >= 2.5 ? '淨收益普通' : '淨收益偏弱';
  const cashText = ctx.cashflowYen >= 0 ? '貸款後現金流為正' : '貸款後現金流為負';
  const comparableText = comparables.band && comparables.band !== '待輸入'
    ? `可比位置為「${comparables.band}」`
    : '可比基準尚需補強';
  return `綜合水平：${level}。${comparableText}，${yieldText}，${cashText}；資格文件為「${eligibility.title}」。`;
}

function renderPurchaseDecisionRows(rows) {
  const tbody = document.getElementById('purchase-tool-decision-body');
  if (!tbody) return;
  tbody.innerHTML = rows.map((row) => `
    <tr>
      <td>${esc(row[0])}</td>
      <td><strong>${esc(row[1])}</strong></td>
      <td>${esc(row[2])}</td>
    </tr>
  `).join('');
}

function renderPurchaseSelectedComparables(rows) {
  const wrap = document.getElementById('purchase-tool-selected-comparables');
  if (!wrap) return;
  const list = (Array.isArray(rows) ? rows : readPurchaseSelectedCases()).filter(Boolean);
  const visible = list.slice(0, 8);
  wrap.replaceChildren();
  if (!visible.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = '尚未加入已選案件。可先在案件列表加入對比，再回到此處校準市場均價。';
    wrap.appendChild(empty);
    return;
  }
  visible.forEach((item, index) => {
    wrap.appendChild(buildPurchaseComparableCard(item, index));
  });
  if (list.length > visible.length) {
    const more = document.createElement('small');
    more.className = 'purchase-tool-comparable-more';
    more.textContent = `另有 ${list.length - visible.length} 筆已選案件已納入校準，可在右側列表移除後即時重算。`;
    wrap.appendChild(more);
  }
}

function buildPurchaseComparableCard(item, index) {
  const card = document.createElement('div');
  card.className = 'purchase-tool-comparable-card';
  const title = item.title || ('已選案件 ' + (index + 1));
  const head = document.createElement('div');
  head.className = 'purchase-tool-comparable-card-head';
  const copy = document.createElement('div');
  const strong = document.createElement('strong');
  const span = document.createElement('span');
  strong.textContent = title;
  span.textContent = item.region || item.station || '位置待補';
  copy.append(strong, span);
  const removeBtn = buildPurchaseComparableRemoveButton(item, index);
  head.append(copy, removeBtn);
  card.appendChild(head);
  const summary = document.createElement('small');
  const price = item.priceMan ? formatPurchaseToolMan(item.priceMan) : '價格待補';
  const area = item.areaSqm ? item.areaSqm.toFixed(1) + '㎡' : '面積待補';
  const unit = item.unitPrice ? item.unitPrice.toFixed(item.unitPrice >= 100 ? 0 : 1) + ' 萬/㎡' : '單價待補';
  const yieldText = item.yieldPct ? item.yieldPct.toFixed(1) + '%' : '收益率待補';
  summary.textContent = price + '｜' + area + '｜' + unit + '｜' + yieldText;
  card.appendChild(summary);
  return card;
}

function buildPurchaseComparableRemoveButton(item, index) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'purchase-tool-remove-case-btn';
  button.textContent = '移除';
  button.setAttribute('aria-label', '移除 ' + (item.title || '可比案件'));
  button.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    removePurchaseToolSelectedCase(item, index);
  });
  return button;
}

function purchaseToolImageUrl(item) {
  const gallery = Array.isArray(item.galleryUrls) ? item.galleryUrls : [];
  return item.thumbUrl || gallery[0] || '';
}

function renderPurchaseImageComparables(rows) {
  const wrap = document.getElementById('purchase-tool-image-comparables');
  if (!wrap) return;
  const list = (Array.isArray(rows) ? rows : readPurchaseSelectedCases()).filter(Boolean);
  wrap.replaceChildren();
  if (!list.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = '加入已選案件後，這裡會顯示圖片與單價對比。';
    wrap.appendChild(empty);
    return;
  }
  list.forEach((item, index) => {
    wrap.appendChild(buildPurchaseImageComparableCard(item, index));
  });
}

function buildPurchaseImageComparableCard(item, index) {
  const card = document.createElement('div');
  card.className = 'purchase-tool-image-card';
  const media = document.createElement('div');
  media.className = 'purchase-tool-image-card-media';
  const imgUrl = purchaseToolImageUrl(item);
  if (imgUrl) {
    const img = document.createElement('img');
    img.src = imgUrl;
    img.alt = item.title || '可比物件';
    img.loading = 'lazy';
    img.decoding = 'async';
    media.appendChild(img);
  } else {
    media.textContent = 'NO IMAGE';
  }
  if (index === 0) {
    const badge = document.createElement('b');
    badge.className = 'purchase-tool-image-card-badge';
    badge.textContent = '本案';
    media.appendChild(badge);
  }
  media.appendChild(buildPurchaseComparableRemoveButton(item, index));
  const body = document.createElement('div');
  body.className = 'purchase-tool-image-card-body';
  const title = document.createElement('strong');
  title.textContent = item.title || ('可比物件 ' + (index + 1));
  const region = document.createElement('span');
  region.textContent = item.region || item.station || '位置待補';
  const meta = document.createElement('span');
  meta.textContent = [
    '公寓大樓',
    item.age ? item.age + '年' : '',
    item.walk ? '徒歩' + item.walk + '分' : '',
  ].filter(Boolean).join('・') || '物件條件待補';
  const stats = document.createElement('div');
  stats.className = 'purchase-tool-image-card-stats';
  [
    [item.priceMan ? formatPurchaseToolMan(item.priceMan) : '價格待補', '總價'],
    [item.areaSqm ? item.areaSqm.toFixed(1) + '㎡' : '面積待補', '專有面積'],
    [item.unitPrice ? item.unitPrice.toFixed(item.unitPrice >= 100 ? 0 : 1) + ' 萬/㎡' : '單價待補', '單價'],
    [item.yieldPct ? item.yieldPct.toFixed(2) + '%' : '收益率待補', '淨收益率'],
  ].forEach(([value, label]) => {
    const cell = document.createElement('span');
    const strong = document.createElement('strong');
    const small = document.createElement('small');
    strong.textContent = value;
    small.textContent = label;
    cell.append(strong, small);
    stats.appendChild(cell);
  });
  body.append(title, region, meta, stats);
  card.append(media, body);
  return card;
}

function updatePurchaseDashboardVisuals(ctx, eligibility, decision) {
  const gauge = document.getElementById('purchase-tool-score-gauge');
  const scoreLabel = document.getElementById('purchase-tool-score-label');
  const yieldBar = document.getElementById('purchase-tool-bar-yield');
  const cashflowBar = document.getElementById('purchase-tool-bar-cashflow');
  const dtiBar = document.getElementById('purchase-tool-bar-dti');
  const yieldLabel = document.getElementById('purchase-tool-bar-yield-label');
  const cashflowLabel = document.getElementById('purchase-tool-bar-cashflow-label');
  const dtiLabel = document.getElementById('purchase-tool-bar-dti-label');
  if (gauge) gauge.style.setProperty('--purchase-score', String(clampPurchaseValue(decision.score)));
  if (scoreLabel) {
    scoreLabel.textContent =
      decision.score >= 80 ? '高適合度' :
      decision.score >= 65 ? '中高適合度' :
      decision.score >= 50 ? '中等適合度' :
      '需重新評估';
  }
  const yieldPct = clampPurchaseValue((ctx.netYield / 5) * 100);
  const cashflowPct = clampPurchaseValue(50 + (ctx.cashflowYen / 200000) * 50);
  const dtiPct = Number.isFinite(eligibility.dti) && eligibility.dti < 900 ? clampPurchaseValue(100 - (eligibility.dti / 45) * 100) : 0;
  if (yieldBar) yieldBar.style.setProperty('--bar-value', String(yieldPct));
  if (cashflowBar) cashflowBar.style.setProperty('--bar-value', String(cashflowPct));
  if (dtiBar) dtiBar.style.setProperty('--bar-value', String(dtiPct));
  if (yieldLabel) yieldLabel.textContent = formatPurchasePercent(ctx.netYield);
  if (cashflowLabel) cashflowLabel.textContent = formatPurchaseToolYen(ctx.cashflowYen);
  if (dtiLabel) dtiLabel.textContent = Number.isFinite(eligibility.dti) && eligibility.dti < 900 ? `DTI ${eligibility.dti.toFixed(0)}%` : '待補收入';
}

function updatePurchaseToolDataSourceStatus() {
  const el = document.getElementById('purchase-tool-data-source');
  if (!el) return;
  const rows = readPurchaseSelectedCases();
  const modal = document.getElementById('purchase-tool-modal');
  const calibrated = modal?.dataset.selectedMarketSynced === '1';
  const imported = modal?.dataset.selectedCaseImported === '1';
  if (rows.length && calibrated) {
    el.textContent = `已用 ${rows.length} 筆已選案件校準市場基準，儀表板按左側表單即時計算。`;
  } else if (rows.length && imported) {
    el.textContent = `已帶入已選案件資料，另有 ${rows.length} 筆可切到可比校準套用市場基準。`;
  } else if (rows.length) {
    el.textContent = `偵測到 ${rows.length} 筆已選案件；目前仍按左側表單與手動基準估算。`;
  } else {
    el.textContent = '尚未帶入案件或輸入條件，儀表板暫不計算。';
  }
}

function purchaseToolHasPrimaryCase(ctx) {
  return !!(ctx && (ctx.priceMan > 0 || ctx.areaSqm > 0 || ctx.rentMan > 0 || ctx.loanMan > 0));
}

function renderPurchaseToolEmptyState() {
  const fields = {
    'purchase-tool-main-payment': '—',
    'purchase-tool-main-payment-twd': '尚未計算',
    'purchase-tool-result-cash': '—',
    'purchase-tool-yield': '—',
    'purchase-tool-cashflow': '—',
    'purchase-tool-unit-price': '單價 —',
    'purchase-tool-dti': '—',
    'purchase-tool-net-yield': '—',
    'purchase-tool-comparable-band': '待輸入',
    'purchase-tool-decision-score': '待輸入',
    'purchase-tool-decision-title': '請先帶入已選案件，或手動輸入房價、面積與租金。',
    'purchase-tool-decision-advice': '尚未形成綜合建議。',
    'purchase-tool-bar-yield-label': '—',
    'purchase-tool-bar-cashflow-label': '—',
    'purchase-tool-bar-dti-label': '—',
    'purchase-tool-eligibility-title': '購房資格問答',
    'purchase-tool-eligibility-summary': '尚未輸入案件條件，暫不進行資格與資金承受度判斷。',
  };
  Object.entries(fields).forEach(([id, text]) => {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  });
  const dataSource = document.getElementById('purchase-tool-data-source');
  if (dataSource) dataSource.textContent = '尚未帶入案件或輸入條件，儀表板暫不計算。';
  const scoreLabel = document.getElementById('purchase-tool-score-label');
  if (scoreLabel) scoreLabel.textContent = '尚未計算';
  const gauge = document.getElementById('purchase-tool-score-gauge');
  if (gauge) gauge.style.setProperty('--purchase-score', '0');
  ['purchase-tool-bar-yield', 'purchase-tool-bar-cashflow', 'purchase-tool-bar-dti'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.style.setProperty('--bar-value', '0');
  });
  const tbody = document.getElementById('purchase-tool-decision-body');
  if (tbody) tbody.innerHTML = '<tr><td colspan="3">尚未輸入案件條件。</td></tr>';
  const schedule = document.getElementById('purchase-tool-schedule-list');
  if (schedule) schedule.innerHTML = '<p class="muted">請先輸入貸款條件。</p>';
  const eligibilityList = document.getElementById('purchase-tool-eligibility-list');
  if (eligibilityList) eligibilityList.innerHTML = '<li>尚未選擇。</li>';
  renderPurchaseSelectedComparables();
  renderPurchaseImageComparables();
  window.__purchaseToolLastSummary = '';
}

function calculatePurchaseTool() {
  const ctx = readPurchaseToolContext();
  if (!purchaseToolHasPrimaryCase(ctx)) {
    renderPurchaseToolEmptyState();
    return;
  }
  purchaseToolSetValue('purchase-tool-loan', Math.round(ctx.loanMan));
  const comparables = evaluatePurchaseComparables(ctx);
  const eligibility = evaluatePurchaseEligibility(ctx);
  const decision = buildPurchaseDecision(ctx, comparables, eligibility);

  const main = document.getElementById('purchase-tool-main-payment');
  const mainTwd = document.getElementById('purchase-tool-main-payment-twd');
  const cash = document.getElementById('purchase-tool-result-cash');
  const grossYield = document.getElementById('purchase-tool-yield');
  const cashflow = document.getElementById('purchase-tool-cashflow');
  const unitPrice = document.getElementById('purchase-tool-unit-price');
  const dti = document.getElementById('purchase-tool-dti');
  const netYield = document.getElementById('purchase-tool-net-yield');
  const band = document.getElementById('purchase-tool-comparable-band');
  const score = document.getElementById('purchase-tool-decision-score');
  const title = document.getElementById('purchase-tool-decision-title');
  const advice = document.getElementById('purchase-tool-decision-advice');

  if (main) main.textContent = formatPurchaseToolYen(ctx.selectedPlan.firstPayment);
  if (mainTwd) mainTwd.textContent = formatPurchaseToolTwdFromYen(ctx.selectedPlan.firstPayment, ctx.twdRate);
  if (cash) cash.textContent = formatPurchaseToolMan(ctx.cashNeedMan);
  if (grossYield) grossYield.textContent = formatPurchasePercent(ctx.grossYield);
  if (cashflow) cashflow.textContent = formatPurchaseToolYen(ctx.cashflowYen);
  if (unitPrice) unitPrice.textContent = `單價 ${ctx.unitPrice ? ctx.unitPrice.toFixed(1) : '0'} 萬日圓/㎡`;
  if (dti) dti.textContent = Number.isFinite(eligibility.dti) && eligibility.dti < 900 ? `${eligibility.dti.toFixed(0)}%` : '待補收入';
  if (netYield) netYield.textContent = formatPurchasePercent(ctx.netYield);
  if (band) band.textContent = comparables.band;
  if (score) score.textContent = `${decision.score} 分`;
  if (title) title.textContent = decision.title;
  if (advice) advice.textContent = buildPurchaseDecisionAdvice(ctx, comparables, eligibility, decision);
  updatePurchaseDashboardVisuals(ctx, eligibility, decision);
  updatePurchaseToolDataSourceStatus();

  renderPurchaseDecisionRows(decision.rows);
  renderPurchaseToolSchedule(ctx.selectedPlan);
  renderPurchaseSelectedComparables();
  renderPurchaseImageComparables();
  window.__purchaseToolLastSummary = buildPurchaseAdvisorSummary(ctx, comparables, eligibility, decision);
}

function buildPurchaseAdvisorSummary(ctx, comparables, eligibility, decision) {
  return [
    `日本購房決策工具摘要`,
    `案件：${ctx.title}｜${ctx.region || '-'}｜${ctx.station || '-'} 徒歩 ${ctx.walk || 0} 分`,
    `價格：${formatPurchaseToolMan(ctx.priceMan)}｜面積：${ctx.areaSqm || 0}㎡｜單價：${ctx.unitPrice ? ctx.unitPrice.toFixed(1) : '0'} 萬日圓/㎡`,
    `貸款：${formatPurchaseToolMan(ctx.loanMan)}｜月付：${formatPurchaseToolYen(ctx.selectedPlan.firstPayment)}｜總自備：${formatPurchaseToolMan(ctx.cashNeedMan)}`,
    `收益：毛收益率 ${formatPurchasePercent(ctx.grossYield)}｜淨收益率 ${formatPurchasePercent(ctx.netYield)}｜月現金流 ${formatPurchaseToolYen(ctx.cashflowYen)}`,
    `承受力：認列收入 ${formatPurchaseToolMan(ctx.recognizedIncomeMan)}/年｜金融資產 ${formatPurchaseToolMan(ctx.liquidAssetsMan)}｜DTI ${Number.isFinite(eligibility.dti) && eligibility.dti < 900 ? eligibility.dti.toFixed(0) + '%' : '待補'}`,
    `可比：${comparables.band}｜${comparables.score} 分｜${comparables.reasons.slice(0, 2).join('；')}`,
    `資格：${eligibility.score} 分｜${eligibility.title}`,
    `總評：${decision.score} 分｜${decision.title}`,
  ].join('\n');
}

async function copyPurchaseToolAdvisorSummary() {
  calculatePurchaseTool();
  const text = String(window.__purchaseToolLastSummary || '').trim();
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    const btn = document.activeElement;
    if (btn && btn.textContent) {
      const old = btn.textContent;
      btn.textContent = '已複製摘要';
      window.setTimeout(() => { btn.textContent = old; }, 1200);
    }
  } catch (_) {
    window.prompt('可複製以下顧問摘要', text);
  }
}

function normalizeSelectedCase(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const fields = raw.fields && typeof raw.fields === 'object' ? raw.fields : {};
  const title = raw.title || raw.title_zh_hant || raw.title_original || raw.name || fields.title || '';
  const region = raw.region || raw.region_zh || raw.jp_region_display_zh || raw.address_hint_zh || fields.address || '';
  const station = raw.station || raw.nearest_station || raw.transit || raw.transit_line_zh || fields.access || '';
  const priceText = [
    raw.price_man,
    raw.price_text_hant,
    raw.price_fx_hant,
    raw.price_hint,
    raw.price,
    raw.price_text,
    raw.total_price,
    fields.price_text_hant,
    fields.price,
  ].map((x) => String(x || '')).find(Boolean) || '';
  const areaText = [
    raw.area_text_hant,
    raw.exclusive_area_jp,
    raw.area,
    raw.area_sqm,
    raw.building_area,
    fields.area_text_hant,
    fields.area,
    fields.building_area,
  ].map((x) => String(x || '')).find(Boolean) || '';
  const rentText = [
    raw.rent,
    raw.estimated_rent,
    raw.annual_full_rent,
    raw.monthly_rent,
    fields.rent,
    fields.estimated_rent,
  ].map((x) => String(x || '')).find(Boolean) || '';
  const yieldText = [
    raw.yield_pct,
    raw.gross_yield,
    raw.gross_yield_text,
    raw.current_yield,
    raw.current_yield_text,
    raw.feature_tags_hant,
    fields.yield_pct,
    fields.gross_yield,
  ].flat().map((x) => String(x || '')).find((x) => /利回|收益|報酬|yield|%|％/i.test(x)) || '';
  const walkText = [station, raw.transit, raw.access_line_jp, raw.address_hint_zh, fields.access].map((x) => String(x || '')).join(' ');
  const ageText = [raw.age_text_hant, raw.building_age, fields.age_text_hant].map((x) => String(x || '')).join(' ');
  const priceMan = typeof raw.price_man === 'number' && raw.price_man > 0 ? raw.price_man : purchaseToolParseMan(priceText);
  const areaSqm = purchaseToolParseSqm(areaText);
  let rentMan = purchaseToolParseMan(rentText);
  const yieldPct = typeof raw.yield_pct === 'number' && raw.yield_pct > 0 ? raw.yield_pct : purchaseToolParsePercent(yieldText);
  const walk = Number(walkText.match(/(?:徒歩|步行|徒步)?\s*([0-9]{1,3})\s*分/)?.[1] || 0);
  const age = Number(ageText.match(/([0-9]{1,3})\s*年/)?.[1] || 0);
  const unitPrice = priceMan > 0 && areaSqm > 0 ? priceMan / areaSqm : 0;
  if (!rentMan && priceMan > 0 && yieldPct > 0) rentMan = purchaseToolRentFromYield(priceMan, yieldPct);
  const galleryUrls = Array.isArray(raw.gallery_urls) ? raw.gallery_urls.map((x) => String(x || '').trim()).filter(Boolean) : [];
  const thumbUrl = String(raw.thumb_url || raw.image_url || raw.hero_media_url || raw.thumbnail_url || galleryUrls[0] || '').trim();
  const stableKey = String(
    raw.case_key ||
    raw.source_item_id ||
    raw.content_id ||
    raw.item_url ||
    raw.url ||
    raw.canonical_url ||
    raw.slug ||
    ''
  ).trim();
  return { title, region, station, priceMan, areaSqm, rentMan, yieldPct, walk, age, unitPrice, thumbUrl, galleryUrls, stableKey };
}

function purchaseToolCaseFallbackKey(item) {
  if (!item) return '';
  return [
    item.title || '',
    item.region || '',
    item.station || '',
    item.priceMan || '',
    item.areaSqm || '',
    item.thumbUrl || '',
  ].join('|');
}

function purchaseToolSelectedCaseKey(item) {
  return String((item && item.stableKey) || '').trim() || purchaseToolCaseFallbackKey(item);
}

function readPurchaseSelectedRawCases() {
  try {
    const rows = JSON.parse(localStorage.getItem(PURCHASE_SELECTED_CASES_KEY) || '[]');
    return Array.isArray(rows) ? rows : [];
  } catch (_) {
    return [];
  }
}

function readPurchaseSelectedCases() {
  try {
    return readPurchaseSelectedRawCases().map(normalizeSelectedCase).filter(Boolean);
  } catch (_) {
    return [];
  }
}

function writePurchaseSelectedRawCases(rows) {
  const payload = Array.isArray(rows) ? rows : [];
  let oldValue = '';
  let newValue = '';
  try {
    oldValue = localStorage.getItem(PURCHASE_SELECTED_CASES_KEY) || '';
    newValue = JSON.stringify(payload);
    localStorage.setItem(PURCHASE_SELECTED_CASES_KEY, newValue);
  } catch (_) {}
  try {
    window.dispatchEvent(new StorageEvent('storage', {
      key: PURCHASE_SELECTED_CASES_KEY,
      oldValue,
      newValue,
      storageArea: localStorage,
    }));
  } catch (_) {}
  try {
    window.dispatchEvent(new CustomEvent('sclaw:selected-cases-updated', {
      detail: { items: payload, source: 'purchase-tool' },
    }));
  } catch (_) {}
  try {
    if ('BroadcastChannel' in window) {
      const channel = new BroadcastChannel(PURCHASE_SELECTED_CASES_CHANNEL_KEY);
      channel.postMessage({ type: 'selected-cases-updated', source: 'purchase-tool', items: payload });
      window.setTimeout(() => {
        try { channel.close(); } catch (_) {}
      }, 0);
    }
  } catch (_) {}
}

function removePurchaseToolSelectedCase(item, index) {
  const targetKey = purchaseToolSelectedCaseKey(item);
  const rows = readPurchaseSelectedRawCases();
  let removed = false;
  const next = rows.filter((raw, rawIndex) => {
    if (removed) return true;
    const normalized = normalizeSelectedCase(raw);
    const rawKey = purchaseToolSelectedCaseKey(normalized);
    const keyMatches = targetKey && rawKey === targetKey;
    const fallbackIndexMatches = !targetKey && rawIndex === index;
    if (keyMatches || fallbackIndexMatches) {
      removed = true;
      return false;
    }
    return true;
  });
  if (!removed && Number.isInteger(index) && index >= 0 && index < rows.length) {
    next.splice(index, 1);
    removed = true;
  }
  if (!removed) return;
  const modal = document.getElementById('purchase-tool-modal');
  writePurchaseSelectedRawCases(next);
  if (modal?.dataset.selectedMarketSynced === '1' && !next.length) {
    modal.dataset.selectedMarketSynced = '0';
    purchaseToolSetValue('purchase-tool-market-unit-price', '');
    purchaseToolSetValue('purchase-tool-market-yield', '');
    purchaseToolSetValue('purchase-tool-target-walk', '');
    purchaseToolSetValue('purchase-tool-target-age', '');
    updatePurchaseToolMarketFromRows([]);
  }
  syncPurchaseToolSelectedCases();
}

function purchaseToolDialog() {
  const modal = document.getElementById('purchase-tool-modal');
  return modal ? modal.querySelector('.purchase-tool-dialog') : document.querySelector('.purchase-tool-dialog');
}

function purchaseToolActivePaneKey() {
  return String(purchaseToolDialog()?.dataset.activePane || '');
}

function updatePurchaseToolMarketFromRows(rows) {
  const list = (Array.isArray(rows) ? rows : readPurchaseSelectedCases()).filter(Boolean);
  const source = document.getElementById('purchase-tool-market-source');
  if (!list.length) {
    if (source) source.textContent = '尚未找到已選案件。可先在案件列表加入對比，再回到購房工具校準。';
    return false;
  }
  const unitPrice = purchaseToolMedian(list.map((item) => item.unitPrice));
  const yieldPct = purchaseToolMedian(list.map((item) => item.yieldPct));
  const walk = purchaseToolMedian(list.map((item) => item.walk));
  const age = purchaseToolMedian(list.map((item) => item.age));
  if (unitPrice) purchaseToolSetValue('purchase-tool-market-unit-price', unitPrice.toFixed(unitPrice >= 100 ? 0 : 1));
  if (yieldPct) purchaseToolSetValue('purchase-tool-market-yield', yieldPct.toFixed(1));
  if (walk) purchaseToolSetValue('purchase-tool-target-walk', Math.round(walk));
  if (age) purchaseToolSetValue('purchase-tool-target-age', Math.round(age));
  if (source) {
    source.textContent = `已用 ${list.length} 筆已選案件校準${unitPrice ? `，均價約 ${unitPrice.toFixed(unitPrice >= 100 ? 0 : 1)} 萬日圓/㎡` : ''}${yieldPct ? `，收益率約 ${yieldPct.toFixed(1)}%` : ''}。`;
  }
  return true;
}

function syncPurchaseToolSelectedCases(options = {}) {
  const rows = readPurchaseSelectedCases();
  renderPurchaseSelectedComparables(rows);
  renderPurchaseImageComparables(rows);
  const modal = document.getElementById('purchase-tool-modal');
  const shouldCalibrate =
    !!options.calibrateMarket ||
    purchaseToolActivePaneKey() === 'comparables' ||
    modal?.dataset.selectedMarketSynced === '1';
  if (shouldCalibrate && rows.length) {
    updatePurchaseToolMarketFromRows(rows);
    if (modal) modal.dataset.selectedMarketSynced = '1';
  } else if (shouldCalibrate && !rows.length) {
    updatePurchaseToolMarketFromRows([]);
    if (modal?.dataset.selectedMarketSynced === '1') modal.dataset.selectedMarketSynced = '0';
  }
  calculatePurchaseTool();
  return rows;
}

function handlePurchaseToolSelectedCasesChanged(event) {
  if (event && event.type === 'storage' && event.key !== PURCHASE_SELECTED_CASES_KEY) return;
  window.clearTimeout(window.__purchaseToolSelectedCasesTimer);
  window.__purchaseToolSelectedCasesTimer = window.setTimeout(() => {
    syncPurchaseToolSelectedCases();
  }, 40);
}

function applyPurchaseToolSelectedCase() {
  const rows = readPurchaseSelectedCases();
  const item = rows[0] || null;
  if (!item) {
    window.alert('尚未找到已選案件。可先在案件列表加入對比，再回到購房工具帶入。');
    return;
  }
  if (item.title) purchaseToolSetValue('purchase-tool-case-title', item.title);
  if (item.region) purchaseToolSetValue('purchase-tool-region', item.region);
  if (item.station) purchaseToolSetValue('purchase-tool-station', item.station);
  if (item.walk) purchaseToolSetValue('purchase-tool-walk', Math.round(item.walk));
  if (item.priceMan) purchaseToolSetValue('purchase-tool-price', Math.round(item.priceMan));
  if (item.areaSqm) purchaseToolSetValue('purchase-tool-area', item.areaSqm);
  if (item.rentMan) purchaseToolSetValue('purchase-tool-rent', item.rentMan);
  if (item.age) purchaseToolSetValue('purchase-tool-age', Math.round(item.age));
  purchaseToolSetDefaultIfEmpty('purchase-tool-down-rate', 30);
  purchaseToolSetDefaultIfEmpty('purchase-tool-years', 30);
  purchaseToolSetDefaultIfEmpty('purchase-tool-rate', 1.8);
  purchaseToolSetDefaultIfEmpty('purchase-tool-twd-rate', 20.8);
  purchaseToolSetDefaultIfEmpty('purchase-tool-cost-rate', 7);
  purchaseToolSetDefaultIfEmpty('purchase-tool-broker-rate', 3.3);
  purchaseToolSetDefaultIfEmpty('purchase-tool-tax-rate', 2.1);
  purchaseToolSetDefaultIfEmpty('purchase-tool-reserve-rate', 8);
  purchaseToolSetDefaultIfEmpty('purchase-tool-monthly-fee', 0);
  purchaseToolSetDefaultIfEmpty('purchase-tool-annual-tax', 0);
  purchaseToolSetDefaultIfEmpty('purchase-tool-buyer-status', 'overseas');
  purchaseToolSetDefaultIfEmpty('purchase-tool-purpose', 'investment');
  purchaseToolSetDefaultIfEmpty('purchase-tool-payment-path', 'cash');
  purchaseToolSetDefaultIfEmpty('purchase-tool-cash-ratio', 40);
  purchaseToolSetDefaultIfEmpty('purchase-tool-income', 0);
  purchaseToolSetDefaultIfEmpty('purchase-tool-debt', 0);
  const modal = document.getElementById('purchase-tool-modal');
  const calibrated = updatePurchaseToolMarketFromRows(rows);
  if (modal) {
    modal.dataset.selectedCaseImported = '1';
    if (calibrated) modal.dataset.selectedMarketSynced = '1';
  }
  setPurchaseToolActivePane('case');
  calculatePurchaseTool();
}

function applyPurchaseToolSelectedCaseMarket() {
  const rows = readPurchaseSelectedCases();
  if (!rows.length) {
    window.alert('尚未找到已選案件。可先在案件列表加入對比，再回到購房工具校準。');
    return;
  }
  updatePurchaseToolMarketFromRows(rows);
  const modal = document.getElementById('purchase-tool-modal');
  if (modal) modal.dataset.selectedMarketSynced = '1';
  setPurchaseToolActivePane('comparables');
  syncPurchaseToolSelectedCases({ calibrateMarket: true });
}

function applyPurchaseToolPreset() {
  purchaseToolSetValue('purchase-tool-case-title', '東京核心區投資公寓');
  purchaseToolSetValue('purchase-tool-region', '東京都新宿區');
  purchaseToolSetValue('purchase-tool-station', '新宿駅');
  purchaseToolSetValue('purchase-tool-walk', 7);
  purchaseToolSetValue('purchase-tool-property-type', 'mansion');
  purchaseToolSetValue('purchase-tool-area', 42.5);
  purchaseToolSetValue('purchase-tool-age', 15);
  purchaseToolSetValue('purchase-tool-floor', '5F / 10F');
  purchaseToolSetValue('purchase-tool-mode', 'house');
  purchaseToolSetValue('purchase-tool-price', 6200);
  purchaseToolSetValue('purchase-tool-down-rate', 35);
  purchaseToolSetValue('purchase-tool-years', 30);
  purchaseToolSetValue('purchase-tool-rate', 1.65);
  purchaseToolSetValue('purchase-tool-repay', 'equal-payment');
  purchaseToolSetValue('purchase-tool-twd-rate', 20.8);
  purchaseToolSetValue('purchase-tool-cost-rate', 7.5);
  purchaseToolSetValue('purchase-tool-broker-rate', 3.3);
  purchaseToolSetValue('purchase-tool-tax-rate', 2.1);
  purchaseToolSetValue('purchase-tool-legal-fee', 65);
  purchaseToolSetValue('purchase-tool-monthly-fee', 4.6);
  purchaseToolSetValue('purchase-tool-rent', 22);
  purchaseToolSetValue('purchase-tool-reserve-rate', 8);
  purchaseToolSetValue('purchase-tool-annual-tax', 22);
  purchaseToolSetValue('purchase-tool-market-unit-price', 145);
  purchaseToolSetValue('purchase-tool-market-yield', 4.1);
  purchaseToolSetValue('purchase-tool-target-walk', 10);
  purchaseToolSetValue('purchase-tool-target-age', 25);
  purchaseToolSetValue('purchase-tool-buyer-status', 'overseas');
  purchaseToolSetValue('purchase-tool-purpose', 'investment');
  purchaseToolSetValue('purchase-tool-payment-path', 'overseas-loan');
  purchaseToolSetValue('purchase-tool-cash-ratio', 45);
  purchaseToolSetValue('purchase-tool-income', 1200);
  purchaseToolSetValue('purchase-tool-debt', 0);
  purchaseToolSetValue('purchase-tool-assets', 1800);
  purchaseToolSetValue('purchase-tool-existing-property', 0);
  purchaseToolSetValue('purchase-tool-timeline', 'planning');
  document.querySelectorAll('[data-purchase-doc]').forEach((item, idx) => {
    item.checked = idx < 4;
  });
  const modal = document.getElementById('purchase-tool-modal');
  if (modal) {
    delete modal.dataset.selectedCaseImported;
    delete modal.dataset.selectedMarketSynced;
  }
  setPurchaseQuestionStep(0);
  refreshPurchaseQuestionAnswers();
  calculatePurchaseTool();
}

function resetPurchaseTool() {
  purchaseToolSetValue('purchase-tool-case-title', '');
  purchaseToolSetValue('purchase-tool-region', '');
  purchaseToolSetValue('purchase-tool-station', '');
  purchaseToolSetValue('purchase-tool-walk', '');
  purchaseToolSetValue('purchase-tool-property-type', 'mansion');
  purchaseToolSetValue('purchase-tool-area', '');
  purchaseToolSetValue('purchase-tool-age', '');
  purchaseToolSetValue('purchase-tool-floor', '');
  purchaseToolSetValue('purchase-tool-mode', 'house');
  purchaseToolSetValue('purchase-tool-price', '');
  purchaseToolSetValue('purchase-tool-loan', '');
  purchaseToolSetValue('purchase-tool-down-rate', '');
  purchaseToolSetValue('purchase-tool-years', '');
  purchaseToolSetValue('purchase-tool-rate', '');
  purchaseToolSetValue('purchase-tool-repay', 'equal-payment');
  purchaseToolSetValue('purchase-tool-twd-rate', '');
  purchaseToolSetValue('purchase-tool-cost-rate', '');
  purchaseToolSetValue('purchase-tool-broker-rate', '');
  purchaseToolSetValue('purchase-tool-tax-rate', '');
  purchaseToolSetValue('purchase-tool-legal-fee', '');
  purchaseToolSetValue('purchase-tool-monthly-fee', '');
  purchaseToolSetValue('purchase-tool-rent', '');
  purchaseToolSetValue('purchase-tool-reserve-rate', '');
  purchaseToolSetValue('purchase-tool-annual-tax', '');
  purchaseToolSetValue('purchase-tool-market-unit-price', '');
  purchaseToolSetValue('purchase-tool-market-yield', '');
  purchaseToolSetValue('purchase-tool-target-walk', '');
  purchaseToolSetValue('purchase-tool-target-age', '');
  purchaseToolSetValue('purchase-tool-buyer-status', '');
  purchaseToolSetValue('purchase-tool-purpose', '');
  purchaseToolSetValue('purchase-tool-payment-path', '');
  purchaseToolSetValue('purchase-tool-cash-ratio', 0);
  purchaseToolSetValue('purchase-tool-income', '');
  purchaseToolSetValue('purchase-tool-debt', '');
  purchaseToolSetValue('purchase-tool-assets', '');
  purchaseToolSetValue('purchase-tool-existing-property', '');
  purchaseToolSetValue('purchase-tool-timeline', '');
  document.querySelectorAll('[data-purchase-doc]').forEach((item, idx) => {
    item.checked = false;
  });
  const modal = document.getElementById('purchase-tool-modal');
  if (modal) {
    delete modal.dataset.selectedCaseImported;
    delete modal.dataset.selectedMarketSynced;
  }
  setPurchaseQuestionStep(0);
  refreshPurchaseQuestionAnswers();
  setPurchaseToolActivePane('case');
  calculatePurchaseTool();
}

function lockBodyScrollForPurchaseTool() {
  if (document.body.classList.contains('purchase-tool-scroll-lock')) return;
  if (document.body.classList.contains('support-chat-scroll-lock')) return;
  if (document.body.classList.contains('support-selected-cases-scroll-lock')) return;
  document.body.classList.add('purchase-tool-scroll-lock');
  try {
    const y = window.scrollY || window.pageYOffset || 0;
    document.body.dataset.purchaseToolScrollY = String(y);
    document.body.style.position = 'fixed';
    document.body.style.top = `-${y}px`;
    document.body.style.left = '0';
    document.body.style.right = '0';
    document.body.style.width = '100%';
  } catch (_) {}
}

function unlockBodyScrollForPurchaseTool() {
  if (!document.body.classList.contains('purchase-tool-scroll-lock')) return;
  document.body.classList.remove('purchase-tool-scroll-lock');
  try {
    const hasStoredScroll = Object.prototype.hasOwnProperty.call(document.body.dataset, 'purchaseToolScrollY');
    const y = parseInt(String(document.body.dataset.purchaseToolScrollY || '0'), 10) || 0;
    document.body.style.position = '';
    document.body.style.top = '';
    document.body.style.left = '';
    document.body.style.right = '';
    document.body.style.width = '';
    delete document.body.dataset.purchaseToolScrollY;
    if (hasStoredScroll) window.scrollTo(0, y);
  } catch (_) {}
}

function setPurchaseToolModalOpen(open, options = {}) {
  const modal = document.getElementById('purchase-tool-modal');
  const btn = document.getElementById('purchase-tool-toggle');
  const widget = document.getElementById('support-chat-widget');
  if (!modal) return;
  const shouldOpen = !!open;
  const root = document.documentElement;
  const scrollbarComp = Math.max(0, window.innerWidth - document.documentElement.clientWidth);
  if (shouldOpen && !(options && options.skipExclusive)) closeFloatingPanelsExcept('purchase');
  modal.style.display = shouldOpen ? 'flex' : 'none';
  modal.setAttribute('aria-hidden', shouldOpen ? 'false' : 'true');
  root.classList.toggle('purchase-tool-scroll-lock', shouldOpen);
  if (shouldOpen) {
    document.body.style.setProperty('--purchase-tool-scrollbar-comp', `${scrollbarComp}px`);
    lockBodyScrollForPurchaseTool();
  } else {
    document.body.style.removeProperty('--purchase-tool-scrollbar-comp');
    unlockBodyScrollForPurchaseTool();
  }
  if (widget) widget.classList.toggle('is-purchase-tool-open', shouldOpen);
  if (btn) {
    btn.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
    btn.classList.toggle('is-active', shouldOpen);
  }
  if (shouldOpen) {
    setPurchaseToolActivePane(document.querySelector('.purchase-tool-tab.is-active')?.dataset.purchaseTab || 'case');
    calculatePurchaseTool();
    const first = document.getElementById('purchase-tool-case-title');
    window.setTimeout(() => {
      try { first && first.focus({ preventScroll: true }); } catch (_) {}
    }, 80);
  } else if (options && options.restoreFocus && btn) {
    try { btn.focus({ preventScroll: true }); } catch (_) {}
  }
}

function togglePurchaseTool(force, options = {}) {
  const modal = document.getElementById('purchase-tool-modal');
  if (!modal) return;
  const open = typeof force === 'boolean' ? force : modal.style.display === 'none' || !modal.style.display;
  setPurchaseToolModalOpen(open, { restoreFocus: !open, ...(options || {}) });
}

function installPurchaseTool() {
  const modal = document.getElementById('purchase-tool-modal');
  if (!modal || modal.dataset.bound === '1') return;
  if (modal.parentElement && modal.parentElement.id === 'support-chat-widget') {
    document.body.appendChild(modal);
  }
  modal.dataset.bound = '1';
  modal.querySelectorAll('.purchase-tool-backdrop, .purchase-tool-head .ui-close-btn').forEach((btn) => {
    btn.addEventListener('click', () => setPurchaseToolModalOpen(false, { restoreFocus: true }));
  });
  modal.querySelectorAll('input, select').forEach((el) => {
    el.addEventListener('input', calculatePurchaseTool);
    el.addEventListener('change', calculatePurchaseTool);
  });
  modal.querySelectorAll('[data-purchase-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const key = String(btn.dataset.purchaseTab || 'case');
      setPurchaseToolActivePane(key);
      if (key === 'comparables') {
        syncPurchaseToolSelectedCases({ calibrateMarket: true });
      } else {
        calculatePurchaseTool();
      }
    });
  });
  modal.querySelectorAll('[data-purchase-answer]').forEach((btn) => {
    btn.addEventListener('click', () => setPurchaseQuestionAnswer(btn));
    btn.setAttribute('aria-pressed', btn.classList.contains('is-selected') ? 'true' : 'false');
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && modal.style.display !== 'none') setPurchaseToolModalOpen(false, { restoreFocus: true });
  });
  window.addEventListener('storage', handlePurchaseToolSelectedCasesChanged);
  window.addEventListener('sclaw:selected-cases-updated', handlePurchaseToolSelectedCasesChanged);
  setPurchaseToolActivePane('case');
  refreshPurchaseQuestionAnswers();
  calculatePurchaseTool();
}

document.addEventListener('DOMContentLoaded', function () {
  installPurchaseTool();
});
