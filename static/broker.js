'use strict';

// Tokens exist only in the password field and the local request body, never in application state.
const brokerOperationNames = {
  INPUT:'Пополнение',OUTPUT:'Вывод',BUY:'Покупка',SELL:'Продажа',BUY_CARD:'Покупка с карты',
  DIVIDEND:'Дивиденды',COUPON:'Купон',BROKER_FEE:'Комиссия брокера',SERVICE_FEE:'Обслуживание',
  TAX:'Налог',BOND_TAX:'Налог с купонов',DIVIDEND_TAX:'Налог с дивидендов',
  BOND_REPAYMENT_FULL:'Погашение облигации',BOND_REPAYMENT:'Амортизация',
  INPUT_SECURITIES:'Ввод бумаг',OUTPUT_SECURITIES:'Вывод бумаг',
};
function brokerAmount(value) {
  if(!value || typeof value!=='object') return null;
  return num(value.units)+num(value.nano)/1e9;
}
function brokerMoney(value, fallback='RUB') {
  const amount=brokerAmount(value);
  if(amount===null) return '—';
  try{return money(amount,(value.currency||fallback).toUpperCase(),2);}catch{return `${decimal(amount,2)} ${esc(value.currency||fallback)}`;}
}
function brokerDate(value) {
  if(!value) return '—';
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return '—';
  return new Intl.DateTimeFormat('ru-RU',{dateStyle:'medium',timeStyle:'short'}).format(date);
}
function brokerPage() {
  const broker=state.broker,account=state.brokerAccount;
  const configured=broker?.configured;
  return `<div class="page-heading"><div><div class="eyebrow">ПОДКЛЮЧЕНИЕ БРОКЕРА</div><h1>Т-Инвестиции</h1><p>Счета, позиции и операции из вашего брокерского кабинета.</p></div><div class="heading-actions"><a class="button" href="/api/backup" download>${icon('download')} Резервная копия</a>${configured?'<button class="button" data-broker-action="accounts">'+icon('swap')+' Обновить счета</button>':''}</div></div>
    <section class="card panel broker-connection"><details ${configured?'':'open'}><summary>${icon('shield')} ${configured?'Подключено · управление токеном':'Подключить Т-Инвестиции'} <span class="badge ${configured?'green':''}">${configured?'Только чтение':'Не подключено'}</span></summary><p class="note">Создайте токен с правами «Только чтение» в <a href="https://www.tbank.ru/invest/settings/api/" target="_blank" rel="noopener noreferrer">настройках Т-Инвестиций</a>. Можно ограничить его одним счётом.</p>${configured?'<p class="note">При смене доступных счетов их недоступные локальные копии удаляются. Сначала сохраните резервную копию.</p>':''}<form data-broker-form="token"><div class="form-error" role="alert"></div><label class="field">${configured?'Новый токен для замены':'Токен доступа'}<input type="password" name="token" autocomplete="off" spellcheck="false" autocapitalize="off" minlength="20" maxlength="1024" required placeholder="${configured?'Сохранённый токен не отображается':'Вставьте токен с правами только чтения'}"><small>Передаётся локальному серверу и хранится зашифрованным. После сохранения поле очищается.</small></label><div class="form-actions">${configured?'<button class="button danger" type="button" data-broker-action="disconnect">Удалить подключение</button>':''}<button class="button primary" type="submit">${configured?'Проверить и заменить':'Проверить и сохранить'}</button></div></form></details></section>
    ${broker?.accounts.length?`<section class="card panel broker-account-controls"><div class="form-grid"><label class="field">Брокерский счёт<select id="broker-account-select">${broker.accounts.map(a=>`<option value="${esc(a.id)}" ${a.id===state.brokerId?'selected':''}>${esc(a.info.name||'Счёт')} · ${esc(a.id)}${a.info.status==='ACCOUNT_STATUS_CLOSED'?' · закрыт':''}</option>`).join('')}</select></label><label class="field">Начало истории операций<input id="broker-from" type="date" min="1900-01-01" max="${state.meta.today}" value="${esc(account?.operations_from?.slice(0,10)||account?.info.openedDate?.slice(0,10)||state.meta.today)}"></label></div><div class="broker-sync-row"><p class="note">${account?.synced_at?`Сохранённая копия: ${brokerDate(account.synced_at)}`:'Данные счёта ещё не загружены.'}<br>Синхронизация обновляет текущие позиции и операции с выбранной даты.</p><button class="button primary" data-broker-action="sync" ${configured?'':'disabled'}>${icon('download')} Синхронизировать</button></div><div class="form-error" id="broker-error" role="alert"></div><p id="broker-progress" class="note" role="status"></p></section>`:''}
    ${account?.snapshot?brokerSnapshot(account):`<section class="card panel"><div class="chart-empty">${icon('wallet')}<span>${configured?'Выберите счёт и нажмите «Синхронизировать».':'Подключите токен, чтобы загрузить свои счета.'}</span></div></section>`}`;
}
function brokerSnapshot(account) {
  const p=account.snapshot.portfolio,positions=account.snapshot.positions;
  const list=p.positions||[],virtual=p.virtualPositions||[];
  const balance=positions.money||[],blocked=positions.blocked||[];
  const metricsHTML=`<div class="cards">${metric('Стоимость счёта',brokerMoney(p.totalAmountPortfolio),'По оценке Т-Инвестиций · RUB','wallet')}${metric('Изменение за день',brokerMoney(p.dailyYield),'По данным брокера · RUB','chart',false,tone(brokerAmount(p.dailyYield)))}${metric('Валюта в портфеле',brokerMoney(p.totalAmountCurrencies),'Стоимость валютных позиций · RUB','swap')}${metric('Позиций на счёте',String(list.length),'Количество инструментов, включая валюту','grid',true)}</div>`;
  const rows=list.map(a=>`<tr><td><div class="asset-name"><span class="asset-logo" style="background:#edf4e9;color:#72996c">${esc((a.ticker||a.figi||'?').slice(0,2))}</span><div><strong>${esc(a.ticker||a.figi||a.instrumentUid)}</strong><small>${esc(a.classCode||a.instrumentType||'')}</small></div></div></td><td>${esc(({share:'Акция',bond:'Облигация',etf:'Фонд',currency:'Валюта',futures:'Фьючерс',option:'Опцион'})[a.instrumentType]||a.instrumentType||'—')}</td><td class="numeric">${a.quantity?decimal(brokerAmount(a.quantity),9):'—'}</td><td class="numeric">${brokerMoney(a.averagePositionPrice,a.currentPrice?.currency)}<small class="cell-sub">Средняя цена брокера</small></td><td class="numeric">${brokerMoney(a.currentPrice)}${a.currentNkd?`<small class="cell-sub">НКД: ${brokerMoney(a.currentNkd)}</small>`:''}</td><td class="numeric ${tone(brokerAmount(a.expectedYield))}">${a.expectedYield?brokerMoney(a.expectedYield,a.currentPrice?.currency):'—'}</td><td>${a.blocked?'<span class="badge amber">Заблокировано</span>':'—'}</td></tr>`).join('');
  return metricsHTML+`<section class="card table-panel"><div class="panel-header"><div><h2>Текущие позиции</h2><p class="panel-subtitle">Количество в штуках. Цены и результат позиции — в валюте инструмента.</p></div><span class="badge green">Данные брокера</span></div><div class="table-scroll"><table><thead><tr><th>Инструмент</th><th>Тип</th><th class="numeric">Количество</th><th class="numeric">Средняя цена</th><th class="numeric">Текущая цена</th><th class="numeric">Результат позиции</th><th>Статус</th></tr></thead><tbody>${rows||'<tr><td colspan="7" class="empty-row">На счёте нет открытых позиций.</td></tr>'}</tbody></table></div><div class="table-footer"><span>Итоговая стоимость и результат получены от брокера. Результат позиций не равен прибыли за всё время.</span></div></section>
  ${virtual.length?`<section class="card panel" style="margin-bottom:22px"><h2>Виртуальные позиции · ${virtual.length}</h2><p class="note">Виртуальные позиции учитываются брокером отдельно от обычных бумаг.</p><div class="cash-list">${virtual.map(a=>`<div class="cash-chip"><span>${esc(a.ticker||a.figi||a.instrumentUid)}</span><strong>${decimal(brokerAmount(a.quantity),9)}</strong></div>`).join('')}</div></section>`:''}
  <div class="grid-two"><section class="card panel"><h2>Денежные остатки</h2><div class="cash-list">${balance.map(a=>`<div class="cash-chip"><span>${esc(a.currency?.toUpperCase())}</span><strong>${brokerMoney(a)}</strong></div>`).join('')||'<p class="note">Денежных остатков нет.</p>'}</div><p class="note">Данные GetPositions. Заблокированные суммы показаны отдельно.</p></section><section class="card panel"><h2>Заблокированные деньги</h2><div class="cash-list">${blocked.map(a=>`<div class="cash-chip"><span>${esc(a.currency?.toUpperCase())}</span><strong>${brokerMoney(a)}</strong></div>`).join('')||'<p class="note">Заблокированных денег нет.</p>'}</div></section></div>
  <section class="card table-panel"><div class="panel-header"><div><h2>Операции брокера <span class="badge">${account.operations_count}</span></h2><p class="panel-subtitle">Последняя загрузка: ${brokerDate(account.operations_from)} — ${brokerDate(account.operations_to)}</p></div></div><div class="table-scroll"><table><thead><tr><th>Дата</th><th>Операция</th><th>Инструмент</th><th class="numeric">Количество</th><th class="numeric">Денежный поток</th><th class="numeric">Комиссия</th></tr></thead><tbody>${(account.operations||[]).map(brokerOperationRow).join('')||'<tr><td colspan="6" class="empty-row">Исполненных операций за выбранный период нет.</td></tr>'}</tbody></table></div><div class="table-footer"><span>Показано ${account.operations.length} из ${account.operations_count}. Суммы и комиссии — как их передал брокер; они не складываются повторно.</span>${account.operations.length<account.operations_count?'<button class="text-button" data-broker-action="more">Показать ещё 100</button>':''}</div></section><p class="note">Брокерские счета хранятся в отдельном разделе. Ручные портфели и расчёт их доходности ведутся независимо. Данные обновляются по кнопке; между синхронизациями отображается сохранённая копия.</p>`;
}
function brokerOperationRow(operation) {
  const key=String(operation.type||'').replace('OPERATION_TYPE_','');
  return `<tr><td>${brokerDate(operation.date)}</td><td><strong class="cell-main">${esc(brokerOperationNames[key]||operation.name||key||'Операция')}</strong><small class="cell-sub" title="${esc(operation.description)}">${esc((operation.description||'').slice(0,65))}</small></td><td>${esc(operation.ticker||operation.figi||'—')}</td><td class="numeric">${esc(operation.quantityDone||operation.quantity||'—')}</td><td class="numeric ${tone(brokerAmount(operation.payment))}">${brokerMoney(operation.payment)}</td><td class="numeric">${brokerMoney(operation.commission,operation.payment?.currency)}</td></tr>`;
}
document.addEventListener('submit',async event=>{
  const form=event.target.closest('form[data-broker-form]');
  if(!form)return;
  event.preventDefault();
  const errorBox=$('.form-error',form),button=$('button[type=submit]',form);
  errorBox.textContent='';button.disabled=true;
  try{
    if(form.dataset.brokerForm==='token'){
      const token=form.elements.token.value.trim();
      form.elements.token.value='';
      await api('/tbank/token','PUT',{token});
      toast('Подключение сохранено. Выберите счёт и синхронизируйте данные.');
    }else if(form.dataset.brokerForm==='disconnect'){
      await api('/tbank/token','DELETE');
      $('#modal').close();
      toast('Локальный токен удалён. Сохранённые данные счетов доступны.');
    }
    await refresh();
  }catch(error){if(form.isConnected)errorBox.textContent=error.message;else toast(error.message,true);}
  finally{button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-broker-action]');
  if(!button)return;
  const action=button.dataset.brokerAction;
  button.disabled=true;
  const box=$('#broker-error');if(box)box.textContent='';
  try{
    if(action==='accounts'){await api('/tbank/accounts','POST',{});await refresh();toast('Список счетов обновлён');}
    else if(action==='sync'){
      const from=$('#broker-from').value;
      if(!from)throw new Error('Укажите дату начала истории');
      $('#broker-progress').textContent='Загружаем позиции и все страницы операций. Это может занять несколько минут. Сохранённые данные обновятся после завершения.';
      const result=await api(`/tbank/accounts/${encodeURIComponent(state.brokerId)}/sync`,'POST',{from});
      await refresh();toast(`Счёт обновлён. Получено операций: ${result.operations}`);
    }else if(action==='more'){
      const page=await api(`/tbank/accounts/${encodeURIComponent(state.brokerId)}/operations`,'POST',{offset:state.brokerAccount.operations.length});
      state.brokerAccount.operations.push(...page.operations);
      state.brokerAccount.operations_count=page.operations_count;render();
    }else if(action==='disconnect'){
      openModal('Удалить подключение?','Локальный токен будет удалён. Сохранённая история счетов останется. Для отзыва токена в банке удалите его в настройках Т-Инвестиций.',`<form data-broker-form="disconnect"><div class="form-error" role="alert"></div><div class="form-actions"><button class="button" type="button" data-action="close-modal">Отмена</button><button class="button danger" type="submit">Удалить токен</button></div></form>`);
    }
  }catch(error){if(box?.isConnected)box.textContent=error.message;else toast(error.message,true);}
  finally{button.disabled=false;const progress=$('#broker-progress');if(progress)progress.textContent='';}
});
document.addEventListener('change',async event=>{
  if(event.target.id!=='broker-account-select')return;
  try{state.brokerId=event.target.value;await refresh();}catch(error){toast(error.message,true);}
});
