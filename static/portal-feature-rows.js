(function () {
  function getFeatureRows(panel) {
    const narrative = panel.querySelector(':scope > .portal-suumo-narrative');
    const raw = String(narrative && narrative.textContent ? narrative.textContent : '').trim();
    if (!narrative || !raw) return null;
    return {
      narrative,
      rows: raw.split(/\r?\n/u).map((line) => line.trim()).filter(Boolean),
    };
  }

  function escapeFeatureText(value) {
    const box = document.createElement('textarea');
    box.textContent = String(value || '');
    return box.innerHTML;
  }

  function createFeatureList() {
    const list = document.createElement('dl');
    list.className = 'portal-suumo-dlcompact portal-suumo-feature-list';
    return list;
  }

  function createFeatureItem(labelText, valueText) {
    const item = document.createElement('div');
    const label = document.createElement('span');
    const value = document.createElement('span');
    label.className = 'portal-suumo-feature-label';
    value.className = 'portal-suumo-feature-value portal-suumo-cell-preline';
    label.textContent = labelText;
    value.textContent = valueText;
    item.append(label, value);
    return item;
  }

  window.normalizePortalFeatureRows = function (root) {
    const scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll('.portal-suumo-tabpanel[data-suumo-panel="tokucho"]').forEach((panel) => {
      const source = getFeatureRows(panel);
      if (!source || !source.rows.length) return;
      source.narrative.hidden = true;
      source.narrative.style.setProperty('display', 'none', 'important');
      if (panel.querySelector(':scope > .portal-suumo-feature-list')) return;
      const list = createFeatureList();
      source.rows.forEach((line) => {
        const matched = line.match(/^([^:：]{1,18})[:：]\s*(.+)$/u);
        list.appendChild(createFeatureItem(
          matched ? matched[1].trim() : '特色摘要',
          matched ? matched[2].trim() : line
        ));
      });
      panel.insertBefore(list, source.narrative);
    });
  };

  function runNormalize() {
    window.normalizePortalFeatureRows(document);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', runNormalize);
  } else {
    runNormalize();
  }

  new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach((node) => {
        if (node instanceof Element) window.normalizePortalFeatureRows(node);
      });
    });
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
