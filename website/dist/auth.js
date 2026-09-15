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
    body.innerHTML = '<h2>관리자 권한이 있는 서버</h2><p>서버를 선택해 채널과 현재 설정을 확인하세요. 이 화면에서는 설정을 변경하지 않습니다.</p><div id="real-guilds">목록을 불러오는 중…</div><button id="logout">로그아웃</button>';
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
    status.textContent='현재 봇 설정 · 읽기 전용 · 확인 '+new Date(data.checkedAt).toLocaleString();
    if (!data.channels.length) { content.textContent='표시할 텍스트·음성 채널이 없습니다.'; return; }
    const label = document.createElement('label'); label.textContent='채널 선택'; label.htmlFor='live-channel';
    const select = document.createElement('select'); select.id='live-channel';
    for (const channel of data.channels) {
      const option = document.createElement('option'); option.value=channel.id;
      option.textContent=(channel.type==='voice'?'◷ ':'# ')+channel.name; select.append(option);
    }
    const details=document.createElement('div'); details.className='setting-block live-details';
    content.append(label,select,details);
    function draw() {
      const channel=data.channels.find(c=>c.id===select.value); details.replaceChildren();
      const title=document.createElement('h2'); title.textContent='사용 중인 기능'; details.append(title);
      for (const text of (channel.enabled.length?channel.enabled:['이 채널에 설정된 기능이 없습니다.'])) {
        const line=document.createElement('p'); line.textContent=text; details.append(line);
      }
      if(channel.mentionRole) { const line=document.createElement('p'); line.textContent='서버 오픈 멘션 역할: @'+channel.mentionRole; details.append(line); }
      const required = channel.type==='voice' ? [['view','채널 보기'],['manage','채널 관리']] : [['view','채널 보기'],['send','메시지 보내기'],['embed','링크 첨부'],['attach','파일 첨부']];
      const missing=required.filter(([key])=>!channel.permissions[key]).map(([,label])=>label);
      const permission=document.createElement('p'); permission.textContent=missing.length?'샤벳의 부족한 권한: '+missing.join(' · '):'샤벳의 기본 채널 권한이 준비되어 있습니다.';
      details.append(permission);
    }
    select.onchange=draw; draw();
  } catch { if(root.contains(content)) status.textContent='연결이 끊겼어요. 서버 목록에서 다시 선택해주세요.'; }
}
