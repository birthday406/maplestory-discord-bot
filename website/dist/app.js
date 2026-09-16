const $=s=>document.querySelector(s), app=$('#app');
const servers=[{id:'a',name:'메이플 쉼터',channels:[['news','메이플-공지','text'],['chat','자유게시판','text'],['event','이벤트-알림','text'],['locked','운영진-전용','locked'],['utc','UTC 시간','voice']]},{id:'b',name:'샤벳 테스트 길드',channels:[['news','공지사항','text'],['event','길드-이벤트','text'],['time','한국 시간','voice']]}];
const settings=[['news','한국어 공식 공지','패치·알려진 이슈 수정도 함께 전달해요.'],['sunny','썬데이 당일 알림','이번 주 혜택이 시작될 때 알려드려요.'],['cash','캐시이동 알림','시작일과 종료 전 알림을 받아요.'],['miracle','미라클 타임 알림','미라클 타임과 종료 전 알림을 받아요.'],['server','서버 오픈 알림','공식 접속 재개 안내가 나오면 알려드려요.'],['cube','큐브세일 알림','현재는 받을 채널 예약만 지원해요.']];
let server='a',channel='news',filter='전체',query='',saved={},draft={},dirty=false;
// 가상 설정만 브라우저에 보관하며, 손상된 저장값은 기본 설정으로 대신합니다.
try{const data=JSON.parse(localStorage.getItem('sherbet-demo-settings')||'{}');if(data&&typeof data==='object'&&!Array.isArray(data))saved=data}catch{}
const key=()=>server+':'+channel;
const current=()=>servers.find(s=>s.id===server).channels.find(c=>c[0]===channel);
function load(){draft=structuredClone(saved[key()]||{news:channel==='news',sunny:channel==='event',role:'',display:'off'});dirty=false}
load();
function notice(title,text){$('#notice-title').textContent=title;$('#notice-text').textContent=text;$('#notice').showModal()}
$('#login').onclick=()=>{location.hash='account'};
$('.close').onclick=$('.close-action').onclick=()=>$('#notice').close();
function toast(text){$('#toast').textContent=text;$('#toast').classList.add('show');setTimeout(()=>$('#toast').classList.remove('show'),3000)}
function leave(){return !dirty||confirm('저장하지 않은 변경사항을 버리고 이동할까요?')}
// 공개 대시보드는 실제 설정으로 연결하고 가상 시안은 preview 주소에서만 엽니다.
function render(){
 let page=location.hash.slice(1)||'home';
 if(page==='dashboard'){history.replaceState(null,'','#account');page='account'}
 const banner=document.querySelector('.demo');
 banner.hidden=page!=='preview'&&page!=='account';
 banner.textContent=page==='account'?'실제 서버 설정 · 저장하면 선택한 Discord 채널에 적용됩니다.':'가상 시안 · 여기서 저장한 설정은 실제 Discord에 적용되지 않습니다.';
 document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('active',a.hash==='#'+page.split('?')[0]||(page==='account'&&a.hash==='#dashboard')));
 if(page==='commands')commands();else if(page==='rankings'||page.startsWith('rankings?'))rankingsPage();else if(page==='account')account();else if(page==='updates')updatesPage();else if(page==='terms'||page==='privacy')policyPage(page);else if(page==='preview')dashboard();else home();
}
// 이동을 취소하면 주소만 복구해 확인 창이 반복해서 뜨지 않게 합니다.
window.addEventListener('hashchange',()=>{if(dirty&&!leave()){history.replaceState(null,'','#preview');return}load();render()});
function dashboard(){const c=current(),voice=c[2]==='voice',locked=c[2]==='locked';app.innerHTML=`<div class="heading"><div><span class="eyebrow">YOUR GUILD, YOUR WAY</span><h1>우리 길드에 맞게, 샤벳.</h1><p>받고 싶은 소식과 알림을 채널마다 골라보세요.</p></div><span class="pill">가상 서버 · 관리자 미리보기</span></div><div class="workspace"><aside><label class="caption" for="guild">서버 선택</label><select id="guild">${servers.map(s=>`<option value="${s.id}" ${s.id===server?'selected':''}>${s.name}</option>`).join('')}</select><div class="channels"><div class="group">알림을 받을 채널</div>${servers.find(s=>s.id===server).channels.map(v=>`<button class="channel ${v[0]===channel?'active':''}" data-channel="${v[0]}"><b>${v[2]==='voice'?'◷':'#'}</b>${v[1]}</button>`).join('')}</div><p class="aside-note">설정은 이 브라우저에만 저장돼요.<br>실제 Discord에는 영향을 주지 않습니다.</p></aside><section class="content"><div class="channel-title"><div><h2>${voice?'◷':'#'} ${c[1]}</h2><p>${voice?'채널 이름에 표시할 정보를 선택하세요.':'이 채널에서 받을 알림을 설정하세요.'}</p></div><span class="pill">${voice?'음성 채널':'텍스트 채널'}</span></div>${locked?'<div class="warning">권한 부족 예시 · 샤벳이 이 채널을 볼 수 없어요. 서버 관리자에게 채널 보기 권한을 요청해주세요.</div>':''}<div class="setting-layout"><div>${voice?`<div class="setting-block"><div class="block-title">채널 이름 자동 갱신</div><div class="setting-row"><div><h3>시간·환율 표시</h3><p>이 기능에는 채널 관리 권한이 필요해요.</p></div></div><div class="role"><select id="display" aria-label="표시할 정보">${[['off','사용 안 함'],['kst','한국 시간 (KST)'],['utc','세계 표준시 (UTC)'],['exchange','USD / KRW 환율']].map(([v,l])=>`<option value="${v}" ${draft.display===v?'selected':''}>${l}</option>`).join('')}</select></div></div>`:`<div class="setting-block"><div class="block-title">소식과 이벤트</div>${settings.map(([id,title,desc])=>`<div class="setting-row"><div><h3>${title}</h3><p>${desc}</p></div><button class="switch" role="switch" aria-label="${title}" aria-checked="${!!draft[id]}" data-setting="${id}" ${locked?'disabled':''}></button></div>${id==='server'&&draft.server?`<div class="role"><label class="caption" for="role">알림을 받을 역할</label><select id="role"><option value="">역할 선택</option value="maple" ${draft.role==='maple'?'selected':''}>@메이플알림</option><option value="guild" ${draft.role==='guild'?'selected':''}>@길드원</option></select></div>`:''}`).join('')}</div>`}</div><div class="preview"><span class="caption">이런 모습으로 도착해요</span><div class="message"><div class="botline"><img src="sherbet.png" alt="샤벳">샤벳 <small>앱</small></div><div class="embed"><h3>${voice?'채널 목록에서 바로 확인':'이번 주 썬데이 메이플'}</h3><p>${voice?'◷ UTC: 19:30':'스타포스 강화 비용 30% 할인<br>이번 주 혜택을 확인해보세요.'}</p></div></div><p class="preview-note">모양을 보여주는 예시이며 실제 일정·실시간 정보가 아닙니다.</p><div class="checklist"><strong>설정 전에 확인해주세요</strong><p>알림에는 채널 보기·메시지 보내기·링크 첨부 권한이 필요해요. 이미지 알림은 파일 첨부도 허용해주세요.</p></div></div></div><div class="savebar"><span id="save-status">${dirty?'저장하지 않은 변경사항이 있어요.':'현재 설정을 확인하고 있어요.'}</span><div class="buttons"><button id="cancel" ${!dirty?'disabled':''}>되돌리기</button><button class="primary" id="save" ${!dirty||locked?'disabled':''}>변경사항 저장</button></div></div></section></div>`;
$('#guild').onchange=e=>{if(!leave()){e.target.value=server;return}server=e.target.value;channel='news';load();dashboard()};document.querySelectorAll('[data-channel]').forEach(b=>b.onclick=()=>{if(!leave())return;channel=b.dataset.channel;load();dashboard()});document.querySelectorAll('[data-setting]').forEach(b=>b.onclick=()=>{draft[b.dataset.setting]=!draft[b.dataset.setting];dirty=true;dashboard();document.querySelector(`[data-setting="${b.dataset.setting}"]`).focus()});if($('#role'))$('#role').onchange=e=>{draft.role=e.target.value;dirty=true;dashboard()};if($('#display'))$('#display').onchange=e=>{draft.display=e.target.value;dirty=true;dashboard()};$('#cancel').onclick=()=>{load();dashboard()};$('#save').onclick=()=>{if(draft.server&&!draft.role&&!voice){notice('알림을 받을 역할을 골라주세요','서버 오픈 알림은 선택한 역할을 멘션합니다. 역할을 선택한 뒤 저장해주세요.');return}saved[key()]=structuredClone(draft);try{localStorage.setItem('sherbet-demo-settings',JSON.stringify(saved))}catch{toast('브라우저 저장 공간을 사용할 수 없어요.');return}dirty=false;dashboard();toast('이 브라우저에 예시 설정을 저장했어요.')};}
function home(){app.innerHTML=`<section class="hero"><div><span class="eyebrow">A LITTLE COMPANION FOR YOUR MAPLE LIFE</span><h1>글로벌 메이플,<br><em>조금 더 가깝게.</em></h1><p>영문 공지를 찾아보는 시간은 줄이고,<br>함께 메이플하는 즐거움은 더하고.<br>한국어 소식부터 성장 계산까지 샤벳과 함께하세요.</p><div class="hero-actions"><button class="primary" id="invite">서버에 초대하기 ↗</button><a class="hero-secondary" href="#dashboard">대시보드 둘러보기 ↗</a></div></div><div class="mascot-stage"><span class="tag one">새로운 소식이 도착했어요!</span><img src="sherbet.png" alt="샤벳 도트 캐릭터"><span class="tag two">오늘도 함께 메이플 🍁</span></div></section><div class="feature-grid"><article class="feature"><span class="number">01 / STAY UPDATED</span><h2>놓치지 않는 메이플 소식</h2><p>한국어 공식 공지, 패치 수정과 이벤트 알림을 우리 길드의 채널에서 받아보세요.</p></article><article class="feature"><span class="number">02 / TRACK YOUR GROWTH</span><h2>눈에 보이는 캐릭터 성장</h2><p>랭킹과 성장 기록을 확인하고, 다음 목표에 필요한 경험치와 재료를 계산하세요.</p></article><article class="feature"><span class="number">03 / JUST FOR FUN</span><h2>부담 없이 미리 뽑아보기</h2><p>스스비·시그니처·원더베리. 게임 재화 소모 없이 시뮬레이터로 체험하세요.</p></article></div>`;$('#invite').onclick=()=>{location.href='https://discord.com/oauth2/authorize?client_id=1134715857403129939'}}
function commands(){app.innerHTML=`<div class="heading"><div><span class="eyebrow">FIND YOUR NEXT COMMAND</span><h1>무엇을 도와드릴까요?</h1><p>필요한 명령어를 찾고, 사용 방법을 확인하세요.</p></div><input class="search" type="search" id="search" placeholder="명령어 또는 기능 검색" aria-label="명령어 검색"></div><div class="commands-layout"><div class="filters">${['전체',...Object.keys(commandData),'관리자'].map(c=>`<button data-filter="${c}" class="${filter===c?'active':''}">${c}</button>`).join('')}</div><div class="command-list" id="results"></div></div>`;$('#search').value=query;$('#search').oninput=e=>{query=e.target.value;results()};document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;commands()});results();commandWalkthrough()}
function results(){const groups={...commandData,'관리자':[['/관리자','홈페이지 대시보드에서 서버 설정']]};const rows=Object.entries(groups).flatMap(([g,rows])=>rows.map(([name,desc])=>({g,name,desc}))).filter(r=>(filter==='전체'||filter===r.g)&&(r.name+' '+r.desc+' '+(commandGuides[r.name]||[]).join(' ')).toLowerCase().includes(query.toLowerCase()));$('#results').replaceChildren();if(!rows.length){$('#results').innerHTML='<div class="empty">검색 결과가 없어요. 다른 이름으로 찾아보세요.</div>';return}rows.forEach(r=>{const card=document.createElement('article');card.className='command-card';const code=document.createElement('code');code.textContent=r.name;const desc=document.createElement('p');desc.textContent=r.desc;const label=document.createElement('span');label.className='caption';label.textContent=r.g;card.append(label,code,desc);
const guide=commandGuides[r.name];
if(guide){
 desc.textContent=guide[0];
 const details=document.createElement('details');details.className='command-guide';
 const summary=document.createElement('summary');summary.textContent='사용 방법 보기';
 const steps=document.createElement('p');steps.textContent=guide[1];
 const note=document.createElement('p');note.className='guide-note';note.textContent=guide[2];
 details.append(summary,steps,note);card.append(details);
}
// 준비된 실제 결과만 보여주고 빈 이미지 자리는 만들지 않습니다.
if(r.name==='/시그니처 · /원더베리') {
  showSalePeriod(card);
  for(const [file,label] of [['example-signature.png','시그니처 5회 결과 예시'],['example-wonderberry.png','원더베리 결과 예시']]) {
    const figure=document.createElement('figure');figure.className='command-example';
    const img=document.createElement('img');img.src=file;img.alt=label;img.loading='lazy';
    const caption=document.createElement('figcaption');caption.textContent=label+' · 결과는 매번 달라집니다.';
    figure.append(img,caption);card.append(figure);
  }
}$('#results').append(card)})}
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});render();

// 봇 도움말과 같은 공개 원문을 안전한 텍스트 노드로 표시합니다.
async function policyPage(key){
 app.innerHTML='<article class="policy-page"><p>문서를 불러오는 중입니다.</p></article>';
 const article=app.querySelector('.policy-page');
 try{
  const response=await fetch('policies.json');
  if(!response.ok)throw new Error('policy load');
  const data=await response.json(), policy=data[key];
  if(!article.isConnected)return;
  article.replaceChildren();
  const title=document.createElement('h1');title.textContent=policy.title;article.append(title);
  const date=document.createElement('p');date.textContent='변경일: '+data.updated;article.append(date);
  for(const section of policy.sections){
   const heading=document.createElement('h2');heading.textContent=section.title;
   const body=document.createElement('p');body.textContent=section.body;article.append(heading,body);
  }
  const contact=document.createElement('a');contact.href='mailto:'+data.contact;contact.textContent='운영자 문의·삭제 요청: '+data.contact;article.append(contact);
 }catch{if(article.isConnected)article.textContent='문서를 불러오지 못했습니다. 새로고침하거나 valentineday302@gmail.com으로 문의해주세요.'}
}

// 공개 업데이트에는 검증된 배포 내용을 넣고 HTML 대신 텍스트로 표시합니다.
async function updatesPage(){
 app.innerHTML='<section class="policy-page"><h1>업데이트 내역</h1><p>샤벳에 새로 추가되거나 달라진 기능을 알려드려요.</p><div class="updates-list" role="status">업데이트를 불러오는 중입니다.</div></section>';
 const list=app.querySelector('.updates-list');
 try{
  const response=await fetch('updates.json');
  if(!response.ok)throw new Error('updates load');
  const entries=await response.json();
  if(!list.isConnected)return;
  list.replaceChildren();
  // 날짜가 같은 배포는 한 카드에 모으고, 세부 변경 내용은 모두 보존합니다.
  const dates=[...new Set(entries.map(entry=>entry.date))].sort().reverse();
  for(const day of dates){
   const card=document.createElement('article');card.className='update-entry';
   const date=document.createElement('time');date.dateTime=day;date.textContent=day.replaceAll('-','.');
   const heading=document.createElement('h2');heading.textContent='샤벳 업데이트';card.append(date,heading);
   for(const entry of entries.filter(entry=>entry.date===day)){
    const section=document.createElement('section');
    const title=document.createElement('h3');title.textContent=entry.title;
    const items=document.createElement('ul');
    for(const text of entry.items){const item=document.createElement('li');item.textContent=text;items.append(item)}
    section.append(title,items);card.append(section);
   }
   list.append(card);
  }
  if(!entries.length)list.textContent='아직 등록된 업데이트가 없습니다.';
 }catch{if(list.isConnected)list.textContent='업데이트 내역을 불러오지 못했습니다. 새로고침해주세요.'}
}

// 실제 Discord 녹화와 혼동하지 않도록 사용 흐름을 보여주는 재현 예시로 표시합니다.
function commandWalkthrough(){
 const section=document.createElement('section');section.className='command-walkthrough';
 section.innerHTML=`<div><span class="eyebrow">HOW IT WORKS</span><h2>명령어 한 번, 이렇게 시작해요.</h2><p>사용 흐름을 재현한 예시입니다. 실제 Discord 녹화나 실시간 결과는 아닙니다.</p></div><label>둘러볼 기능<select id="walkthrough-kind"><option value="랭킹">캐릭터 랭킹 조회</option><option value="헥사">HEXA 재료 계산</option><option value="관리자">홈페이지 서버 설정</option></select></label><div class="walkthrough-steps"></div><button type="button" class="walkthrough-play">흐름 재생 ↻</button>`;
 const examples={
  '랭킹':['/랭킹 닉네임: Dark','캐릭터의 저장된 기록을 조회합니다.','랭킹 카드와 성장 기록에서 기준일·경험치를 확인해요.'],
  '헥사':['/헥사','코어 종류와 현재·목표 레벨을 입력하고 계산하기를 눌러요.','필요한 솔 에르다와 조각 개수를 확인해요.'],
  '관리자':['/관리자 → 홈페이지에서 설정하기','Discord 로그인 후 관리할 서버를 선택해요.','기능별 채널·알림을 선택하고 저장하면 적용돼요.']
 };
 const draw=play=>{
  const box=section.querySelector('.walkthrough-steps');box.classList.remove('playing');box.replaceChildren();
  examples[section.querySelector('select').value].forEach((text,i)=>{const row=document.createElement('p');row.className='walkthrough-step';const badge=document.createElement('b');badge.textContent=String(i+1).padStart(2,'0');row.append(badge,document.createTextNode(text));box.append(row)});
  if(play)box.classList.add('playing');
 };
 section.querySelector('select').onchange=()=>draw(false);
 section.querySelector('button').onclick=()=>draw(true);
 app.querySelector('.heading').after(section);draw(false);
}
