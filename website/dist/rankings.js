// 공개 게임 기록만 조회합니다. 로그인과 Discord 개인 조회 이력은 사용하지 않습니다.
function rankingsPage() {
  const root=document.querySelector('#app');
  root.innerHTML=`<div class="heading"><div><span class="eyebrow">MAPLE RANKINGS</span><h1>우리의 메이플 기록</h1><p>캐릭터를 검색하거나 월드별 성장 순위를 살펴보세요.</p></div></div>
  <form class="ranking-search"><label>월드<select id="ranking-world"><option value="45">Kronos</option><option value="19">Scania</option><option value="1">Bera</option><option value="70">Hyperion</option></select></label><label>캐릭터 이름<input id="ranking-name" placeholder="닉네임 입력" maxlength="12" autocomplete="off"></label><button class="primary" type="submit">캐릭터 검색</button><button type="button" id="ranking-list">월드 순위표</button></form>
  <p class="ranking-note">샤벳 수집 기록 · Lv.260 이상 · 각 캐릭터의 기준일은 UTC입니다. 실시간 공식 순위와 다를 수 있으며 저장된 순위표는 최대 100명까지 표시합니다.</p><p id="ranking-status" role="status"></p><section id="ranking-result"></section>`;
  const form=root.querySelector('form'),world=root.querySelector('#ranking-world'),input=root.querySelector('#ranking-name');
  const status=root.querySelector('#ranking-status'),result=root.querySelector('#ranking-result');
  let serial=0;
  const element=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  const number=value=>value==null?'기록 없음':Number(value).toLocaleString('ko-KR');
  function table(headers,rows) {
    const wrap=element('div');wrap.className='ranking-table-wrap';const t=element('table');
    const thead=element('thead'),tr=element('tr');for(const title of headers){const th=element('th',title);th.scope='col';tr.append(th);}thead.append(tr);t.append(thead);
    const body=element('tbody');for(const cells of rows){const row=element('tr');for(const value of cells){const td=element('td');if(value instanceof Node)td.append(value);else td.textContent=value;row.append(td);}body.append(row);}t.append(body);wrap.append(t);return wrap;
  }
  async function load(nickname='') {
    const request=++serial;status.textContent='기록을 불러오는 중…';result.replaceChildren();
    try {
      const params=new URLSearchParams({world:world.value});if(nickname)params.set('nickname',nickname);
      const response=await fetch('/api/rankings?'+params);
      if(request!==serial||!result.isConnected)return;
      if(!response.ok){status.textContent=({400:'닉네임 또는 월드를 확인해주세요. 닉네임은 최대 12바이트입니다.',404:'저장된 캐릭터가 없습니다. 자동 수집 대상은 Lv.260 이상입니다.',429:'조회 요청이 많습니다. 잠시 후 다시 시도해주세요.',503:'랭킹 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.'})[response.status]||'조회에 실패했습니다.';return;}
      const data=await response.json();if(request!==serial||!result.isConnected)return;
      status.textContent='저장된 기록 기준 · 최대 1분 캐시';
      if(data.character) {
        const c=data.character;const card=element('article');card.className='setting-block ranking-profile';
        card.append(element('h2',c.name),element('p',`${c.world} · ${c.job_name} · Lv.${c.level}`),element('p',`기준일 ${c.updated_date} UTC`));
        card.append(element('p',c.level===300?'최고 레벨 달성':`다음 레벨까지 ${number(c.remainingExp)} EXP`));
        card.append(element('p',`유니온 ${c.legion_level>0?number(c.legion_level):'대표 기록 없음'} · 업적 ${c.achievement_score>0?number(c.achievement_score):'대표 기록 없음'}`));result.append(card);
        result.append(element('h2','최근 경험치 변화'));
        if(!data.gains.length)result.append(element('p','비교할 날짜별 기록이 아직 부족합니다.'));
        else {const max=Math.max(1,...data.gains.map(g=>g.exp||0));result.append(table(['기준일 (UTC)','비교 기간','획득 경험치'],data.gains.map(g=>{const bar=element('div',number(g.exp));bar.className='exp-bar';bar.style.setProperty('--gain',Math.max(0,(g.exp||0)/max*100)+'%');return [g.date,g.days+'일',bar];})));}
      } else {
        result.append(element('h2',data.world+' · 경험치 순위표'));
        if(!data.rows.length){result.append(element('p','이 월드의 저장된 기록이 아직 없습니다.'));return;}
        result.append(element('p','수집된 캐릭터를 레벨·현재 경험치 순으로 정렬합니다. 동률은 닉네임 순서입니다.'));
        result.append(table(['순서','캐릭터','레벨','직업','기준일 (UTC)'],data.rows.map((c,i)=>{const button=element('button',c.name);button.className='character-link';button.onclick=()=>{input.value=c.name;load(c.name);};return [i+1,button,c.level,c.job_name,c.updated_date];})));
      }
    }catch{if(request===serial&&result.isConnected)status.textContent='연결에 실패했습니다. 잠시 후 다시 시도해주세요.';}
  }
  form.onsubmit=e=>{e.preventDefault();const name=input.value.trim();if(!name){status.textContent='검색할 캐릭터 이름을 입력해주세요.';return;}load(name);};
  root.querySelector('#ranking-list').onclick=()=>{input.value='';load();};world.onchange=()=>{input.value='';load();};load();
}

async function showSalePeriod(card) {
  try {
    const response=await fetch('/sales.json');if(!response.ok)return;const sale=await response.json();if(!card.isConnected)return;
    const start=new Date(sale.start),end=new Date(sale.endExclusive),now=new Date();
    const text=document.createElement('p');text.className='sale-period';
    const format=date=>date.toLocaleString('ko-KR',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',timeZoneName:'short'});
    text.textContent=(now<start?'판매 시작 전':now>=end?'현재 진행 중인 판매가 아닙니다.':'판매 진행 중')+'\n시작: '+format(start)+' (점검 완료 공지 기준)\n종료: '+format(new Date(end-1000));card.append(text);
  }catch{ /* 기간 조회 실패를 판매 종료로 오인하지 않습니다. */ }
}
