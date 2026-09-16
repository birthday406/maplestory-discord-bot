// 공개 게임 기록만 조회합니다. 로그인과 Discord 개인 조회 이력은 사용하지 않습니다.
function rankingsPage() {
  const route=new URLSearchParams(location.hash.split('?')[1]||'');
  const selectedName=route.get('nickname')||'';
  const selectedWorld=route.get('world')||'45';
  const root=document.querySelector('#app');
  root.innerHTML=`<div class="heading"><div><span class="eyebrow">MAPLE RANKINGS</span><h1>우리의 메이플 기록</h1><p>캐릭터를 검색하거나 월드별 성장 순위를 살펴보세요.</p></div></div>
  <form class="ranking-search"><label>월드<select id="ranking-world"><option value="45">Kronos</option><option value="19">Scania</option><option value="1">Bera</option><option value="70">Hyperion</option></select></label><label>캐릭터 이름<input id="ranking-name" placeholder="닉네임 입력" maxlength="12" autocomplete="off"></label><button class="primary" type="submit">캐릭터 검색</button></form>
  <p class="ranking-note">Lv.260 이상 · 수집 기록 기준 상위 100명</p><p id="ranking-status" role="status"></p><section id="ranking-result"></section>`;
  const form=root.querySelector('form'),world=root.querySelector('#ranking-world'),input=root.querySelector('#ranking-name');
  const status=root.querySelector('#ranking-status'),result=root.querySelector('#ranking-result');
  world.value=['45','19','1','70'].includes(selectedWorld)?selectedWorld:'45';
  input.value=selectedName;
  if(selectedName){root.querySelector('.ranking-note').hidden=true;root.querySelector('h1').textContent='캐릭터 상세';root.querySelector('.heading p').textContent='수집된 캐릭터 정보와 성장 기록을 확인하세요.';}
  let serial=0;
  const element=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  const number=value=>value==null?'기록 없음':Number(value).toLocaleString('ko-KR');
  // 사진은 HTTPS 주소만 사용하고 실패하면 동일 크기의 기본 그림을 표시합니다.
  function portrait(c) {
    const img=element('img');img.className='ranking-avatar';img.alt=c.name+' 캐릭터';img.loading='lazy';img.referrerPolicy='no-referrer';
    let url;try{url=new URL(c.image_url);}catch{}
    img.src=url&&url.protocol==='https:'?url.href:'sherbet.png';
    img.onerror=()=>{img.onerror=null;img.src='sherbet.png';};return img;
  }
  function detailLink(c) {const a=element('a',c.name);a.className='character-link';a.href='#rankings?'+new URLSearchParams({world:world.value,nickname:c.name});return a;}
  function table(headers,rows) {
    const wrap=element('div');wrap.className='ranking-table-wrap'+(headers[1]==='캐릭터 사진'?' ranking-list-table':'');const t=element('table');
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
      status.textContent='';
      if(data.character) {
        const c=data.character;const card=element('article');card.className='setting-block ranking-profile';
        const back=element('a','↖ 월드 순위표로');back.href='#rankings?'+new URLSearchParams({world:world.value});back.className='ranking-back';result.append(back);
        card.append(portrait(c));
        card.append(element('h2',c.name),element('p',`${c.world} · ${c.job_name} · Lv.${c.level}`),element('p',`기준일 ${c.updated_date} UTC`));
        card.append(element('p',c.level===300?'최고 레벨 달성':`다음 레벨까지 ${number(c.remainingExp)} EXP`));
        card.append(element('p',`유니온 ${c.legion_level>0?number(c.legion_level):'대표 기록 없음'} · 업적 ${c.achievement_score>0?number(c.achievement_score):'대표 기록 없음'}`));result.append(card);
        const stats=element('dl');stats.className='ranking-stats';
        const rank=v=>v>0?number(v)+'위':'기록 없음';
        for(const [label,value] of [['월드 순위',rank(c.worldRank)],['유니온 순위',rank(c.legion_rank)],['업적 순위',rank(c.achievement_rank)],['현재 경험치',number(c.exp)+' EXP'],['경험치 진행률',c.requiredExp?(c.exp/c.requiredExp*100).toFixed(3)+'%':'최고 레벨']]){const box=element('div');box.append(element('dt',label),element('dd',value));stats.append(box);}card.append(stats);
        result.append(element('p','공개 랭킹에서 수집한 정보입니다. 장비·상세 스탯은 제공되지 않습니다.'));
        result.append(element('h2','최근 경험치 변화'));
        if(!data.gains.length)result.append(element('p','비교할 날짜별 기록이 아직 부족합니다.'));
        else {const max=Math.max(1,...data.gains.map(g=>g.exp||0));result.append(table(['기준일 (UTC)','비교 기간','획득 경험치'],data.gains.map(g=>{const bar=element('div',number(g.exp));bar.className='exp-bar';bar.style.setProperty('--gain',Math.max(0,(g.exp||0)/max*100)+'%');return [g.date,g.days+'일',bar];})));}
      } else {
        const dates=[...new Set(data.rows.map(c=>c.updated_date).filter(Boolean))].sort();
        status.textContent=dates.length>1?`갱신 중 · 기록 기준 ${dates[0]} ~ ${dates.at(-1)} UTC`:(dates.length?`기록 기준 ${dates[0]} UTC`:'');
        result.append(element('h2',data.world+' · 공식 월드 랭킹'));
        if(!data.rows.length){result.append(element('p','이 월드의 저장된 기록이 아직 없습니다.'));return;}
        result.append(element('p','공식 월드 순위를 그대로 표시합니다. 수집 시점 이후의 변경은 다음 갱신에 반영됩니다.'));
        result.append(table(['순위','캐릭터 사진','캐릭터','레벨·직업'],data.rows.map((c,i)=>{const photo=detailLink(c);photo.replaceChildren(portrait(c));const info=element('div');info.className='ranking-job';info.append(element('strong','Lv.'+c.level),element('span',c.job_name));return [c.ranking,photo,detailLink(c),info];})));
      }
    }catch{if(request===serial&&result.isConnected)status.textContent='연결에 실패했습니다. 잠시 후 다시 시도해주세요.';}
  }
  form.onsubmit=e=>{e.preventDefault();const name=input.value.trim();if(!name){status.textContent='검색할 캐릭터 이름을 입력해주세요.';return;}location.hash='rankings?'+new URLSearchParams({world:world.value,nickname:name});};
  world.onchange=()=>{location.hash='rankings?'+new URLSearchParams({world:world.value});};load(selectedName);
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
