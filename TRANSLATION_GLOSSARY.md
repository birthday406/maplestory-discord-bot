# API 번역 용어 교정표

최종 갱신: 2026-09-09

공지 요약·번역은 GPT + Google을 사용합니다. Google 번역에서도 파일 용어를 임시 표식으로 보호한 뒤 권장 한국어로 복원합니다. Google 연결은 2026-09-09 운영 배포를 완료했습니다. Ollama `/패치질문`은 기존처럼 용어 지침을 사용합니다.

아래 권장 번역은 사용자가 지정한 프로젝트 기준입니다. 공식 번역을 외부에서 검증했다는 뜻은 아닙니다. 기존 번역이 `—`인 항목은 오역 사례 없이 권장 표현만 등록한 항목입니다.

봇의 Ollama 요청은 이 파일의 `사용자 확정 용어` 표를 자동으로 읽고 원문에 등장하는 항목만 번역 지침에 포함합니다. 같은 영어 용어가 관리자 DM 교정 DB에도 있으면 DM 설정이 우선합니다. Ollama·Google 연결과 최근 추가 용어 모두 운영에 적용했습니다.

파일 변경은 다음 API 요청부터 읽으며 DB로 복사하지 않습니다. 추가 API 호출이나 자동 재번역은 하지 않습니다. Ollama는 모델 지침 방식이고 Google은 표식 복원 방식입니다. Google이 표식을 누락·중복하면 오류로 처리하며, 기존 번역·메시지·캐시·고정 문구도 소급 변경하지 않습니다.

## 사용자 확정 용어

| 영어 원문 | 기존 번역·피할 표현 | 권장 번역 |
| --- | --- | --- |
| Arteria | — | 아르테리아 |
| Tallahart | — | 탈라하트 |
| Frieren | 프리에렌 | 프리렌 |
| Linie | — | 리니에 |
| Face | 페이스 | 얼굴 |
| Collaboration | 협업 | 콜라보레이션 |
| Guardian Angel Slime | 수호천사 슬라임 | 가디언 엔젤 슬라임 |
| Vellum | 벨럼 | 벨룸 |
| Lügner | 뤼그너 | 류그너 |
| Philosopher's Book | 철학자의 책 | 필로소퍼 북 |
| Fern | 펀 | 페른 |
| Stark | 스타크 | 슈타르크 |
| Übel | — | 위벨 |
| Geardock | 기어독 | 기어드락 |
| Special Skill Ring | 스페셜 스킬 링 | 특수 스킬 반지 |
| Sia Astelle | 시아 아스텔레 | 시아 아스텔 |
| Erel Light | 에렐 라이트 | 에릴 라이트 |
| Node | 노드 | 코어 |
| Omega Sector | 오메가 섹터 | 지구방위본부 |
| Familiar | 펫 | 퍼밀리어 |
| Patrol Securitron | 순찰 시큐리턴 | 순찰형 경비 로봇 |
| Strike Securitron | 강습 시큐리턴 | 공격형 경비 로봇 |
| Combatron N | 컴배트론 N | 일반형 병기 로봇 |
| Combatron EX | 컴배트론 EX | 강화형 병기 로봇 |
| Kronos's Retribution | 크로노스의 보복 | 크로노스의 원념 |
| Sacred Symbol | 신성한 심볼 | 어센틱심볼 |
| Grand Sacred Symbol | 그랜드 세이크리드 심볼 | 그랜드 어센틱심볼 |
| Hotel Arcus | 호텔 아르쿠스 | 호텔 아르크스 |
| Shagri-La | 샤그리-라 | 도원경 |
| bonus stat | 보너스 스탯 | 추가옵션 |
| legion | 군단 | 유니온 |
| Monster Park Monday’s Creation Boxes | 몬스터 파크 월요일의 창조상자 | 창조의 월요일 상자 |
| Monster Park Tuesday’s Enchantment Boxes | 몬스터 파크 화요일의 인챈트 상자 | 강화의 화요일 상자 |
| Monster Park Saturday’s Festive Boxes | 몬스터 파크 토요일의 축제 상자 | 축제의 토요일 상자 |
| Ring of Restraint | 구속의 링 | 리스트레인트 링 |
| zone | 존 | 영역 |
| Continuous Ring | 지속의 링 | 컨티뉴어스 링 |
| Boss Damage | 보스 데미지 | 보스 몬스터 공격 시 데미지 |
| Critical Damage Ring | — | 크리데미지 링 |
| Totalling Ring | — | 링 오브 썸 |
| Risk Taker Ring | — | 리스크테이커 링 |
| Weapon Jump | — | 웨폰퍼프 |
| Level Jump | — | 레벨퍼프 |
| Durability Ring | — | 듀라빌리티 링 |
| Ultimatum Ring | — | 얼티메이덤 링 |
| Heroic World | — | 히로익 월드 |
| Interactive World | — | 인터랙티브 월드 |
| Grindstone of Life | — | 생명의 연마석 |
| Grindstone of Faith | — | 신념의 연마석 |
| Erda Shower | — | 에르다 샤워 |
| Erda Shower/Fountain | 에르다 샤워/분수, 에르다 샤워/샘 | 에르다 샤워/파운틴 |
| Mo Xuan | 모현 | 묵현 |
| True Arachnid Reflection | 진정한 거미 반사, 진거미 반사 | 스파이더 인 미러 |
| Solar Crest | 태양 문장 | 크레스트 오브 더 솔라 |
| Flame Emblem | 화염 문장 | 불꽃의 문양 |
| Sol Janus | 태양 야누스 | 솔 야누스 |
| Cyclic ring | 순환 고리 | 순환의 고리 |
| Primal Crystal | 원시 수정 | 태초의 결정 |
| Burning Field | 불타는 들판 | 버닝 필드 |
| Glowing Cube | — | 레드 큐브 |
| Bright Cube | — | 블랙 큐브 |
| Absolab | — | 앱솔랩스 |
| Arcane Umbra | — | 아케인셰이드 |
| Arcane | — | 아케인셰이드 |
| Eternal | — | 에테르넬 |
| Dawn | — | 여명 |
| Pitched | — | 칠흑 |
| Nodestone | — | 코어 젬스톤 |
| Chosen Seren | — | 선택받은 세렌 |
| Kaling | — | 카링 |
| Gloom | — | 더스크 |
| Darknell | — | 듄켈 |
| Lotus | — | 스우 |
| Night Troupe | 나이트 트룹 | 나이트 트루프 |
| Premium Surprise Style Box | 프리미엄 서프라이즈 스타일 박스 | 스스비 |
| PSSB | — | 스스비 |
| Leap | 도약 | 리프 |
| Luxe Sauna | 럭스 사우나 | VIP 사우나 |
| Sunny Sunday | 화창한 일요일 | 썬데이 메이플 |
| Mount | 탈것 | 라이딩 |
| Monster Park Clear EXP | — | 몬스터 파크 클리어 경험치 |
| Monster Park Extreme | — | 익몬 |
| Star Force enhancements | — | 스타포스 강화 |
| Star Force | — | 스타포스 |
| Elite monster | — | 앨리트 몬스터 |
| HEXA Matrix | — | 헥사 매트릭스 |
| Ability resets | — | 어빌리티 재설정 |
| Treasure Hunter EXP | — | 트레져 헌터 경험치 |
| Sol Erda | — | 솔 에르다 |
| Rune Appearance Cooldown reduction | — | 룬 재등장 및 재사용 대기시간 감소 |
| Combo Kill EXP | — | 콤보킬 경험치 획득량 |
| Rune EXP buff effect | — | 룬 경험치 버프 효과 |
| Magnificent Soul | — | 위대한 소울 |
| Monster Collection | — | 몬스터 컬렉션 |
| Mysterious Monsterbloom | — | 의문의 모몽 |
| Spiegelette | 슈피겔레트 | 슈피겔라 |
| Haste Fever Time Booster | 가속 열풍 시간 부스터 | 헤이스트 피버 타임 부스터 |

## 적용 시 주의사항

- `/썬데이`의 기존 고정 번역에서 용어·표현을 가져왔습니다. 고정 혜택의 수치·레벨·횟수·제외 조건은 사전에 넣지 않고 원문을 따릅니다. 썬데이 표시에서 숨기는 주문의 흔적 안내도 일반 번역에서 삭제하는 규칙으로 옮기지 않습니다.

- 영어 원문에 대응해 교정합니다. `Familiar → 퍼밀리어`를 이유로 실제 펫을 뜻하는 `Pet`까지 바꾸지 않습니다.
- `Arcane`, `Eternal`, `Dawn`, `Pitched`는 사용자가 지정한 장비·세트 명칭 문맥에 적용하며 일반 단어까지 무조건 치환하지 않습니다.
- 긴 용어를 먼저 구분합니다. 예를 들어 `Grand Sacred Symbol`은 `Sacred Symbol`과 별도 항목입니다.
- 대소문자·연속 공백·일반/곡선 아포스트로피 차이를 정규화하고, `Node/Nodes`처럼 s가 붙는 일반 복수형을 인식합니다. 불규칙 단·복수나 다른 철자는 별도 등록이 필요합니다. 원문의 수치·조건은 바꾸지 않습니다.
- 표의 세 열과 `사용자 확정 용어` 제목을 유지합니다. 파일 누락·빈 교정표·잘못된 행·중복 영어 원문은 오류로 처리하여 교정 없이 조용히 번역하지 않습니다. 파일 밖 설명 문장은 AI 지시문으로 전달하지 않습니다.
- `Shagri-La`는 사용자 제공 영어 표기를 그대로 기록했습니다. 다른 철자의 원문을 발견하면 별칭 여부를 확인한 뒤 추가합니다.

## 번역명 미정

선택된 표현 `크루시블 배지`는 이번 요청에서 대체 번역이 지정되지 않았으므로 확정 교정표에 넣지 않았습니다.
