'use strict';

const $ = (s, root = document) => root.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = value => Number(value || 0);
const money = (value, currency = state.data?.portfolio.currency || 'RUB', digits = 0) => new Intl.NumberFormat('ru-RU', {style:'currency',currency,maximumFractionDigits:digits,minimumFractionDigits:digits}).format(num(value));
const decimal = (value, digits = 2) => new Intl.NumberFormat('ru-RU', {maximumFractionDigits:digits}).format(num(value));
const pct = value => value === null ? '—' : `${decimal(value, 1)}%`;
const signed = (value, formatter = money) => `${num(value) > 0 ? '+' : ''}${formatter(value)}`;
const tone = value => num(value) > 0 ? 'positive' : num(value) < 0 ? 'negative' : '';
const day = value => new Intl.DateTimeFormat('ru-RU', {day:'numeric',month:'short',year:'numeric'}).format(new Date(`${value}T12:00:00`));
const month = value => new Intl.DateTimeFormat('ru-RU', {month:'short'}).format(new Date(`${value}-15T12:00:00`)).replace('.', '');
const colors = ['#599781','#a8c593','#d6bf7e','#8ba2bf','#b3a1c8','#7dbbb8','#ce9b8a','#bbc6c9'];
const types = {deposit:'Пополнение',withdrawal:'Вывод',buy:'Покупка',sell:'Продажа',dividend:'Дивиденды',coupon:'Купон',fee:'Комиссия'};
const kinds = {stock:'Акции',bond:'Облигации',fund:'Фонды',crypto:'Криптовалюта',metal:'Металлы',other:'Другие активы'};
const pages = {overview:'Обзор портфеля',assets:'Мои активы',transactions:'Операции',dividends:'Дивиденды',analytics:'Аналитика',tbank:'Т-Инвестиции',settings:'Настройки'};
const paths = {
 grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
 wallet:'M3 7V5a2 2 0 0 1 2-2h13v4 M3 7h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z M16 11h5v6h-5z',
 swap:'M4 7h16m-4-4 4 4-4 4 M20 17H4m4-4-4 4 4 4',
 coin:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M15 8h-4a2 2 0 0 0 0 4h2a2 2 0 0 1 0 4H9 M12 6v2m0 8v2',
 chart:'M4 4v16h17 M8 15l4-5 4 3 5-7',
 settings:'m9 3-1 3-3 1-2 4 2 2v3l3 2 2 3 4-1 2-3 3-1 2-4-2-2v-3l-3-2-2-3-4 1z M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8',
 plus:'M12 5v14 M5 12h14',
 upload:'M12 16V3m-4 4 4-4 4 4 M4 15v5h16v-5',
 download:'M12 3v13m-4-4 4 4 4-4 M4 16v5h16v-5',
 arrow:'M5 19 19 5M5 5h14v14',
 search:'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6',
 edit:'m15 4 5 5 M3 21l5-1L21 7a2 2 0 0 0-5-5L3 15v6z',
 trash:'M3 6h18M9 6V3h6v3 M5 6l1 15h12l1-15 M10 10v7m4-7v7',
 close:'M6 6l12 12M6 18 18 6',
 shield:'M12 3 3 7v6c0 4 9 8 9 8s9-4 9-8V7l-9-4z M8 12l3 3 5-6',
 target:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10 M12 11v2',
 calendar:'M5 4h14a2 2 0 0 1 2 2v14H3V6a2 2 0 0 1 2-2 M7 2v5m10-5v5M3 10h18 M7 14h3m4 0h3m-10 3h3',
 chevron:'m9 5 7 7-7 7',
};
function icon(name) { return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.grid}"/></svg>`; }
const state = {meta:null,data:null,pid:null,page:'overview',period:'all',search:'',category:'',txType:'',loadId:0,broker:null,brokerAccount:null,brokerId:null,authRequired:false};
let toastTimer, importText = '', importValidated = false;

async function api(path, method = 'GET', body) {
  const response = await fetch(`/api${path}`, {method,headers:body === undefined ? {} : {'Content-Type':'application/json'},body:body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if(response.status===401){location.replace('/login.html');throw new Error('Войдите в приложение');}
  if (!response.ok) throw new Error(data.error || 'Не удалось выполнить запрос');
  return data;
}
function toast(text, error = false) {
  clearTimeout(toastTimer);
  $('#toast').textContent = text;
  $('#toast').className = `visible${error ? ' error' : ''}`;
  toastTimer = setTimeout(() => { $('#toast').className = ''; }, 4500);
}
function currentPage() { return Object.hasOwn(pages, location.hash.slice(1)) ? location.hash.slice(1) : 'overview'; }
async function refresh(pid = state.pid) {
  const loadId = ++state.loadId;
  const meta = await api('/meta');
  if (loadId !== state.loadId) return;
  state.meta = meta;
  const authentication=await api('/auth/status');
  state.authRequired=authentication.required;
  $('#logout').hidden=!authentication.required;
  $('#change-password').hidden=!authentication.required;
  state.broker = await api('/tbank');
  if (loadId !== state.loadId) return;
  if (!state.broker.accounts.some(a=>a.id===state.brokerId)) state.brokerId=state.broker.accounts[0]?.id || null;
  state.brokerAccount=state.brokerId?await api(`/tbank/accounts/${encodeURIComponent(state.brokerId)}`):null;
  if (loadId !== state.loadId) return;
  const preferred = Number(pid || localStorage.getItem('invest-portfolio'));
  state.pid = meta.portfolios.some(p => p.id === preferred) ? preferred : meta.portfolios[0]?.id || null;
  if (state.pid) {
    const data = await api(`/portfolios/${state.pid}`);
    if (loadId !== state.loadId) return;
    state.data = data;
    localStorage.setItem('invest-portfolio', String(state.pid));
  } else {
    state.data = null;
    localStorage.removeItem('invest-portfolio');
  }
  render();
}
function render() {
  state.page = currentPage();
  const nav = [['overview','grid'],['assets','wallet'],['transactions','swap'],['dividends','coin'],['analytics','chart'],['tbank','swap'],['settings','settings']];
  $('#navigation').innerHTML = nav.map(([page,name]) => `${page === 'settings' ? '<div class="nav-separator"></div>' : ''}<a href="#${page}" class="${state.page === page ? 'active' : ''}" ${state.page === page ? 'aria-current="page"' : ''}>${icon(name)}${pages[page]}</a>`).join('');
  $('#crumb').textContent = pages[state.page];
  $('#portfolio-select').innerHTML = state.meta.portfolios.length ? state.meta.portfolios.map(p => `<option value="${p.id}" ${p.id === state.pid ? 'selected' : ''}>${esc(p.name)}</option>`).join('') : '<option value="">Нет портфелей</option>';
  $('#portfolio-select').disabled = !state.meta.portfolios.length;
  document.title = `${pages[state.page]} — Инвест Обзор`;
  if (state.page==='tbank') {$('#main').innerHTML=brokerPage();return;}
  if (state.page==='settings' && !state.data) {
    $('#main').innerHTML='<div class="page-heading"><div><h1>Настройки</h1><p>Параметры вашего пространства.</p></div></div>'+accountSettings();
    return;
  }
  if (!state.data) { $('#main').innerHTML = welcome(); return; }
  const pageRender = {overview,assets:assetsPage,transactions:transactionsPage,dividends:dividendsPage,analytics:analyticsPage,settings:settingsPage};
  $('#main').innerHTML = pageRender[state.page]();
}
function welcome() {
  return `<div class="welcome"><section class="welcome-hero"><div class="welcome-orbit"></div><div class="eyebrow">СПОКОЙНОЕ УПРАВЛЕНИЕ КАПИТАЛОМ</div><h1>Вся картина ваших инвестиций.</h1><p>Соберите активы в одном месте. Следите за капиталом, учитывайте сделки и дивиденды — в своём локальном пространстве.</p><div class="heading-actions"><button class="button primary" data-action="new-portfolio">${icon('plus')} Создать портфель</button><button class="button" data-action="demo">Открыть демо ${icon('chevron')}</button></div></section><div class="feature-grid"><article class="card">${icon('wallet')}<h2>Понятный учёт</h2><p>Портфели, категории, покупки и продажи. Импорт операций из CSV.</p></article><article class="card">${icon('coin')}<h2>Доход под контролем</h2><p>Дивиденды, купоны, комиссии и налоги. Прогноз выплат на год.</p></article><article class="card">${icon('shield')}<h2>Данные у вас</h2><p>Локальная база SQLite, резервные копии и работа без внешних сервисов.</p></article></div></div>`;
}
function heading(title, description, actions = '') {
  const p = state.data.portfolio;
  return `<div class="page-heading"><div><div class="eyebrow">${esc(p.name)} <span>· ${esc(p.currency)}</span></div><h1>${title}</h1><p>${description}</p></div><div class="heading-actions">${actions}</div></div>${p.is_demo ? '<div class="demo-notice"><span>Демонстрационный портфель. Цены, выплаты и история условные и не являются рыночными данными.</span><button class="text-button" data-action="new-portfolio">Создать свой →</button></div>' : ''}`;
}
function standardActions() { return `<button class="button" data-action="import">${icon('upload')} Импорт CSV</button><button class="button primary" data-action="new-transaction">${icon('plus')} Операция</button>`; }
function metric(title, value, note, glyph, highlight = false, valueClass = '') {
  return `<article class="card metric${highlight ? ' highlight' : ''}"><div class="metric-label">${title}${icon(glyph)}</div><div class="metric-value ${valueClass}">${value}</div><div class="metric-note">${note}</div></article>`;
}
function metricMoney(value) {
  const currency = state.data.portfolio.currency;
  const symbol = new Intl.NumberFormat('ru-RU',{style:'currency',currency,maximumFractionDigits:0}).formatToParts(0).find(p=>p.type==='currency')?.value || currency;
  return `${decimal(value,0)}<span class="currency">${esc(symbol)}</span>`;
}
function metrics() {
  const s = state.data.summary;
  return `<div class="cards">${metric('Стоимость портфеля',metricMoney(s.equity),`Активы и денежный остаток`, 'wallet')}${metric('Общая прибыль',metricMoney(s.profit),`С учётом выплат, комиссий и налогов`, 'chart',false,tone(s.profit))}${metric('Получено выплат',metricMoney(s.income),`Дивиденды и купоны после удержаний`, 'coin')}${metric('Пассивный доход / год',metricMoney(s.annual_income),`<span class="badge green">${pct(s.dividend_yield)}</span> Прогноз до налогов`, 'arrow',true)}</div>`;
}
function overview() {
  return heading('Обзор портфеля','Большая картина. Каждый актив на своём месте.',standardActions()) + metrics() +
    `<div class="grid-main">${historyPanel()}${allocationPanel()}</div>${assetTable(true)}<div class="grid-two">${incomePanel()}${forecastPanel()}</div>${cashPanel()}`;
}
function historyPanel() {
  return `<section class="card panel"><div class="panel-header"><div><h2>Динамика капитала</h2><p class="panel-subtitle">Оценка портфеля в дни сохранения данных</p></div><div class="segmented" aria-label="Период графика">${[['1m','1 мес'],['3m','3 мес'],['1y','Год'],['all','Всё']].map(([key,title])=>`<button data-action="period" data-period="${key}" aria-pressed="${state.period===key}" class="${state.period===key?'active':''}">${title}</button>`).join('')}</div></div><div class="chart-legend"><span class="legend-item"><i class="legend-dot" style="background:#599781"></i>Стоимость</span><span class="legend-item"><i class="legend-dot" style="background:#bfcac1"></i>Чистые пополнения</span></div>${lineChart()}</section>`;
}
function lineChart() {
  const cutoff = new Date(`${state.meta.today}T00:00:00`);
  if (state.period === '1m') cutoff.setMonth(cutoff.getMonth()-1);
  if (state.period === '3m') cutoff.setMonth(cutoff.getMonth()-3);
  if (state.period === '1y') cutoff.setFullYear(cutoff.getFullYear()-1);
  const history = state.data.history.filter(h=>state.period==='all' || new Date(`${h.date}T12:00:00`) >= cutoff);
  if (!history.length) return '<div class="chart-empty">За выбранный период нет сохранённых оценок.</div>';
  if (history.length === 1) return `<div class="chart-empty">${icon('chart')}<strong>${money(history[0].equity)}</strong><span>Первая оценка · ${day(history[0].date)}<br>История будет расти по мере использования.</span></div>`;
  const w=650,h=205,left=56,right=12,top=15,bottom=30;
  const values = history.flatMap(p=>[num(p.equity),num(p.contributions)]);
  let lo=Math.min(...values),hi=Math.max(...values);
  const range=hi-lo || Math.abs(hi)*.1 || 100;
  lo-=range*.12; hi+=range*.12;
  const start=new Date(`${history[0].date}T12:00:00`).getTime(),end=new Date(`${history.at(-1).date}T12:00:00`).getTime();
  const x=i=>left+(new Date(`${history[i].date}T12:00:00`).getTime()-start)/(end-start)*(w-left-right);
  const y=value=>top+(hi-num(value))/(hi-lo)*(h-top-bottom);
  const path=key=>history.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(2)},${y(p[key]).toFixed(2)}`).join(' ');
  const short=value=>Math.abs(value)>=1e6?`${decimal(value/1e6,1)} млн`:Math.abs(value)>=1000?`${decimal(value/1000,0)} тыс`:decimal(value,0);
  const grid=Array.from({length:4},(_,i)=>{const v=lo+(hi-lo)*i/3;return `<line class="grid-line" x1="${left}" y1="${y(v)}" x2="${w-right}" y2="${y(v)}"/><text x="${left-9}" y="${y(v)+3}" text-anchor="end">${short(v)}</text>`;}).join('');
  const labels=[...new Set([0,Math.floor((history.length-1)/2),history.length-1])].map((i,j)=>`<text x="${x(i)}" y="${h-5}" text-anchor="${j===0?'start':i===history.length-1?'end':'middle'}">${esc(new Intl.DateTimeFormat('ru-RU',{month:'short',year:'2-digit'}).format(new Date(`${history[i].date}T12:00:00`)))}</text>`).join('');
  return `<div class="line-chart"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Стоимость портфеля и чистые пополнения по сохранённым оценкам"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#8fc2a2" stop-opacity=".22"/><stop offset="100%" stop-color="#8fc2a2" stop-opacity=".01"/></linearGradient></defs>${grid}<path d="${path('equity')} L${x(history.length-1)},${h-bottom} L${left},${h-bottom} Z" fill="url(#chart-fill)"/><path d="${path('contributions')}" fill="none" stroke="#bdc9bf" stroke-width="1.5" stroke-dasharray="5 5"/><path d="${path('equity')}" fill="none" stroke="#599781" stroke-width="2.5" stroke-linejoin="round"/>${history.map((p,i)=>`<circle cx="${x(i)}" cy="${y(p.equity)}" r="3" fill="#599781"><title>${esc(day(p.date))}: ${esc(money(p.equity))}; пополнения ${esc(money(p.contributions))}</title></circle>`).join('')}${labels}</svg></div>`;
}
function allocationPanel() {
  const total = num(state.data.summary.market_value);
  const categories = [...state.data.categories].sort((a,b)=>num(b.value)-num(a.value));
  let cumulative=0;
  const circumference=2*Math.PI*65;
  const rings=categories.map((c,i)=>{const frac=num(c.value)/total;const out=`<circle cx="90" cy="90" r="65" fill="none" stroke="${colors[i%colors.length]}" stroke-width="19" stroke-dasharray="${Math.max(0,frac*circumference-3)} ${circumference}" stroke-dashoffset="${-cumulative*circumference}"><title>${esc(c.name)}: ${pct(frac*100)}</title></circle>`;cumulative+=frac;return out;}).join('');
  const count=state.data.assets.filter(a=>num(a.quantity)>0).length;
  return `<section class="card panel allocation-panel"><div class="panel-header"><div><h2>Структура активов</h2><p class="panel-subtitle">Распределение по категориям · без денег</p></div>${icon('grid')}</div><div class="allocation-content"><div class="donut-layout"><svg class="donut" viewBox="0 0 180 180" role="img" aria-label="Распределение активов по категориям"><circle cx="90" cy="90" r="65" fill="none" stroke="#f0f3ee" stroke-width="19"/>${rings}</svg><div class="donut-center"><strong>${count}</strong><span>активов в портфеле</span></div></div><div class="allocation-list">${categories.length ? categories.map((c,i)=>`<div class="allocation-row"><i class="legend-dot" style="background:${colors[i%colors.length]}"></i><span class="allocation-name" title="${esc(c.name)}">${esc(c.name)}</span><strong>${money(c.value)}</strong><span>${pct(num(c.value)/total*100)}</span></div>`).join('') : '<p class="note">Здесь появится распределение после первой покупки.</p>'}</div></div></section>`;
}
function logo(asset) {
  const n = [...asset.ticker].reduce((a,c)=>a+c.charCodeAt(0),0)%colors.length;
  return `<span class="asset-logo" style="color:${colors[n]};background:${colors[n]}20">${esc(asset.ticker.slice(0,2))}</span>`;
}
function assetName(a) { return `<div class="asset-name">${logo(a)}<div><strong>${esc(a.ticker)}</strong><small>${esc(a.name)}</small></div></div>`; }
function rowButtons(resource,id) {
  return `<div class="row-actions"><button class="icon-button" data-action="edit-${resource}" data-id="${id}" aria-label="Редактировать ${resource==='asset'?'актив':'операцию'}">${icon('edit')}</button><button class="icon-button delete" data-action="delete-${resource}" data-id="${id}" aria-label="Удалить ${resource==='asset'?'актив':'операцию'}">${icon('trash')}</button></div>`;
}
function filteredAssets() {
  return state.data.assets.filter(a=>(!state.search || `${a.ticker} ${a.name}`.toLowerCase().includes(state.search.toLowerCase())) && (!state.category || a.category===state.category));
}
function assetRows(list) {
  return list.length ? list.map(a=>`<tr><td>${assetName(a)}</td><td><span class="badge">${esc(a.category)}</span></td><td class="numeric">${decimal(a.quantity,8)}<small class="cell-sub">${money(a.average_price,a.currency,2)} ср.</small></td><td class="numeric">${money(a.price,a.currency,2)}<small class="cell-sub" title="${esc(a.price_updated_at)}">Ручная котировка</small></td><td class="numeric"><strong class="cell-main">${money(a.value)}</strong><small class="cell-sub">${money(a.cost_base)} вложено</small></td><td class="numeric ${tone(a.gain)}"><span class="cell-main">${signed(a.gain)}</span><small class="cell-sub ${tone(a.gain)}">${a.gain_pct===null?'—':signed(a.gain_pct,pct)}</small></td><td class="numeric"><span class="weight-track"><span style="width:${Math.max(0,Math.min(100,num(a.weight)))}%"></span></span>${pct(a.weight)}</td><td>${rowButtons('asset',a.id)}</td></tr>`).join('') : '<tr><td colspan="8" class="empty-row">Активы не найдены. Добавьте актив и внесите покупку вручную или импортируйте CSV.</td></tr>';
}
function assetTable(compact = false) {
  const cats=[...new Set(state.data.assets.map(a=>a.category))];
  return `<section class="card table-panel"><div class="panel-header"><div><h2>Мои активы <span class="badge">${state.data.assets.length}</span></h2>${compact?'':'<p class="panel-subtitle">Котировки и годовые выплаты задаются в редакторе актива</p>'}</div><button class="text-button" data-action="new-asset">${icon('plus')} Добавить актив</button></div><div class="table-toolbar"><div class="search-wrap">${icon('search')}<input id="asset-search" type="search" placeholder="Найти актив…" aria-label="Поиск по активам" value="${esc(state.search)}"></div><select id="category-filter" aria-label="Фильтр по категории"><option value="">Все категории</option>${cats.map(c=>`<option ${c===state.category?'selected':''} value="${esc(c)}">${esc(c)}</option>`).join('')}</select></div><div class="table-scroll"><table><thead><tr><th>Актив</th><th>Категория</th><th class="numeric">Количество / ср. цена</th><th class="numeric">Цена</th><th class="numeric">Стоимость</th><th class="numeric">Рост позиции</th><th class="numeric">Доля</th><th><span class="sr-only">Действия</span></th></tr></thead><tbody id="asset-rows">${assetRows(filteredAssets())}</tbody></table></div><div class="table-footer"><span>Средняя стоимость покупки с комиссией · Рост без полученных выплат</span><span>Валюта портфеля: ${esc(state.data.portfolio.currency)}</span></div></section>`;
}
function assetsPage() { return heading('Мои активы','Ваш капитал — в деталях.',standardActions())+assetTable()+cashPanel(); }
function cashPanel() {
  const cash=state.data.cash;
  return `<section class="card panel"><div class="panel-header"><div><h2>Денежный остаток</h2><p class="panel-subtitle">Пополнения, сделки и выплаты формируют баланс автоматически</p></div><button class="text-button" data-action="deposit">${icon('plus')} Пополнить</button></div><div class="cash-list">${cash.length?cash.map(c=>`<div class="cash-chip"><span>${esc(c.currency)}</span><strong class="${tone(c.amount)}">${money(c.amount,c.currency,2)}</strong></div>`).join(''):'<p class="note">Операций с деньгами пока нет.</p>'}</div>${cash.some(c=>num(c.amount)<0)?'<p class="warning-note">Есть отрицательный денежный остаток. Проверьте, внесены ли все пополнения и операции обмена валюты. Маржинальное кредитование отдельно не моделируется.</p>':''}</section>`;
}
function transactionRows(list) {
  const assets=new Map(state.data.assets.map(a=>[a.id,a]));
  return list.length ? list.map(t=>{const a=assets.get(t.asset_id);return `<tr><td>${day(t.date)}</td><td><span class="badge ${['deposit','dividend','coupon'].includes(t.type)?'green':''}">${types[t.type]}</span></td><td>${a?assetName(a):'<span class="cell-sub">Денежная операция</span>'}</td><td class="numeric">${['buy','sell'].includes(t.type)?decimal(t.quantity,8):'—'}</td><td class="numeric">${['buy','sell'].includes(t.type)?money(t.price,t.currency,2):'—'}</td><td class="numeric"><strong class="cell-main">${money(t.amount,t.currency,2)}</strong><small class="cell-sub">${esc(t.currency)}</small></td><td class="numeric">${money(num(t.fee)+num(t.tax),t.currency,2)}</td><td><span class="cell-sub" title="${esc(t.note)}">${esc(t.note.length>35?t.note.slice(0,35)+'…':t.note)}</span></td><td>${rowButtons('transaction',t.id)}</td></tr>`;}).join('') : '<tr><td colspan="9" class="empty-row">Операций пока нет. Начните с пополнения, затем добавьте покупку.</td></tr>';
}
function transactionsPage() {
  return heading('История операций','Каждое движение капитала — на своём месте.',standardActions())+`<section class="card table-panel"><div class="panel-header"><h2>Все операции <span class="badge">${state.data.transactions.length}</span></h2><a class="text-button" href="/api/portfolios/${state.pid}/export" download>${icon('download')} Экспорт CSV</a></div><div class="table-toolbar"><select id="transaction-filter" aria-label="Фильтр по типу операции"><option value="">Все типы операций</option>${Object.entries(types).map(([key,title])=>`<option value="${key}" ${state.txType===key?'selected':''}>${title}</option>`).join('')}</select><span class="note" style="margin:0">Новые операции сверху</span></div><div class="table-scroll"><table><thead><tr><th>Дата</th><th>Операция</th><th>Актив</th><th class="numeric">Количество</th><th class="numeric">Цена</th><th class="numeric">Сумма</th><th class="numeric">Удержания</th><th>Комментарий</th><th><span class="sr-only">Действия</span></th></tr></thead><tbody id="transaction-rows">${transactionRows(state.data.transactions.filter(t=>!state.txType||t.type===state.txType))}</tbody></table></div><div class="table-footer"><span>Суммы операций указаны до комиссий и налогов, в валюте операции</span></div></section>`+cashPanel();
}
function incomePanel() {
  const list = state.data.income_history.slice(-12);
  const max=Math.max(...list.map(i=>num(i.amount)),1);
  return `<section class="card panel"><div class="panel-header"><div><h2>Полученные дивиденды и купоны</h2><p class="panel-subtitle">Фактические выплаты после налогов и комиссий</p></div>${icon('coin')}</div>${list.length?`<div class="bar-chart" role="img" aria-label="Полученные выплаты по месяцам">${list.map(i=>`<div class="bar-column" title="${esc(i.month)}: ${money(i.amount)}"><span class="bar-value">${decimal(num(i.amount)/1000,1)}к</span><div class="bar" style="height:${Math.max(2,num(i.amount)/max*120)}px"></div><span class="bar-label">${month(i.month)} '${i.month.slice(2,4)}</span></div>`).join('')}</div>`:'<div class="chart-empty">Здесь появятся фактически полученные выплаты.<br>Добавьте операцию «Дивиденды» или «Купон».</div>'}</section>`;
}
function forecastPanel() {
  const list=[...state.data.assets].filter(a=>num(a.annual_income)>0).sort((a,b)=>num(b.annual_income)-num(a.annual_income)).slice(0,5);
  return `<section class="card panel"><div class="panel-header"><div><h2>Ожидаемый доход</h2><p class="panel-subtitle">На год вперёд · до налогов</p></div><span class="badge green">Оценка</span></div><div class="income-list">${list.length?list.map(a=>`<div class="income-item">${logo(a)}<div class="income-description"><strong>${esc(a.name)}</strong><small>${esc(a.ticker)} · ${money(a.annual_dividend,a.currency,2)} на единицу / год</small></div><div class="income-amount">${money(a.annual_income)}<small>в год</small></div></div>`).join(''):'<p class="note">Укажите прогноз выплаты на единицу в год в редакторе актива.</p>'}</div><p class="note">Расчёт: текущий остаток × указанная годовая выплата. Даты выплат и их объявление не прогнозируются.</p></section>`;
}
function dividendsPage() {
  const s=state.data.summary;
  return heading('Дивидендный доход','Выплаты сегодня. Ориентир на будущее.',`<button class="button primary" data-action="new-dividend">${icon('plus')} Добавить выплату</button>`)+`<div class="cards">${metric('Получено за всё время',metricMoney(s.income),'После налогов и комиссий','coin')}${metric('Прогноз на год',metricMoney(s.annual_income),'До налогов · ручная оценка','arrow',true)}${metric('В среднем за месяц',metricMoney(num(s.annual_income)/12),'Прогноз годового дохода / 12','calendar')}${metric('Доходность выплат',pct(s.dividend_yield),'К текущей стоимости активов','chart')}</div><div class="grid-two">${incomePanel()}${forecastPanel()}</div><section class="card table-panel"><div class="panel-header"><h2>История выплат</h2></div><div class="table-scroll"><table><thead><tr><th>Дата</th><th>Операция</th><th>Актив</th><th class="numeric">Количество</th><th class="numeric">Цена</th><th class="numeric">Начислено</th><th class="numeric">Удержания</th><th>Комментарий</th><th><span class="sr-only">Действия</span></th></tr></thead><tbody>${transactionRows(state.data.transactions.filter(t=>['dividend','coupon'].includes(t.type)))}</tbody></table></div></section>`;
}
function analyticsPage() {
  const s=state.data.summary,p=state.data.portfolio;
  const residual=num(s.profit)-num(s.unrealized)-num(s.realized)-num(s.income);
  const goal=num(p.goal),progress=goal?Math.min(100,Math.max(0,num(s.equity)/goal*100)):0;
  return heading('Аналитика портфеля','Посмотрите, из чего складывается результат.')+metrics()+`<div class="grid-main">${historyPanel()}${allocationPanel()}</div><div class="grid-two"><section class="card panel"><div class="panel-header"><div><h2>Из чего складывается прибыль</h2><p class="panel-subtitle">Учёт по средней стоимости покупки</p></div>${icon('chart')}</div><div class="breakdown">${[['Рост открытых позиций',s.unrealized],['Результат продаж',s.realized],['Полученные выплаты, нетто',s.income],['Переоценка денег и прочие расходы',residual],['Общая прибыль',s.profit]].map(([title,val])=>`<div class="breakdown-row"><span>${title}</span><strong class="${tone(val)}">${signed(val)}</strong></div>`).join('')}</div><p class="note">Комиссии сделок включены в стоимость позиций и результат продаж. Курсы покупки фиксируются в операции; текущая оценка использует курсы из настроек.</p></section><section class="card panel"><div class="panel-header"><div><h2>На пути к цели</h2><p class="panel-subtitle">Накопление капитала</p></div><button class="text-button" data-action="edit-portfolio">Настроить</button></div>${goal?`<div class="projection"><strong>${pct(progress)}</strong><small>цели достигнуто</small></div><div class="goal-track"><span style="width:${progress}%"></span></div><div class="goal-values"><strong>${money(s.equity)}</strong><span>из ${money(goal)}</span></div><p class="note">Осталось ${money(Math.max(0,goal-num(s.equity)))}. Плановое пополнение: ${money(p.monthly_contribution)} / месяц.</p>${num(p.monthly_contribution)>0?`<p class="note">При нулевой доходности и без вывода средств: ещё примерно ${decimal(Math.ceil(Math.max(0,goal-num(s.equity))/num(p.monthly_contribution)),0)} мес. Это простой план накоплений, без прогноза рыночной доходности.</p>`:''}`:'<div class="chart-empty">Укажите цель по капиталу и ежемесячное пополнение.<button class="text-button" data-action="edit-portfolio">Задать цель →</button></div>'}</section></div>${rebalanceTable()}`;
}
function rebalanceTable() {
  const list=state.data.assets,total=num(state.data.summary.market_value);
  const targetTotal=list.reduce((sum,a)=>sum+num(a.target_weight),0);
  return `<section class="card table-panel"><div class="panel-header"><div><h2>План распределения</h2><p class="panel-subtitle">Сравнение текущих и целевых долей, без денежного остатка</p></div><span class="badge ${Math.abs(targetTotal-100)<.001?'green':'amber'}">Целевые доли: ${pct(targetTotal)}</span></div><div class="table-scroll"><table><thead><tr><th>Актив</th><th class="numeric">Текущая доля</th><th class="numeric">Целевая доля</th><th class="numeric">Отклонение</th><th class="numeric">Разница до целевой стоимости</th><th></th></tr></thead><tbody>${list.length?list.map(a=>`<tr><td>${assetName(a)}</td><td class="numeric">${pct(a.weight)}</td><td class="numeric">${pct(a.target_weight)}</td><td class="numeric">${signed(num(a.weight)-num(a.target_weight),v=>decimal(v,1))} п.п.</td><td class="numeric">${signed(total*num(a.target_weight)/100-num(a.value))}</td><td><button class="icon-button" data-action="edit-asset" data-id="${a.id}" aria-label="Изменить целевую долю ${esc(a.ticker)}">${icon('edit')}</button></td></tr>`).join(''):'<tr><td colspan="6" class="empty-row">Добавьте активы и задайте целевые доли в их настройках.</td></tr>'}</tbody></table></div><div class="table-footer"><span>Справочное сравнение распределения. Целевые доли задаёте вы.</span></div></section>`;
}
function settingsPage() {
  const p=state.data.portfolio, rates=new Map(state.meta.rates.map(r=>[r.currency,r]));
  return heading('Настройки','Ваше пространство, ваши правила.')+accountSettings()+`<div class="settings-layout"><section class="card panel"><div class="panel-header"><div><h2>Портфель</h2><p class="panel-subtitle">Основные параметры и цель по капиталу</p></div>${icon('wallet')}</div><form data-form="portfolio" data-id="${p.id}"><div class="form-error" role="alert"></div>${portfolioFields(p)}<div class="form-actions"><button class="button primary" type="submit">Сохранить</button></div></form><div class="danger-zone"><h2>Удаление портфеля</h2><p>Удаляет портфель, его активы, операции и историю. Сначала сохраните резервную копию.</p><button class="button danger small" data-action="delete-portfolio">Удалить портфель</button></div></section><div><section class="card panel"><div class="panel-header"><div><h2>Курсы валют</h2><p class="panel-subtitle">Рублей за единицу валюты · ввод вручную</p></div>${icon('swap')}</div><form data-form="rates"><div class="form-error" role="alert"></div><div class="rates-grid">${state.meta.currencies.map(cur=>`<label class="field"><span class="rate-label">${cur}<small>${rates.has(cur)?day(rates.get(cur).updated_at.slice(0,10)):'Не задан'}</small></span><input name="${cur}" type="number" min="0.000000000001" step="any" value="${esc(rates.get(cur)?.rub||'')}" placeholder="Введите курс" ${cur==='RUB'?'readonly':''}></label>`).join('')}</div><p class="note">Для пересчёта валют задайте курсы валюты актива и базовой валюты. Исторические курсы операций сохраняются отдельно.</p><div class="form-actions"><button class="button primary" type="submit">Сохранить курсы</button></div></form></section><section class="card panel" style="margin-top:20px"><div class="panel-header"><div><h2>Ваши данные</h2><p class="panel-subtitle">Локальная база данных SQLite</p></div>${icon('shield')}</div><div class="heading-actions" style="flex-wrap:wrap"><a class="button" href="/api/backup" download>${icon('download')} Резервная копия</a><a class="button" href="/api/portfolios/${state.pid}/export" download>${icon('download')} Операции CSV</a></div><p class="note">Резервная копия содержит портфели, операции и сохранённые данные брокера. Токены доступа исключаются. Для восстановления остановите сервер и замените файл базы, как описано в README.</p><p class="note">Подключение токена и загрузка счетов доступны в разделе «Т-Инвестиции». Запросы к брокеру выполняются только сервером. Ручные портфели ведутся независимо от брокерских счетов.</p></section></div></div>`;
}
function currencyOptions(selected) { return state.meta.currencies.map(c=>`<option value="${c}" ${c===selected?'selected':''}>${c}</option>`).join(''); }
function accountSettings() {
  return state.authRequired?`<section class="card panel" style="margin-bottom:22px"><div class="panel-header"><div><h2>Безопасность аккаунта</h2><p class="panel-subtitle">Для смены пароля потребуется текущий пароль. После смены все сессии будут завершены.</p></div>${icon('shield')}</div><button class="button" data-action="change-password">Сменить пароль</button></section>`:'';
}
function passwordModal() {
  openModal('Сменить пароль','После сохранения нужно войти с новым паролем на всех устройствах.',`<form data-password-form><div class="form-error" role="alert"></div><div class="form-grid"><label class="field full">Текущий пароль<input name="current_password" type="password" autocomplete="current-password" required maxlength="1024"></label><label class="field full">Новый пароль<input name="new_password" type="password" autocomplete="new-password" required minlength="9" maxlength="1024"><small>От 9 до 1024 символов, с сохранением пробелов и регистра.</small></label><label class="field full">Повторите новый пароль<input name="confirm_password" type="password" autocomplete="new-password" required minlength="9" maxlength="1024"></label></div><div class="form-actions"><button class="button" type="button" data-action="close-modal">Отмена</button><button class="button primary" type="submit">Сохранить пароль</button></div></form>`);
}
function inputField(title,name,value='',opts='') {
  return `<label class="field">${title}<input name="${name}" value="${esc(value)}" ${opts}></label>`;
}
function portfolioFields(p={}) {
  return `<div class="form-grid"><label class="field full">Название портфеля<input name="name" value="${esc(p.name||'')}" placeholder="Например, Дивидендный" required maxlength="120"></label><label class="field">Базовая валюта<select name="currency" ${p.id?'disabled':''}>${currencyOptions(p.currency||'RUB')}</select>${p.id?'<small>Фиксируется при создании</small>':''}</label>${inputField('Цель по капиталу','goal',p.goal||0,'type="number" min="0" step="any" required')}${inputField('Пополнение в месяц','monthly_contribution',p.monthly_contribution||0,'type="number" min="0" step="any" required')}</div>`;
}
function openModal(title,description,body) {
  $('#modal-content').innerHTML = `<div class="dialog-head"><h2 id="dialog-title">${title}</h2><button class="icon-button" data-action="close-modal" aria-label="Закрыть">${icon('close')}</button></div><p class="dialog-description">${description}</p>${body}`;
  if (!$('#modal').open) $('#modal').showModal();
}
function modalForm(form,fields,button='Сохранить',id='') {
  return `<form data-form="${form}" ${id?`data-id="${id}"`:''}><div class="form-error" role="alert"></div>${fields}<div class="form-actions"><button class="button" type="button" data-action="close-modal">Отмена</button><button class="button primary" type="submit">${button}</button></div></form>`;
}
function portfolioModal(edit = false) {
  const p=edit?state.data.portfolio:{};
  openModal(edit?'Настроить портфель':'Новый портфель','Дайте портфелю имя и выберите валюту для общей оценки.',modalForm('portfolio',portfolioFields(p),edit?'Сохранить':'Создать портфель',p.id));
}
function assetModal(id) {
  const p=state.data.portfolio;
  const a=state.data.assets.find(a=>a.id===Number(id)) || {currency:p.currency,kind:'stock',category:'',price:'',annual_dividend:0,target_weight:0};
  const cats=[...new Set(state.data.assets.map(x=>x.category))];
  const fields=`<div class="form-grid">${inputField('Тикер','ticker',a.ticker,'required maxlength="30" placeholder="SBER, BTC…"')}<label class="field">Тип актива<select name="kind">${Object.entries(kinds).map(([key,title])=>`<option value="${key}" ${key===a.kind?'selected':''}>${title}</option>`).join('')}</select><label class="field full">Название<input name="name" value="${esc(a.name||'')}" required maxlength="120" placeholder="Название компании или актива"></label><label class="field">Категория<input name="category" list="categories" value="${esc(a.category)}" required maxlength="80" placeholder="Например, Финансы"><datalist id="categories">${cats.map(c=>`<option value="${esc(c)}">`).join('')}</datalist></label><label class="field">Валюта<select name="currency">${currencyOptions(a.currency)}</select></label>${inputField('Текущая цена за единицу','price',a.price,'type="number" min="0" step="any" required')}${inputField('Целевая доля, %','target_weight',a.target_weight,'type="number" min="0" max="100" step="any" required')}${inputField('Выплата на единицу в год','annual_dividend',a.annual_dividend,'type="number" min="0" step="any" required')}<div class="field"><span>Прогноз выплат</span><small>В валюте актива, до налогов. Для облигаций указывайте цену и купон в денежных единицах, а не в процентах номинала.</small></div></div><p class="note">Котировка вводится вручную. Остаток актива рассчитывается из операций покупки и продажи.</p>`;
  openModal(a.id?'Редактировать актив':'Добавить актив','Активы могут быть акциями, облигациями, фондами и другими инвестициями.',modalForm('asset',fields,a.id?'Сохранить':'Добавить актив',a.id));
}
function transactionModal(id,type='buy') {
  const t=state.data.transactions.find(t=>t.id===Number(id)) || {date:state.meta.today,type,currency:state.data.portfolio.currency,asset_id:state.data.assets[0]?.id,quantity:'',price:'',amount:'',fee:0,tax:0,fx_rate:'',note:''};
  const fields=`<div class="form-grid"><label class="field">Тип операции<select name="type" id="tx-type">${Object.entries(types).map(([key,title])=>`<option value="${key}" ${key===t.type?'selected':''}>${title}</option>`).join('')}</select></label>${inputField('Дата','date',t.date,`type="date" min="1900-01-01" max="${state.meta.today}" required`)}<label class="field full" data-tx="asset">Актив<select name="asset_id" id="tx-asset">${state.data.assets.map(a=>`<option value="${a.id}" ${a.id===Number(t.asset_id)?'selected':''}>${esc(a.ticker)} — ${esc(a.name)} (${a.currency})</option>`).join('')}</select><small>${state.data.assets.length?'Валюта берётся из выбранного актива.':'Сначала добавьте актив на вкладке «Мои активы».'}</small></label><label class="field" data-tx="currency">Валюта<select name="currency" id="tx-currency">${currencyOptions(t.currency)}</select></label><label class="field" data-tx="quantity">Количество<input name="quantity" type="number" min="0.000000000001" step="any" value="${esc(t.quantity)}"></label><label class="field" data-tx="price">Цена за единицу<input name="price" id="tx-price" type="number" min="0.000000000001" step="any" value="${esc(t.price)}"></label><label class="field" data-tx="amount">Сумма до удержаний<input name="amount" type="number" min="0.000000000001" step="any" value="${esc(t.amount)}"></label>${inputField('Комиссия','fee',t.fee,'type="number" min="0" step="any" required')}<label class="field" data-tx="tax">Налог с выплаты<input name="tax" type="number" min="0" step="any" value="${esc(t.tax)}"></label><label class="field full">Курс к базовой валюте<input name="fx_rate" id="tx-rate" type="number" min="0.000000000001" step="any" value="${esc(t.fx_rate)}" placeholder="По текущему курсу из настроек"><small id="tx-rate-help">Для прошлых операций укажите исторический курс: единиц базовой валюты за единицу валюты операции.</small></label><label class="field full">Комментарий<textarea name="note" maxlength="1000" placeholder="Необязательно">${esc(t.note)}</textarea></label></div><p class="note" id="tx-total"></p>`;
  openModal(t.id?'Редактировать операцию':'Новая операция','Покупки, продажи и выплаты автоматически меняют денежный остаток.',modalForm('transaction',fields,'Сохранить операцию',t.id));
  updateTransactionForm(false);
}
function updateTransactionForm(resetRate = true) {
  const form=$('#modal form');
  if (!form || form.dataset.form!=='transaction') return;
  const type=form.elements.type.value,trade=['buy','sell'].includes(type),hasAsset=['buy','sell','dividend','coupon'].includes(type),income=['dividend','coupon'].includes(type);
  for (const [name,show] of Object.entries({asset:hasAsset,currency:!hasAsset,quantity:trade,price:trade,amount:!trade,tax:income})) {
    const el=$(`[data-tx="${name}"]`,form);
    el.hidden=!show;
    const input=$('input,select',el);
    input.disabled=!show;
    input.required=show;
  }
  const asset=state.data.assets.find(a=>a.id===Number(form.elements.asset_id.value));
  const cur=hasAsset?asset?.currency:form.elements.currency.value;
  const base=state.data.portfolio.currency;
  const rub=c=>num(state.meta.rates.find(r=>r.currency===c)?.rub);
  const rate=cur===base?1:(rub(cur)&&rub(base)?rub(cur)/rub(base):null);
  if (resetRate) form.elements.fx_rate.value='';
  $('#tx-rate').placeholder=rate?`Текущий курс: ${decimal(rate,8)}`:'Укажите курс или настройте курсы валют';
  $('#tx-rate-help').textContent=`1 ${cur||'ед.'} = ${rate?decimal(rate,8):'?'} ${base} по настройкам. Для прошлых операций укажите исторический курс.`;
  const quantity=num(form.elements.quantity.value),price=num(form.elements.price.value);
  $('#tx-total').textContent=trade&&cur?`Сумма сделки: ${money(quantity*price,cur,2)} · комиссия указывается отдельно`:income?'Укажите полную сумму выплаты, налог и комиссию — отдельно.':'';
}
function importModal() {
  importText='';importValidated=false;
  openModal('Импорт операций CSV','Универсальный формат. Произвольные отчёты брокеров сначала нужно привести к шаблону.',`<form data-form="import"><div class="form-error" role="alert"></div><div class="inline-help">Заголовки столбцов — на английском. Разделитель: запятая или точка с запятой. Даты: ГГГГ-ММ-ДД. Все суммы — в валюте операции.<br><a href="/example.csv" download>Скачать пример CSV</a> · <a href="/api/portfolios/${state.pid}/export" download>Экспортировать текущие операции</a></div><label class="field">CSV-файл<input id="csv-file" type="file" accept=".csv,text/csv" required><small>До 1 МБ и 5000 операций. Сначала задайте курсы используемых валют. Без fx_rate применяется текущий курс.</small></label><div id="import-preview"></div><div class="form-actions"><button type="button" class="button" data-action="close-modal">Отмена</button><button class="button primary" type="submit" id="import-submit">Проверить файл</button></div></form>`);
}
function deleteModal(resource,id) {
  const isPortfolio=resource==='portfolio';
  let name=state.data.portfolio.name;
  if(resource==='asset') name=state.data.assets.find(a=>a.id===Number(id))?.ticker;
  if(resource==='transaction'){const t=state.data.transactions.find(t=>t.id===Number(id));name=`${types[t.type]} · ${day(t.date)}`;}
  openModal('Удалить запись?',`${esc(name)}. ${isPortfolio?'Будут удалены все активы, операции и история этого портфеля.':'Действие нельзя отменить. Для связанных операций будут проверены остатки активов.'}`,modalForm(`delete-${resource}`,isPortfolio?'<label class="field">Введите название портфеля для подтверждения<input name="confirmation" autocomplete="off" required></label>':'','Удалить',id));
  $('#modal button[type=submit]').className='button danger';
}

document.addEventListener('click',async event=>{
  const el=event.target.closest('[data-action]');
  if (!el) return;
  const {action,id}=el.dataset;
  try {
    if (action==='close-modal') $('#modal').close();
    else if(action==='change-password') passwordModal();
    else if(action==='new-portfolio') portfolioModal();
    else if(action==='edit-portfolio') portfolioModal(true);
    else if(action==='new-asset') assetModal();
    else if(action==='edit-asset') assetModal(id);
    else if(action==='new-transaction') transactionModal();
    else if(action==='edit-transaction') transactionModal(id);
    else if(action==='new-dividend') transactionModal(null,'dividend');
    else if(action==='deposit') transactionModal(null,'deposit');
    else if(action==='import') importModal();
    else if(action==='settings') location.hash='settings';
    else if(action==='delete-portfolio') deleteModal('portfolio',state.pid);
    else if(action==='delete-asset') deleteModal('asset',id);
    else if(action==='delete-transaction') deleteModal('transaction',id);
    else if(action==='period') {state.period=el.dataset.period;render();}
    else if(action==='retry') await refresh();
    else if(action==='demo') {el.disabled=true;const result=await api('/demo','POST',{});location.hash='overview';await refresh(result.id);toast('Демонстрационный портфель готов');}
  } catch(error) {toast(error.message,true);}
  finally {el.disabled=false;}
});
document.addEventListener('submit',async event=>{
  const form=event.target.closest('form[data-form]');
  if(!form) return;
  event.preventDefault();
  const errorBox=$('.form-error',form),button=$('button[type=submit]',form);
  errorBox.textContent='';button.disabled=true;
  const data=Object.fromEntries(new FormData(form)),kind=form.dataset.form,id=form.dataset.id;
  let nextPid=state.pid,message='Изменения сохранены';
  try {
    if(kind==='portfolio'){
      const result=await api(id?`/portfolios/${id}`:'/portfolios',id?'PUT':'POST',data);
      if(!id){nextPid=result.id;state.search='';state.category='';location.hash='overview';message='Портфель создан';}
    } else if(kind==='asset'){
      await api(`/portfolios/${state.pid}/assets${id?`/${id}`:''}`,id?'PUT':'POST',data);
      message=id?'Актив обновлён':'Актив добавлен. Теперь внесите покупку.';
    } else if(kind==='transaction'){
      await api(`/portfolios/${state.pid}/transactions${id?`/${id}`:''}`,id?'PUT':'POST',data);
      message='Операция сохранена';
    } else if(kind==='rates'){
      await api('/rates','PUT',{rates:Object.fromEntries(Object.entries(data).filter(([,v])=>v!==''))});
      message='Курсы сохранены';
    } else if(kind.startsWith('delete-')){
      const resource=kind.slice(7);
      if(resource==='portfolio'&&data.confirmation!==state.data.portfolio.name)throw new Error('Название не совпадает. Введите его точно, включая регистр.');
      await api(`/portfolios/${state.pid}${resource==='portfolio'?'':`/${resource==='asset'?'assets':'transactions'}/${id}`}`,'DELETE');
      if(resource==='portfolio'){nextPid=null;localStorage.removeItem('invest-portfolio');state.pid=null;}
      message='Запись удалена';
    } else if(kind==='import'){
      if(!importText)throw new Error('Выберите CSV-файл');
      const result=await api(`/portfolios/${state.pid}/import`,'POST',{csv:importText,dry_run:!importValidated});
      if(!importValidated){
        importValidated=true;
        $('#import-preview').innerHTML=`<div class="import-preview"><strong>Файл проверен</strong><br>Операций: ${result.count} · Новых активов: ${result.created_assets}<ul>${result.preview.slice(0,5).map(row=>`<li>${esc(row.date)} · ${types[row.type]} ${esc(row.ticker)}</li>`).join('')}</ul>${result.count>5?`<small>И ещё ${result.count-5} операций</small>`:''}</div>`;
        button.textContent=`Импортировать ${result.count} операций`;
        return;
      }
      message=`Импортировано операций: ${result.count}`;
    }
    if($('#modal').open)$('#modal').close();
    await refresh(nextPid);
    toast(message);
  } catch(error){if(form.isConnected && (!form.closest('dialog') || $('#modal').open)){errorBox.textContent=error.message;errorBox.scrollIntoView({block:'nearest'});}else toast(error.message,true);}
  finally{button.disabled=false;}
});
document.addEventListener('input',event=>{
  if(event.target.id==='asset-search') {state.search=event.target.value;$('#asset-rows').innerHTML=assetRows(filteredAssets());}
  if(['quantity','price','fee'].includes(event.target.name)&&event.target.closest('[data-form="transaction"]')) updateTransactionForm(false);
});
document.addEventListener('change',async event=>{
  const el=event.target;
  try {
    if(el.id==='portfolio-select'){state.search='';state.category='';await refresh(Number(el.value));}
    else if(el.id==='category-filter'){state.category=el.value;$('#asset-rows').innerHTML=assetRows(filteredAssets());}
    else if(el.id==='transaction-filter'){state.txType=el.value;$('#transaction-rows').innerHTML=transactionRows(state.data.transactions.filter(t=>!state.txType||t.type===state.txType));}
    else if(['tx-type','tx-asset','tx-currency'].includes(el.id))updateTransactionForm();
    else if(el.id==='csv-file'){
      importValidated=false;importText='';$('#import-preview').innerHTML='';$('#import-submit').textContent='Проверить файл';
      if(el.files[0]){
        if(el.files[0].size>1_000_000)throw new Error('Файл больше 1 МБ');
        importText=await el.files[0].text();
      }
    }
  }catch(error){const box=$('.form-error',$('#modal'));if(box&&$('#modal').open)box.textContent=error.message;else toast(error.message,true);}
});
$('#menu-toggle').addEventListener('click',()=>{const open=$('#sidebar').classList.toggle('open');$('#menu-toggle').setAttribute('aria-expanded',String(open));});
$('#logout').addEventListener('click',async()=>{
  try{await api('/auth/logout','POST',{});location.replace('/login.html');}
  catch(error){toast(error.message,true);}
});
$('#change-password').addEventListener('click',passwordModal);
document.addEventListener('submit',async event=>{
  const form=event.target.closest('form[data-password-form]');
  if(!form)return;
  event.preventDefault();
  const errorBox=$('.form-error',form),button=$('button[type=submit]',form);
  const values=Object.fromEntries(new FormData(form));
  errorBox.textContent='';
  if(values.new_password!==values.confirm_password){errorBox.textContent='Новый пароль и подтверждение не совпадают';return;}
  if(values.new_password===values.current_password){errorBox.textContent='Новый пароль должен отличаться от текущего';return;}
  button.disabled=true;
  try{
    await api('/auth/password','POST',values);
    form.reset();location.replace('/login.html?changed=1');
  }catch(error){errorBox.textContent=error.message;form.elements.current_password.value='';form.elements.current_password.focus();}
  finally{button.disabled=false;}
});
document.addEventListener('click',event=>{if(!event.target.closest('.sidebar,#menu-toggle')){$('#sidebar').classList.remove('open');$('#menu-toggle').setAttribute('aria-expanded','false');}});
window.addEventListener('hashchange',()=>{if(state.meta)render();$('#sidebar').classList.remove('open');$('#menu-toggle').setAttribute('aria-expanded','false');});
refresh().catch(error=>{
  $('#main').innerHTML=`<section class="card error-state"><h1>Не удалось загрузить портфель</h1><p>${esc(error.message)}. Убедитесь, что локальный сервер запущен и база данных доступна.</p><button class="button primary" data-action="retry">Повторить</button></section>`;
});
