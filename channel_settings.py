"""디스코드와 홈페이지에서 함께 쓰는 공지 알림 저장 처리."""


SIMPLE_ALERTS = {
    'news': '공지 알림', 'miracle_time': '미라클 타임 알림',
    'cash_transfer': '캐시이동 알림', 'ursus': '우르스 알림',
}
VOICE_SETTINGS = {'info_time':'시간 표시', 'info_utc':'UTC 표시', 'info_exchange':'환율 표시'}
WEB_SETTINGS = {**SIMPLE_ALERTS, 'sunny_day':'썬데이 당일 알림', 'sunny_list':'썬데이 목록 알림',
                'server':'서버 오픈 알림', 'exchange_log':'환율 기록 알림', **VOICE_SETTINGS}


class SettingConflict(ValueError):
    pass


def set_news_alert(bot, channel_id, enabled, permissions, *, expected=None, kind='news'):
    if kind not in SIMPLE_ALERTS:
        raise ValueError('지원하지 않는 알림입니다.')
    # 저장 중에는 await가 없어 다른 설정 변경과 중간 상태가 섞이지 않습니다.
    channels = bot.alert_channels[kind]
    current = channel_id in channels
    if expected is not None and current != expected:
        raise SettingConflict('다른 곳에서 설정이 변경됐습니다. 다시 조회해주세요.')
    if enabled and (permissions is None or not (
            permissions.view_channel and permissions.send_messages and permissions.embed_links
            and (kind not in {'cash_transfer', 'ursus'} or permissions.attach_files))):
        raise PermissionError('봇의 채널 보기·메시지 보내기·링크 첨부·파일 첨부 권한을 확인해주세요.')
    if current == enabled:
        return False
    if enabled:
        channels.add(channel_id)
    else:
        channels.discard(channel_id)
    try:
        bot.persist_state()
    except Exception:
        # 디스크 저장 실패 시 메모리도 이전 값으로 돌려 성공한 것처럼 보이지 않게 합니다.
        if current:
            channels.add(channel_id)
        else:
            channels.discard(channel_id)
        raise
    return True
