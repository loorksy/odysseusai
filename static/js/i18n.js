const STRINGS = {
  en: {
    title: 'Forex Trading Agent',
    selectedPair: 'Selected pair',
    lang: 'العربية',
    trades: 'Trades',
    events: 'Events',
    noTrades: 'No paper trades yet.',
    noEvents: 'No trading events yet.',
    chartUnavailable: 'TradingView Advanced Charting Library was not found. Ensure charting_library-master is present in the repository.',
  },
  ar: {
    title: 'وكيل تداول الفوركس',
    selectedPair: 'الزوج المختار',
    lang: 'English',
    trades: 'الصفقات',
    events: 'الأحداث',
    noTrades: 'لا توجد صفقات ورقية بعد.',
    noEvents: 'لا توجد أحداث تداول بعد.',
    chartUnavailable: 'لم يتم العثور على مكتبة TradingView Advanced Charting Library. تأكد من وجود charting_library-master داخل المستودع.',
  },
};

function currentLang() {
  const saved = localStorage.getItem('odysseus-lang');
  if (saved) return saved;
  return (navigator.language || '').toLowerCase().startsWith('ar') ? 'ar' : 'en';
}

export function t(key) {
  const lang = currentLang();
  return (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key] || key;
}

export function applyLanguage(lang = currentLang()) {
  localStorage.setItem('odysseus-lang', lang);
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === 'ar' ? 'rtl' : 'ltr';
  document.querySelectorAll('[data-i18n]').forEach((node) => { node.textContent = t(node.dataset.i18n); });
  document.dispatchEvent(new CustomEvent('odysseus:language-changed', { detail: { lang } }));
}

export function initI18n() {
  applyLanguage(currentLang());
  document.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-lang-toggle]');
    if (!btn) return;
    applyLanguage(currentLang() === 'ar' ? 'en' : 'ar');
  });
}

export default { t, applyLanguage, initI18n };
