() => {
  const channel = '309809230095843328';
  if (location.pathname !== `/channels/${channel}/${channel}`) return [];
  // 화면의 메시지 요소만 읽습니다. 계정 저장소·내부 API에는 접근하지 않습니다.
  const text = node => {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent;
    if (node.nodeType !== Node.ELEMENT_NODE || node.getAttribute('aria-hidden') === 'true') return '';
    if (node.matches('[class*="edited"], [class*="roleMention"]')) return '';
    const inner = Array.from(node.childNodes).map(text).join('');
    if (node.tagName === 'A') return `[${inner}](${node.href})`;
    if (node.tagName === 'IMG') return node.alt || '';
    if (node.tagName === 'BR') return '\n';
    if (node.tagName === 'S' || node.tagName === 'DEL') return `~~${inner}~~`;
    if (node.tagName === 'STRONG') return `**${inner}**`;
    if (node.tagName === 'LI') return `\n- ${inner}\n`;
    return inner;
  };
  return Array.from(document.querySelectorAll(`li[id^="chat-messages-${channel}-"]`)).map(item => {
    const id = item.id.split('-').pop();
    const body = item.querySelector(`[id="message-content-${id}"]`);
    const article = item.querySelector('[role="article"]');
    const authorId = (article?.getAttribute('aria-labelledby') || '').split(' ').find(v => v.startsWith('message-username-'));
    const author = authorId && document.getElementById(authorId)?.querySelector('[data-text]')?.getAttribute('data-text');
    const accessories = item.querySelector(`[id="message-accessories-${id}"]`);
    return {
      id, channel_id: channel, author: author || 'MapleStory',
      body: body ? text(body).trim() : '',
      created_at: item.querySelector('time[datetime]')?.getAttribute('datetime'),
      links: body ? Array.from(body.querySelectorAll('a[href^="https://"]')).map(a => a.href) : [],
      images: accessories ? Array.from(accessories.querySelectorAll('a[data-role="img"]')).map(a => a.href) : []
    };
  }).filter(row => row.created_at && (row.body || row.images.length));
}
