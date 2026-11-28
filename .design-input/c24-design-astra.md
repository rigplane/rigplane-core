# C24 design second opinion (GPT-6 Astra, codex exec read-only, 2026-11-28 ~02:22-02:50 EST, escalation MOR-4930)

**Выбираю A: один последовательный recovery-owner, без отмены SOFT ради FULL. Но ограничить только discovery недостаточно для гарантии PTT-off: конечная граница для (ii) пока не доказана.**

**Причина «попросил один раз».** На локальном `origin/main=d0b09bc4dc` воспроизвёл её исходными watchdog и RX pump, загруженными в память, с `VirtualClock`:

- После stall возобновляются UDP-ping, но CI-V payload отсутствует. `_civ_rx.py:1674–1682` выполняет `quiet but alive → continue`, обходя deadline на `1734–1770`.
- `_civ_recovering` остаётся true; `request_recovery_now()` на `1874–1880` отвергает следующий notice как «already recovering».
- За **69,01 виртуальной секунды**: один data-start, ноль recovery, оба task живы, readiness=false. Это воспроизведение механизма симптома, а не доказательство порядка событий в отсутствующем исходном C24 trace.
- Re-arm уже существует на `1976–1984`; простое добавление ещё одного re-arm проблему не решает. Отдельно FULL действительно теряется при coalescing: `_watchdog_recover():1944–1948` запрашивает его через `soft_reconnect()`, а `session_lifecycle.py:1294–1304` присоединяет вызов к существующему SOFT.

**Минимальный вариант A.**

1. В существующем lifecycle хранить неудовлетворённый запрос FULL. SOFT заканчивает ограниченную попытку; затем тот же owner выполняет FULL. Запрос FULL нельзя считать выполненным завершением SOFT. До перехода не запускать очередные SOFT-retry и audio-tail.
2. Deadline распространяется на всю задерживающую SOFT работу, включая CI-V/audio discovery и cleanup. Передавать абсолютный deadline в ограниченные ожидания; внешний `wait_for(soft_reconnect())` снова вводит проблемную отмену.
3. Незавершённое восстановление readiness остаётся основанием для эскалации независимо от ping. Завершать этот эпизод только по данным текущего поколения. После каждого выхода обеспечить действующий recovery либо вооружённый watchdog; `_civ_recovering` не заменяет task.
4. Notice с recipient=0 при not-ready означает «обеспечить восстановление», включая повторную проверку после текущего FULL. До реального owner-stop сохранять managed-TX debt; SOFT exhaustion должен переходить к FULL **до** `_mech.release()`, уничтожающего supervisor.

**Граница (ii).** Для существующего reboot-теста остаётся **14,0 с после окончания downtime/busy**: `17,6 − 0,1 − 3,0 − 0,5`. Сохранить это приёмочное требование. Однако A пока **не гарантирует** его: `_ONE_RECOVERY_ROUND_S=10` — тестовый запас, не product deadline; discovery уже допускает `3 × 10 с`, отдельно остаются handshake, audio и joins. Честная текущая верхняя граница — **неограниченная**. Для конечной гарантии нужно доказать ограниченность всего пути до wire OFF при заданной отзывчивости восстановившегося радио; таймер, заканчивающий попытку ошибкой, такой гарантии не создаёт.

**B дороже и тоже не доказывает bound.** Одного helper недостаточно без транзитивного аудита. Обязательные группы await: **(1)** predecessor/drain/shared recovery; **(2)** watchdog, commander, RX pump; **(3)** audio RX/TX и transport retirement; **(4)** token removal; **(5)** discovery/login/GUID/status/startup; **(6)** managed-TX locks, provider transitions и effects; **(7)** scope/state/audio tail; **(8)** owner teardown. Это карта аудита, **не доказательство проверки каждого await**. В `b7ecbbd8` уже остаётся потеря отмены на `stop_pump():1094`; распространять preemption дальше не рекомендую.

**Точки изменения:** `CivRuntime._civ_data_watchdog_loop`, `request_recovery_now`, `_watchdog_recover`; lifecycle `soft_reconnect`, `_run_recover`; control-phase `_on_conninfo_notice`, `soft_reconnect`, `_after_reconnect_parts`; для deadline — transport `connect/_discover/_ready_handshake` и audio connect. Сохранить исправление watchdog-join из b7 и независимые stop-regressions; убрать reservation/preemption/source-epoch machinery. Pump-join исправить для owner-stop.

**Тесты:** оба указанных сценария на настоящих watchdog/RX pump; ping-only после первого stall; notice во время watchdog/pump join; id-0 в FULL-tail; сохранение TX debt; disconnect/shutdown при успехе, ошибке и deadline. Один виртуальный clock, включая `_control_phase.time.monotonic`; проверять wire OFF отдельно от подтверждения. Файлы и commits не изменены.
