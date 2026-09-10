"""GMS 원본 패널과 이펙트를 사용한 연마석 화면입니다."""
import io
import json
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent / 'assets' / 'polisher'
FONT = ASSETS.parent / 'NotoSansKR-VF.ttf'


def render_polisher(level, stone_count, rate, result, *, animation_frame=None):
    # 원본 패널의 비율을 유지하고 긴 빈 공간만 덜어냅니다.
    with Image.open(ASSETS / 'background.png') as source:
        original = source.convert('RGBA')
    canvas = Image.new('RGBA', (418, 342))
    # 위 패널의 둥근 하단 모서리는 제외하고 아래 패널의 직선 테두리로 이어줍니다.
    canvas.alpha_composite(original.crop((0, 0, 418, 220)), (0, 0))
    canvas.alpha_composite(original.crop((0, 598, 418, 720)), (0, 220))
    draw = ImageDraw.Draw(canvas)

    def text(value, xy, size, color, anchor='lm', weight=500):
        font = ImageFont.truetype(str(FONT), size)
        font.set_variation_by_axes([weight])
        draw.text((xy[0], xy[1]+1), value, font=font, fill='#182229', anchor=anchor)
        draw.text(xy, value, font=font, fill=color, anchor=anchor)

    def icon(target, filename, center, scale):
        with Image.open(ASSETS / filename) as source:
            sprite = source.convert('RGBA')
        sprite = sprite.resize((sprite.width*scale, sprite.height*scale), Image.Resampling.NEAREST)
        target.alpha_composite(sprite, (center[0]-sprite.width//2, center[1]-sprite.height//2))

    # WZ의 뒤/앞 이펙트 사이에 반지를 놓고 글자 영역은 가리지 않습니다.
    effects = Image.new('RGBA', canvas.size)
    for layer in ('back', 'front'):
        if layer == 'front':
            icon(effects, 'ring.png', (209, 110), 2)
        for frame in animation_frame or []:
            if frame['layer'] == layer:
                with Image.open(ASSETS / 'effects' / frame['file']) as source:
                    effects.alpha_composite(source.convert('RGBA'), (209-frame['x'], 110-frame['y']))
    canvas.alpha_composite(effects.crop((12, 33, 406, 177)), (12, 33))
    displayed_level = level + int(bool(result and result['success']))
    text(f'리스트레인트 링  Lv.{displayed_level}', (209, 196), 16, '#f0f5f5', 'mm', 700)
    icon(canvas, 'life.png' if level == 4 else 'faith.png', (55, 270), 1)
    text('생명의 연마석' if level == 4 else '신념의 연마석', (85, 258), 15, '#bed0d3')
    text(f'{stone_count}개 사용', (85, 282), 17, '#e4e9eb', weight=700)
    text('성공 확률', (376, 258), 14, '#bed0d3', 'rm')
    text(f'{rate}%', (376, 283), 22, '#f0f5f5', 'rm', 700)
    status = '연마 중…' if animation_frame is not None else f'Lv.{level} → Lv.{level+1} 강화 준비'
    color = '#c3d0d5'
    if result is not None:
        status = f'연마 성공 · Lv.{level+1} 달성' if result['success'] else f'연마 실패 · Lv.{level} 유지'
        color = '#a5ddbe' if result['success'] else '#f4a1a5'
    text(status, (209, 320), 17, color, 'mm', 700)
    buffer = io.BytesIO()
    canvas.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer


@lru_cache(maxsize=20)
def render_polisher_animation(level, stone_count, rate, success):
    """GIF와 준비+이펙트 시간을 반환합니다. 마지막 2초는 대기에서 제외합니다."""
    with (ASSETS / 'effects' / 'frames.json').open(encoding='utf-8-sig') as file:
        metadata = json.load(file)
    frames, durations = [], []
    # 첨부 교체 직후에는 이펙트 대신 준비 화면을 0.5초 보여 줍니다.
    with Image.open(render_polisher(level, stone_count, rate, None)) as image:
        frames.append(image.convert('RGB'))
    durations.append(500)
    # 기존 자료는 5개까지입니다. 추가 개수는 5개 효과만 재사용하고 수치 표시는 실제값을 씁니다.
    effect_count = min(stone_count, 5)
    for phase in ('try', 'success' if success else 'fail'):
        records = [r for r in metadata if r['level'] == level and r['phase'] == phase
                   and r['count'] == (effect_count if phase == 'try' else 0)]
        if not records:
            raise ValueError('선택한 조건의 연마 이펙트가 없습니다.')
        for number in sorted({r['frame'] for r in records}):
            current = [r for r in records if r['frame'] == number]
            result = None if phase == 'try' else {'success': success}
            with Image.open(render_polisher(level, stone_count, rate, result, animation_frame=current)) as image:
                frames.append(image.convert('RGB'))
            durations.append(max(20, max(r['delay'] for r in current)))
    # Discord의 메시지 갱신 지연 중 재반복을 줄이는 기본 화면 여유 구간입니다.
    effect_duration = sum(durations) / 1000
    with Image.open(render_polisher(level, stone_count, rate, {'success': success})) as image:
        frames.append(image.convert('RGB'))
    durations.append(2000)
    output = io.BytesIO()
    # 준비 화면과 이펙트가 끝나면 봇이 정적 결과로 바꾸고 버튼을 풉니다.
    frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:],
                   duration=durations, disposal=2, optimize=True)
    return output.getvalue(), effect_duration
