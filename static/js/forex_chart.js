import { t, initI18n } from './i18n.js';

function loadScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) return resolve();
    const script = document.createElement('script');
    script.src = src;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.appendChild(script);
  });
}

function normalizePair(pair) {
  return (pair || 'EUR_USD').toUpperCase().replace('/', '_').replace('OANDA:', '');
}

async function loadServerSelectedPair() {
  try {
    const response = await fetch('/api/forex/selected-pair');
    if (!response.ok) return null;
    const data = await response.json();
    return data.selected_pair || null;
  } catch (_) {
    return null;
  }
}

function setSelectedPair(pair) {
  const normalized = normalizePair(pair);
  localStorage.setItem('odysseus-selected-pair', normalized);
  document.querySelectorAll('[data-selected-pair]').forEach((el) => { el.textContent = normalized.replace('_', '/'); });
  fetch('/api/forex/selected-pair', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pair: normalized }),
  }).catch(() => {});
  return normalized;
}

function showFallback(message) {
  const target = document.getElementById('forex-chart');
  if (target) target.innerHTML = `<div class="forex-chart-fallback">${message}</div>`;
}

function rowHtml(item, fallbackPair) {
  const pair = item.pair || fallbackPair || '';
  const side = item.side || item.type || item.event || item.status || '—';
  const at = item.opened_at || item.created_at || item.closed_at || '';
  const detail = item.strategy_id || item.message || item.reason || item.pnl || '';
  return `<div class="forex-journal-row"><strong>${pair}</strong> <span>${side}</span><div class="forex-journal-muted">${detail}</div><div class="forex-journal-muted">${at}</div></div>`;
}

async function refreshJournal() {
  const tradesEl = document.getElementById('forex-trades-log');
  const eventsEl = document.getElementById('forex-events-log');
  if (!tradesEl || !eventsEl) return;
  const pair = localStorage.getItem('odysseus-selected-pair') || 'EUR_USD';
  try {
    const [tradesRes, eventsRes] = await Promise.all([fetch('/api/forex/trades'), fetch('/api/forex/events')]);
    const trades = tradesRes.ok ? (await tradesRes.json()).trades || [] : [];
    const events = eventsRes.ok ? (await eventsRes.json()).events || [] : [];
    tradesEl.innerHTML = trades.length ? trades.slice(-20).reverse().map((x) => rowHtml(x, pair)).join('') : `<div class="forex-journal-empty">${t('noTrades')}</div>`;
    eventsEl.innerHTML = events.length ? events.slice(-20).reverse().map((x) => rowHtml(x, pair)).join('') : `<div class="forex-journal-empty">${t('noEvents')}</div>`;
  } catch (_) {
    tradesEl.innerHTML = `<div class="forex-journal-empty">${t('noTrades')}</div>`;
    eventsEl.innerHTML = `<div class="forex-journal-empty">${t('noEvents')}</div>`;
  }
}

function initJournalTabs() {
  document.addEventListener('click', (event) => {
    const tab = event.target.closest('[data-forex-tab]');
    if (!tab) return;
    const target = tab.dataset.forexTab;
    document.querySelectorAll('[data-forex-tab]').forEach((node) => node.classList.toggle('active', node === tab));
    const trades = document.getElementById('forex-trades-log');
    const events = document.getElementById('forex-events-log');
    if (trades) trades.hidden = target !== 'trades';
    if (events) events.hidden = target !== 'events';
  });
}

async function initTradingViewChart() {
  const target = document.getElementById('forex-chart');
  if (!target) return;
  const serverPair = await loadServerSelectedPair();
  const initialSymbol = setSelectedPair(serverPair || localStorage.getItem('odysseus-selected-pair') || 'EUR_USD');
  try {
    await loadScript('/charting_library/charting_library.js');
    await loadScript('/charting_library/datafeeds/udf/dist/bundle.js');
  } catch (err) {
    console.warn(err);
    showFallback(t('chartUnavailable'));
    return;
  }
  if (!window.TradingView || !window.Datafeeds) {
    showFallback(t('chartUnavailable'));
    return;
  }
  const widget = new window.TradingView.widget({
    container: 'forex-chart',
    library_path: '/charting_library/',
    datafeed: new window.Datafeeds.UDFCompatibleDatafeed('/api/udf'),
    symbol: initialSymbol,
    interval: '60',
    timezone: 'Etc/UTC',
    locale: (document.documentElement.lang || 'en') === 'ar' ? 'ar' : 'en',
    autosize: true,
    theme: 'dark',
    disabled_features: ['use_localstorage_for_settings'],
    enabled_features: ['study_templates'],
  });
  window.odysseusForexWidget = widget;
  widget.onChartReady(() => {
    const chart = widget.activeChart();
    chart.onSymbolChanged().subscribe(null, () => setSelectedPair(chart.symbol()));
    window.odysseusDrawOnForexChart = (commands = []) => {
      const active = widget.activeChart();
      for (const command of commands) {
        try {
          if (command.type === 'shape') active.createShape(command.point, command.options || {});
          if (command.type === 'multipoint') active.createMultipointShape(command.points || [], command.options || {});
          if (command.type === 'study') active.createStudy(command.name, false, false, command.inputs || {}, command.overrides || {});
        } catch (err) {
          console.warn('draw command failed', command, err);
        }
      }
    };
  });
}

export function initForexWorkspace() {
  initI18n();
  document.body.classList.add('forex-agent');
  document.getElementById('chat-container')?.classList.add('forex-layout');
  initJournalTabs();
  refreshJournal();
  window.addEventListener('focus', refreshJournal);
  document.addEventListener('odysseus:language-changed', refreshJournal);
  initTradingViewChart();
}

export default { initForexWorkspace };
