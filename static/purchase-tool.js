// Shared purchase tool behavior for the standalone /purchase-tools page.
function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function closeFloatingPanelsExcept() {}

    function purchaseToolNumber(id, fallback = 0) {
      const el = document.getElementById(id);
      const raw = el ? String(el.value || '').replace(/,/g, '').trim() : '';
      const n = Number(raw);
      return Number.isFinite(n) ? n : fallback;
    }

    function purchaseToolSetValue(id, value) {
      const el = document.getElementById(id);
      if (el) {
        el.value = String(value);
        if (el.tagName === 'INPUT' && String(el.type || '').toLowerCase() === 'hidden') {
          el.setAttribute('value', String(value));
        }
      }
    }

    function setPurchaseToolActivePane(key) {
      const modal = document.getElementById('purchase-tool-modal');
      const dialog = modal ? modal.querySelector('.purchase-tool-dialog') : document.querySelector('.purchase-tool-dialog');
      const paneKey = String(key || 'loan');
      if (dialog) dialog.dataset.activePane = paneKey;
      document.querySelectorAll('[data-purchase-tab]').forEach((item) => {
        item.classList.toggle('is-active', String(item.dataset.purchaseTab || '') === paneKey);
      });
      document.querySelectorAll('[data-purchase-pane]').forEach((pane) => {
        pane.classList.toggle('is-active', String(pane.dataset.purchasePane || '') === paneKey);
      });
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
      const scoreEl = document.getElementById('purchase-tool-eligibility-score');
      const adviceEl = document.getElementById('purchase-tool-eligibility-advice');
      const progress = complete ? 100 : ((step + 1) / questions.length) * 100;
      if (stepLabel) stepLabel.textContent = complete ? '評估完成' : `第 ${step + 1} 題 / 共 ${questions.length} 題`;
      if (stepTitle) stepTitle.textContent = complete ? '查看結果與文件準備' : purchaseEligibilityStepTitle(step);
      if (progressBar) progressBar.style.width = `${Math.max(8, Math.min(100, progress))}%`;
      if (prevBtn) prevBtn.disabled = !complete && step <= 0;
      if (!complete) {
        if (scoreEl) scoreEl.textContent = `第 ${step + 1} 題`;
        if (adviceEl) adviceEl.textContent = `請選擇：${purchaseEligibilityStepTitle(step)}`;
      }
    }

    function goPurchaseQuestionStep(delta) {
      const questions = purchaseEligibilityQuestions();
      if (!questions.length) return;
      const current = purchaseEligibilityCurrentStep();
      if (purchaseEligibilityIsComplete()) {
        setPurchaseQuestionStep(questions.length - 1);
        calculatePurchaseTool();
        return;
      }
      setPurchaseQuestionStep(current + (Number(delta) || 0));
      calculatePurchaseTool();
    }

    function resetPurchaseEligibilityWizard() {
      purchaseToolSetValue('purchase-tool-buyer-status', 'overseas');
      purchaseToolSetValue('purchase-tool-purpose', 'investment');
      purchaseToolSetValue('purchase-tool-payment-path', 'cash');
      purchaseToolSetValue('purchase-tool-cash-ratio', 40);
      purchaseToolSetValue('purchase-tool-income', 900);
      purchaseToolSetValue('purchase-tool-debt', 0);
      purchaseToolSetValue('purchase-tool-existing-property', 0);
      purchaseToolSetValue('purchase-tool-timeline', 'research');
      document.querySelectorAll('[data-purchase-doc]').forEach((item, idx) => {
        item.checked = idx < 2;
      });
      setPurchaseQuestionStep(0);
      refreshPurchaseQuestionAnswers();
      calculatePurchaseTool();
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
      calculatePurchaseTool();
      syncPurchaseHiddenFieldsFromSelectedQuestions();
      window.setTimeout(syncPurchaseHiddenFieldsFromSelectedQuestions, 0);
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

    function purchaseQuestionSelectedButton(key) {
      return document.querySelector(`[data-purchase-answer="${String(key || '')}"].is-selected`);
    }

    function purchaseQuestionValue(key, fallback = '') {
      const btn = purchaseQuestionSelectedButton(key);
      if (btn && Object.prototype.hasOwnProperty.call(btn.dataset, 'purchaseValue')) return String(btn.dataset.purchaseValue || '');
      return String(fallback || '');
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
        years: months / 12,
        firstPayment,
        averagePayment: totalPayment / months,
        lastPayment,
        monthlyDecline,
        totalPayment,
        totalInterest: Math.max(0, totalPayment - principalYen),
        schedule,
      };
    }

    function renderPurchaseToolSchedule(plan, ratePer100Yen) {
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

    function evaluatePurchaseEligibility(context = {}) {
      const priceMan = Math.max(0, Number(context.priceMan) || purchaseToolNumber('purchase-tool-price', 0));
      const loanMan = Math.max(0, Number(context.loanMan) || purchaseToolNumber('purchase-tool-loan', 0));
      const costRate = Math.max(0, Number(context.costRate) || purchaseToolNumber('purchase-tool-cost-rate', 7));
      const selectedPaymentYen = Math.max(0, Number(context.selectedPaymentYen) || 0);
      const status = purchaseQuestionValue('buyer-status', document.getElementById('purchase-tool-buyer-status')?.value || 'overseas');
      const purpose = purchaseQuestionValue('purpose', document.getElementById('purchase-tool-purpose')?.value || 'investment');
      const paymentPath = purchaseQuestionValue('payment-path', document.getElementById('purchase-tool-payment-path')?.value || 'cash');
      const cashRatio = Math.min(100, Math.max(0, Number(purchaseQuestionValue('cash-ratio', document.getElementById('purchase-tool-cash-ratio')?.value || 0)) || 0));
      const incomeBtn = purchaseQuestionSelectedButton('income-profile');
      const annualIncomeMan = Math.max(0, Number(incomeBtn ? incomeBtn.dataset.purchaseIncome : purchaseToolNumber('purchase-tool-income', 0)) || 0);
      const debtMan = Math.max(0, Number(incomeBtn ? incomeBtn.dataset.purchaseDebt : purchaseToolNumber('purchase-tool-debt', 0)) || 0);
      const existingCount = Math.max(0, purchaseToolNumber('purchase-tool-existing-property', 0));
      const timeline = purchaseQuestionValue('timeline', document.getElementById('purchase-tool-timeline')?.value || 'research');
      const docs = Array.from(document.querySelectorAll('[data-purchase-doc]'));
      const docsReady = docs.filter((item) => item.checked).length;
      const requiredCashRatio = priceMan > 0 ? ((Math.max(0, priceMan - loanMan) + priceMan * costRate / 100) / priceMan) * 100 : 0;
      const annualPaymentMan = selectedPaymentYen * 12 / 10000;
      const annualDebtMan = debtMan * 12;
      const dti = annualIncomeMan > 0 ? ((annualPaymentMan + annualDebtMan) / annualIncomeMan) * 100 : 999;
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
        if (annualIncomeMan <= 0) {
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
      if (purpose === 'business') {
        notes.push('若搭配經營管理或事業需求，需把公司、簽證與用途文件分開確認。');
      }
      if (timeline === 'ready' && score < 75) {
        notes.push('時程較近但條件未齊，建議先安排顧問盤點資料。');
      }

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

      const questions = purchaseEligibilityQuestions();
      const currentStep = purchaseEligibilityCurrentStep();
      const complete = purchaseEligibilityIsComplete();
      const scoreEl = document.getElementById('purchase-tool-eligibility-score');
      const adviceEl = document.getElementById('purchase-tool-eligibility-advice');
      const titleEl = document.getElementById('purchase-tool-eligibility-title');
      const summaryEl = document.getElementById('purchase-tool-eligibility-summary');
      const listEl = document.getElementById('purchase-tool-eligibility-list');
      if (!complete && questions.length) {
        const selectedAnswers = Array.from(document.querySelectorAll('[data-purchase-answer].is-selected'))
          .slice(0, currentStep + 1)
          .map((item) => String(item.textContent || '').trim())
          .filter(Boolean);
        if (scoreEl) scoreEl.textContent = `第 ${currentStep + 1} 題`;
        if (adviceEl) adviceEl.textContent = `請選擇：${purchaseEligibilityStepTitle(currentStep)}`;
        if (titleEl) titleEl.textContent = '資格評估進行中';
        if (summaryEl) summaryEl.textContent = `已完成 ${currentStep}/${questions.length} 題。`;
        if (listEl) {
          listEl.innerHTML = selectedAnswers.length
            ? selectedAnswers.map((answer) => `<li>${esc(answer)}</li>`).join('')
            : '<li>尚未選擇。</li>';
        }
        renderPurchaseEligibilityProgress();
        return { score, title: '資格評估進行中', advice: '請完成目前題目。', notes };
      }
      if (scoreEl) scoreEl.textContent = `資格評估 ${score} 分`;
      if (adviceEl) adviceEl.textContent = title;
      if (titleEl) titleEl.textContent = title;
      if (summaryEl) summaryEl.textContent = advice;
      if (listEl) listEl.innerHTML = notes.slice(0, 6).map((note) => `<li>${esc(note)}</li>`).join('');
      return { score, title, advice, notes };
    }

    function calculatePurchaseTool() {
      const mode = String(document.getElementById('purchase-tool-mode')?.value || 'house');
      const priceMan = Math.max(0, purchaseToolNumber('purchase-tool-price', 0));
      const downRate = Math.min(100, Math.max(0, purchaseToolNumber('purchase-tool-down-rate', 30)));
      const manualLoanMan = Math.max(0, purchaseToolNumber('purchase-tool-loan', 0));
      const loanMan = mode === 'loan' ? manualLoanMan : Math.max(0, priceMan * (100 - downRate) / 100);
      const derivedDownMan = Math.max(0, priceMan - loanMan);
      const years = Math.max(1, purchaseToolNumber('purchase-tool-years', 30));
      const annualRate = Math.max(0, purchaseToolNumber('purchase-tool-rate', 1.8));
      const twdRate = Math.max(0, purchaseToolNumber('purchase-tool-twd-rate', 20.8));
      const costRate = Math.max(0, purchaseToolNumber('purchase-tool-cost-rate', 7));
      const monthlyFeeMan = Math.max(0, purchaseToolNumber('purchase-tool-monthly-fee', 0));
      const rentMan = Math.max(0, purchaseToolNumber('purchase-tool-rent', 0));
      const reserveRate = Math.min(100, Math.max(0, purchaseToolNumber('purchase-tool-reserve-rate', 0)));
      const selectedType = String(document.getElementById('purchase-tool-repay')?.value || 'equal-payment');
      const equalPayment = buildPurchaseToolLoanPlan(loanMan, annualRate, years, 'equal-payment');
      const equalPrincipal = buildPurchaseToolLoanPlan(loanMan, annualRate, years, 'equal-principal');
      const selectedPlan = selectedType === 'equal-principal' ? equalPrincipal : equalPayment;
      const costMan = priceMan * costRate / 100;
      const cashNeedMan = derivedDownMan + costMan;
      const grossYield = priceMan > 0 ? (rentMan * 12 / priceMan) * 100 : 0;
      const reserveYen = rentMan * 10000 * reserveRate / 100;
      const cashflowYen = rentMan * 10000 - selectedPlan.firstPayment - monthlyFeeMan * 10000 - reserveYen;

      purchaseToolSetValue('purchase-tool-loan', Math.round(loanMan));
      const main = document.getElementById('purchase-tool-main-payment');
      const mainTwd = document.getElementById('purchase-tool-main-payment-twd');
      const resultLoan = document.getElementById('purchase-tool-result-loan');
      const resultDown = document.getElementById('purchase-tool-result-down');
      const resultCost = document.getElementById('purchase-tool-result-cost');
      const resultCash = document.getElementById('purchase-tool-result-cash');
      if (main) main.textContent = formatPurchaseToolYen(selectedPlan.firstPayment);
      if (mainTwd) mainTwd.textContent = formatPurchaseToolTwdFromYen(selectedPlan.firstPayment, twdRate);
      if (resultLoan) resultLoan.textContent = formatPurchaseToolMan(loanMan);
      if (resultDown) resultDown.textContent = formatPurchaseToolMan(derivedDownMan);
      if (resultCost) resultCost.textContent = formatPurchaseToolMan(costMan);
      if (resultCash) resultCash.textContent = formatPurchaseToolMan(cashNeedMan);

      const tbody = document.getElementById('purchase-tool-compare-body');
      if (tbody) {
        tbody.innerHTML = `
          <tr><td>首月月付</td><td>${formatPurchaseToolYen(equalPayment.firstPayment)}</td><td>${formatPurchaseToolYen(equalPrincipal.firstPayment)}</td></tr>
          <tr><td>月均付款</td><td>${formatPurchaseToolYen(equalPayment.averagePayment)}</td><td>${formatPurchaseToolYen(equalPrincipal.averagePayment)}</td></tr>
          <tr><td>每月遞減</td><td>0 日圓</td><td>${formatPurchaseToolYen(equalPrincipal.monthlyDecline)}</td></tr>
          <tr><td>利息總額</td><td>${formatPurchaseToolMan(equalPayment.totalInterest / 10000)}</td><td>${formatPurchaseToolMan(equalPrincipal.totalInterest / 10000)}</td></tr>
          <tr><td>還款總額</td><td>${formatPurchaseToolMan(equalPayment.totalPayment / 10000)}</td><td>${formatPurchaseToolMan(equalPrincipal.totalPayment / 10000)}</td></tr>
        `;
      }

      const yieldEl = document.getElementById('purchase-tool-yield');
      const cashflowEl = document.getElementById('purchase-tool-cashflow');
      if (yieldEl) yieldEl.textContent = `毛收益率 ${grossYield.toFixed(2)}%`;
      if (cashflowEl) cashflowEl.textContent = `月現金流 ${formatPurchaseToolYen(cashflowYen)}`;
      evaluatePurchaseEligibility({ priceMan, loanMan, costRate, selectedPaymentYen: selectedPlan.firstPayment });
      renderPurchaseToolSchedule(selectedPlan, twdRate);
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
        setPurchaseToolActivePane(document.querySelector('.purchase-tool-tab.is-active')?.dataset.purchaseTab || 'loan');
        calculatePurchaseTool();
        const first = document.getElementById('purchase-tool-price');
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

    function applyPurchaseToolPreset() {
      purchaseToolSetValue('purchase-tool-mode', 'house');
      purchaseToolSetValue('purchase-tool-price', 6200);
      purchaseToolSetValue('purchase-tool-down-rate', 35);
      purchaseToolSetValue('purchase-tool-years', 30);
      purchaseToolSetValue('purchase-tool-rate', 1.65);
      purchaseToolSetValue('purchase-tool-repay', 'equal-payment');
      purchaseToolSetValue('purchase-tool-twd-rate', 20.8);
      purchaseToolSetValue('purchase-tool-cost-rate', 7.5);
      purchaseToolSetValue('purchase-tool-monthly-fee', 4.6);
      purchaseToolSetValue('purchase-tool-rent', 22);
      purchaseToolSetValue('purchase-tool-reserve-rate', 8);
      purchaseToolSetValue('purchase-tool-buyer-status', 'overseas');
      purchaseToolSetValue('purchase-tool-purpose', 'investment');
      purchaseToolSetValue('purchase-tool-payment-path', 'overseas-loan');
      purchaseToolSetValue('purchase-tool-cash-ratio', 45);
      purchaseToolSetValue('purchase-tool-income', 1200);
      purchaseToolSetValue('purchase-tool-debt', 0);
      purchaseToolSetValue('purchase-tool-existing-property', 0);
      purchaseToolSetValue('purchase-tool-timeline', 'planning');
      document.querySelectorAll('[data-purchase-doc]').forEach((item, idx) => {
        item.checked = idx < 4;
      });
      setPurchaseQuestionStep(0);
      refreshPurchaseQuestionAnswers();
      calculatePurchaseTool();
    }

    function resetPurchaseTool() {
      purchaseToolSetValue('purchase-tool-mode', 'house');
      purchaseToolSetValue('purchase-tool-price', 4800);
      purchaseToolSetValue('purchase-tool-loan', 3360);
      purchaseToolSetValue('purchase-tool-down-rate', 30);
      purchaseToolSetValue('purchase-tool-years', 30);
      purchaseToolSetValue('purchase-tool-rate', 1.8);
      purchaseToolSetValue('purchase-tool-repay', 'equal-payment');
      purchaseToolSetValue('purchase-tool-twd-rate', 20.8);
      purchaseToolSetValue('purchase-tool-cost-rate', 7);
      purchaseToolSetValue('purchase-tool-monthly-fee', 3.8);
      purchaseToolSetValue('purchase-tool-rent', 18);
      purchaseToolSetValue('purchase-tool-reserve-rate', 8);
      purchaseToolSetValue('purchase-tool-buyer-status', 'overseas');
      purchaseToolSetValue('purchase-tool-purpose', 'investment');
      purchaseToolSetValue('purchase-tool-payment-path', 'cash');
      purchaseToolSetValue('purchase-tool-cash-ratio', 40);
      purchaseToolSetValue('purchase-tool-income', 900);
      purchaseToolSetValue('purchase-tool-debt', 0);
      purchaseToolSetValue('purchase-tool-existing-property', 0);
      purchaseToolSetValue('purchase-tool-timeline', 'research');
      document.querySelectorAll('[data-purchase-doc]').forEach((item, idx) => {
        item.checked = idx < 2;
      });
      setPurchaseQuestionStep(0);
      refreshPurchaseQuestionAnswers();
      calculatePurchaseTool();
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
        el.addEventListener('change', calculatePurchaseTool);
      });
      modal.querySelectorAll('[data-purchase-tab]').forEach((btn) => {
        btn.addEventListener('click', () => {
          const key = String(btn.dataset.purchaseTab || 'loan');
          setPurchaseToolActivePane(key);
          calculatePurchaseTool();
        });
      });
      modal.querySelectorAll('[data-purchase-answer]').forEach((btn) => {
        btn.addEventListener('click', () => setPurchaseQuestionAnswer(btn));
        btn.setAttribute('aria-pressed', btn.classList.contains('is-selected') ? 'true' : 'false');
      });
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && modal.style.display !== 'none') setPurchaseToolModalOpen(false, { restoreFocus: true });
      });
      setPurchaseToolActivePane('loan');
      refreshPurchaseQuestionAnswers();
      calculatePurchaseTool();
    }


document.addEventListener('DOMContentLoaded', function () {
  installPurchaseTool();
});
