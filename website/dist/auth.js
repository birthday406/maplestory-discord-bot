// 실제 로그인과 가상 설정 화면을 구분합니다. 로그인 토큰은 브라우저에 저장하지 않습니다.
let authInfo = null;
async function account() {
  const root = document.querySelector('#app');
  root.innerHTML = '<div class="heading"><div><span class="eyebrow">DISCORD ACCOUNT</span><h1>Discord 연결</h1><p id="account-status" role="status">연결 상태를 확인하고 있어요.</p></div></div><section id="account-body" class="setting-block" style="padding:24px"></section>';
  const body = document.querySelector('#account-body');
  try {
    const response = await fetch('/api/session');
    if (!response.ok) throw new Error();
    authInfo = await response.json();
    // 이동 중 응답이 도착하면 다른 화면에 계정 정보를 그리지 않습니다.
    if (location.hash !== '#account') return;
    if (!authInfo.user) {
      document.querySelector('#account-status').textContent = authInfo.ready ? 'Discord에서 본인 확인 후 관리 서버를 불러옵니다.' : '로그인 코드는 준비됐어요. 샤벳의 로그인 설정을 등록하면 연결할 수 있어요.';
      if (authInfo.ready) {
        const link = document.createElement('a'); link.href='/auth/login'; link.textContent='Discord에서 로그인하기 ↗'; link.className='primary'; body.append(link);
      } else {
        body.innerHTML = '<h2>연결 준비 순서</h2><p>1. Discord 개발자 페이지에서 샤벳을 선택하세요.</p><p>2. OAuth2 → Redirects에 아래 주소를 등록하세요.</p><code>http://127.0.0.1:8766/auth/callback</code><p>3. 홈페이지 서버를 로그인 설정 입력 모드로 실행하세요.</p><p>Client Secret은 채팅이나 홈페이지에 입력하지 않습니다. 실행 터미널의 숨김 입력으로 받습니다.</p><a href="https://discord.com/developers/applications" target="_blank" rel="noopener noreferrer">Discord 개발자 페이지 열기 ↗</a>';
      }
      return;
    }
    document.querySelector('#account-status').textContent = authInfo.user.name + '님, 로그인되었습니다.';
    body.innerHTML = '<h2>관리자 권한이 있는 서버</h2><p>서버를 선택해 채널별 설정을 확인하세요. 알림과 시간·환율 채널 설정을 변경할 수 있습니다.</p><div id="real-guilds">목록을 불러오는 중…</div><button id="logout">로그아웃</button>';
    document.querySelector('#logout').onclick = async () => {
      try {
        const out = await fetch('/auth/logout', {method:'POST', headers:{'X-CSRF-Token':authInfo.csrf}});
        if (!out.ok) throw new Error();
        authInfo = null; await account();
      } catch { notice('로그아웃 확인 실패', '연결 상태를 확인한 뒤 다시 시도해주세요.'); }
    };
    const list = await fetch('/api/guilds');
    if (!list.ok) throw new Error();
    const data = await list.json();
    if (location.hash !== '#account') return;
    const target = document.querySelector('#real-guilds'); target.replaceChildren();
    if (!data.guilds.length) target.textContent='관리자 권한을 가진 서버가 없습니다.';
    for (const guild of data.guilds) {
      const row = document.createElement('button'); row.className='guild-choice'; row.textContent = guild.name + ' →'; row.onclick=()=>showGuild(guild, data.settingsConnected); target.append(row);
    }
  } catch {
    if (location.hash !== '#account') return;
    document.querySelector('#account-status').textContent='연결 상태를 확인하지 못했어요. 로그인 서버가 실행 중인지 확인하고 다시 시도해주세요.';
    const retry = document.createElement('button'); retry.textContent='다시 확인'; retry.onclick=account; body.append(retry);
  }
}
document.querySelector('#login').onclick = () => { location.hash='account'; };

// 실제 서버 조회는 시안의 localStorage 설정을 사용하지 않습니다.
async function showGuild(guild, connected) {
  const root = document.querySelector('#app');
  root.replaceChildren();
  const back = document.createElement('button'); back.textContent='← 서버 목록'; back.onclick=account;
  const heading = document.createElement('h1'); heading.textContent=guild.name;
  const status = document.createElement('p'); status.setAttribute('role','status');
  status.textContent='현재 채널과 설정을 불러오는 중…';
  const content = document.createElement('section'); content.className='live-settings';
  root.append(back, heading, status, content);
  if (!connected) {
    status.textContent='로그인과 서버 선택은 완료됐어요. 현재 로컬 홈페이지에는 운영 샤벳의 조회 연결이 아직 적용되지 않았습니다.';
    return;
  }
  try {
    const response = await fetch('/api/guilds/'+encodeURIComponent(guild.id));
    if (!root.contains(content)) return;
    if (!response.ok) {
      status.textContent = ({401:'로그인이 만료됐어요. 다시 로그인해주세요.',403:'이 서버의 관리자 권한 또는 샤벳의 조회 권한을 확인할 수 없습니다.',404:'샤벳이 이 서버에 없거나 서버 정보를 아직 받지 못했습니다.',503:'샤벳 조회 연결을 기다리고 있습니다.'})[response.status] || '조회에 실패했습니다. 서버 목록으로 돌아가 다시 시도해주세요.';
      return;
    }
    const data = await response.json();
    if (!root.contains(content)) return;
    status.textContent='현재 봇 설정 · 확인 '+new Date(data.checkedAt).toLocaleString();
    if (!data.channels.length) { content.textContent='표시할 텍스트·음성 채널이 없습니다.'; return; }
    // 알림 종류마다 채널을 독립적으로 선택하며, 다른 카드의 미저장 입력은 유지합니다.
    const hints={sunny_day:'ON: 진행 중인 썬데이를 바로 전송합니다. OFF: 이 채널의 기존 당일 메시지도 삭제합니다.',sunny_list:'ON으로 저장하면 보관된 썬데이 목록을 바로 전송합니다.',server:'공식 Game is up 안내가 올라오면 선택한 역할을 멘션합니다. 저장 시에는 멘션하지 않습니다.',exchange_log:'ON으로 저장하면 현재 환율 기록을 바로 전송합니다.',info_time:'ON: 음성 채널 이름을 시간으로 바꾸고 자동 갱신합니다. OFF: 갱신만 중단합니다.',info_utc:'ON: 음성 채널 이름을 UTC 시간으로 바꾸고 자동 갱신합니다. OFF: 갱신만 중단합니다.',info_exchange:'ON: 음성 채널 이름을 환율로 바꾸고 자동 갱신합니다. OFF: 갱신만 중단합니다.'};
    const introduction=document.createElement('p');
    introduction.textContent='각 기능에서 받을 채널을 선택하고 저장하세요. 선택한 채널에만 적용되며, 다른 채널의 알림은 그대로 유지됩니다.';
    content.append(introduction);
    const cards=[];
    function refreshSummaries() {
      for(const card of cards) {
        const enabled=card.channels.filter(c=>c.settings?.[card.kind]);
        card.summary.textContent='사용 중: '+(enabled.length?enabled.map(c=>(c.type==='voice'?'◷ ':'# ')+c.name).join(', '):'없음');
        for(const option of card.select.options) {
          const channel=card.channels.find(c=>c.id===option.value);
          if(channel) option.textContent=(channel.type==='voice'?'◷ ':'# ')+channel.name+(channel.settings?.[card.kind]?' · 사용 중':'');
        }
      }
    }
    for(const [kind,settingLabel] of Object.entries(data.settingLabels || {news:'공지 알림'})) {
      const channels=data.channels.filter(c=>(c.type==='voice')===kind.startsWith('info_'));
      const box=document.createElement('fieldset');box.className='setting-block feature-setting';
      const title=document.createElement('h2');title.textContent=settingLabel;
      const summary=document.createElement('p');summary.className='feature-summary';
      const hint=document.createElement('p');hint.className='feature-hint';hint.textContent=hints[kind] || '다음 알림부터 적용됩니다. 과거 알림은 다시 보내지 않습니다.';
      const controls=document.createElement('div');controls.className='feature-controls';
      const channelLabel=document.createElement('label');channelLabel.textContent=kind.startsWith('info_')?'표시할 음성 채널':'알림 받을 채널';channelLabel.htmlFor='channel-'+kind;
      const select=document.createElement('select');select.id='channel-'+kind;
      const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent=channels.length?'채널 선택':'선택할 수 있는 채널 없음';select.append(placeholder);
      for(const channel of channels){const option=document.createElement('option');option.value=channel.id;select.append(option);}
      select.value=channels.find(c=>c.settings?.[kind])?.id || '';
      channelLabel.append(select);
      const stateLabel=document.createElement('label');stateLabel.className='feature-state';stateLabel.htmlFor='toggle-'+kind;
      const toggle=document.createElement('input');toggle.id='toggle-'+kind;toggle.type='checkbox';
      stateLabel.append(toggle,document.createTextNode('알림 사용'));
      if(kind.startsWith('info_'))stateLabel.lastChild.textContent='자동 갱신';
      const roleLabel=document.createElement('label');roleLabel.textContent='멘션 역할';roleLabel.htmlFor='role-'+kind;
      const roles=document.createElement('select');roles.id='role-'+kind;
      const empty=document.createElement('option');empty.value='';empty.textContent='역할 선택';roles.append(empty);
      for(const role of data.roles || []){const option=document.createElement('option');option.value=role.id;option.textContent='@'+role.name;roles.append(option);}
      roleLabel.append(roles);
      const save=document.createElement('button');save.className='primary';save.textContent='저장';
      const feedback=document.createElement('p');feedback.setAttribute('role','status');
      const permission=document.createElement('p');permission.className='feature-permission';
      function update() {
        const channel=channels.find(c=>c.id===select.value);
        const current=channel?.settings?.[kind] || false;
        roles.disabled=!toggle.checked || !channel;
        toggle.disabled=!channel;
        save.disabled=data.readOnly || !channel || (toggle.checked===current && (kind!=='server' || !toggle.checked || roles.value===(channel.roleId||''))) || (kind==='server' && toggle.checked && !roles.value);
      }
      function selectChannel() {
        const channel=channels.find(c=>c.id===select.value);
        toggle.checked=channel?.settings?.[kind] || false;
        roles.value=channel?.roleId || '';
        feedback.textContent='';
        const required=kind.startsWith('info_')?[['view','채널 보기'],['manage','채널 관리']]:[['view','채널 보기'],['send','메시지 보내기'],['embed','링크 첨부']];
        if(['sunny_day','sunny_list','cash_transfer','ursus'].includes(kind))required.push(['attach','파일 첨부']);
        const missing=channel?required.filter(([key])=>!channel.permissions[key]).map(([,label])=>label):[];
        permission.textContent=missing.length?'부족한 봇 권한: '+missing.join(' · '):'';
        update();
      }
      select.onchange=selectChannel;
      toggle.onchange=()=>{feedback.textContent='';update();};
      roles.onchange=()=>{feedback.textContent='';update();};
      save.onclick=async()=>{
        const channel=channels.find(c=>c.id===select.value);
        if(!channel || save.disabled)return;
        const next=toggle.checked;
        const payload={channelId:channel.id,enabled:next,previous:channel.settings?.[kind] || false};
        if(kind==='server')Object.assign(payload,{roleId:next?roles.value:null,previousRoleId:channel.roleId});
        // 한 번의 저장 클릭으로 적용합니다. 전송 중에는 중복 입력을 막습니다.
        for(const card of cards)card.box.disabled=true;
        back.disabled=true;feedback.textContent='저장 중…';
        try {
          const response=await fetch('/api/guilds/'+encodeURIComponent(guild.id)+'/alerts/'+encodeURIComponent(kind),{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':authInfo.csrf},body:JSON.stringify(payload)});
          if(!root.contains(content))return;
          if(!response.ok){
            const messages={400:'채널·역할 또는 다른 시간 표시 설정을 확인해주세요.',401:'로그인이 만료됐어요. 다시 로그인해주세요.',403:'관리자 또는 봇의 채널 권한을 확인해주세요.',409:'다른 곳에서 설정이 바뀌었어요. 서버 목록으로 돌아가 다시 조회해주세요.',500:'저장 결과를 확인하지 못했습니다. 다시 조회해주세요.',503:'봇이 연결 중이거나 저장 기능이 아직 적용되지 않았습니다.'};
            feedback.textContent=messages[response.status]||'저장 결과를 다시 조회해주세요.';save.disabled=true;return;
          }
          const result=await response.json();
          channel.settings ||= {};channel.settings[kind]=result.enabled;
          if(kind==='server'){channel.roleId=payload.roleId;channel.mentionRole=next?roles.selectedOptions[0].textContent.replace(/^@/,''):null;}
          if(result.channelName)channel.name=result.channelName;
          refreshSummaries();update();
          feedback.textContent=result.warning || channel.name+' · '+(result.enabled?'ON':'OFF')+' 저장 완료';
        } catch {feedback.textContent='연결이 끊겨 저장 결과를 확인하지 못했습니다. 다시 조회해주세요.';save.disabled=true;}
        finally {for(const card of cards)card.box.disabled=false;back.disabled=false;}
      };
      controls.append(channelLabel,stateLabel);if(kind==='server')controls.append(roleLabel);controls.append(save);
      box.append(title,summary,hint,controls,permission,feedback);content.append(box);
      cards.push({kind,channels,box,summary,select});selectChannel();
    }
    refreshSummaries();
  } catch { if(root.contains(content)) status.textContent='연결이 끊겼어요. 서버 목록에서 다시 선택해주세요.'; }
}
