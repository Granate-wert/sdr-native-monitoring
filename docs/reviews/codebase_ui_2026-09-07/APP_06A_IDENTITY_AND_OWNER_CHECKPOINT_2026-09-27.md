# APP-06A: identity и подтверждённое освобождение RX

Дата: 2026-09-27. Проверенный source checkpoint:
`c22361f1859508e201eb9c01cd738e8e58c7c16c`.
Рабочий checkout:
`C:\Users\posta\.codex\worktrees\app05-exact-witness-opt\SDR Native Monitoring`.
Это backend, используемый UI V2; старый интерфейс не переписывался.

## Таймер и статус roadmap

Существующий четырёхчасовой active-work timebox APP-05 завершён около08:44 MSK
с исключением пользовательской паузы. Новый четырёхчасовой таймер не создан.
Согласно handoff/roadmap, APP-05 сохраняет лучший проверенный результат и
незакрытые acceptance-долги, после чего продолжается APP-06.

APP-05 performance — OPEN. APP-06A — PARTIAL: сделаны перечисленные ниже
identity/owner prerequisites, но ещё нет общего capability/catalog/router.
APP-06B — PARTIAL (ранние official HackRF source/staging результаты);
shared frozen package closure не закрыт. APP-06C/D и APP-07 — OPEN.
Общая цель APP-00…14 остаётся active; финальный Windows release не принят.

## Реализовано в этой итерации

1. **Один IIO context на native owner — `3397de3`.**
   PlutoDevice больше не использует identity временного context, открывая
   отдельный context для приёма. Probe/capabilities читаются через функцию
   table того же открытого owner. При ошибке context закрывается.
   Windows native тесты проверяют ровно одно создание/освобождение.
   Linux source обновлён, но Linux build в этой итерации не выполнялся.

2. **Stop не теряет незакрытого владельца — `2340d5b`.**
   При живом poller после timeout или ошибке native join/disconnect service
   сохраняет ссылки и cleanup obligation. Новый Start, выбор маршрута и
   Sweep lease блокируются; нет скрытого cleanup/restart при следующем Start.
   Явный повторный Stop может завершить освобождение.
   stop_event блокирует поздние spectrum/persistence до преобразования данных
   и перед публикацией. Это не универсальный deadline для native SDK join.

3. **Автоматические aliases только по serial — `6495f14`.**
   Одинаковые model labels, один USB, стандартный IP и устаревший opaque key
   больше не объединяют устройства. Совпадающий наблюдаемый normalized serial
   даёт один device с USB/IP aliases. Unknown serial сохраняет отдельные
   route candidates; route hash не является stable physical identity.

4. **Identity проверяется перед Configure на RX owner — `7734b12`.**
   PlutoDevice, fixed-band engine и continuous Sweep coordinator принимают
   expected_serial и проверяют его на том context, который затем будет
   настраиваться и принимать RX. Missing/mismatch отклоняется до RF attr
   writes, channel enables и buffer creation; rejected context освобождается.
   Python требует exact integer native identity protocol1 для known serial.
   Старый runtime не обходится отдельным Python probe.

   Expected identity передаётся при выборе, RTBW Start, в NativeSweepSource/
   lease, sequential Sweep и обоих continuous factory paths. Private serial
   field Sweep source исключён из repr. Unknown-serial single-route совместимый
   вызов сохраняется, но не считается подтверждением stable identity.

5. **Согласованная нормализация — `c22361f`.**
   ASCII проверяется до lowercase. В частности, Unicode Kelvin sign не может
   стать ASCII serial в Python, тогда как native его отвергает.
   Empty/placeholder/non-ASCII/control-containing serial остаётся unknown.

Изменения control-plane; новые raw-I/Q Python/Qt циклы, FFT/DSP/rendering
алгоритмы, product cadence или настройки брандмауэра не добавлены.

## Проверки

| Gate | Результат и граница доказательства |
| --- | --- |
| Full UI V2, source2340d5b + staged native3397de3 | 894 tests /66 skipped/0 failures,261.396s; modules outside[] |
| Full UI V2, exact c22361f + staged native c22361f | 894 tests /66 skipped/0 failures,252.947s; compiled tests deferred[], modules outside[] |
| Focused identity/route/owner/Live/Sweep/application tests на c22361f | 71 PASS,4.393s |
| Compiled Python identity bindings с test-only IIO DLL | 1 test/3 owner subtests PASS; context cleanup и no RF writes/enables/buffers |
| Final clean c22361f CPU StageOnly build | 34/34 CTest PASS,40.55s; manifest/source/ABI/schema5/self-test PASS |
| New helper/tests | Ruff PASS; scoped helper mypy PASS; diff-check PASS |
| 4 historical service files Ruff baseline comparison | 64 existing findings→64;0 new code/message kinds, не blanket lint PASS |

Full UI V2 gate — offscreen/source regression, не visible desktop/RF/DWM
acceptance. Main gate process использовал явно injected staged module;
отдельные subprocess tests могут читать старый canonical module. Известные
NaN comparison RuntimeWarnings в persistence tests присутствуют; не заявлять
«нулевые warnings». Native build также имеет существующее getenv warning.

До исправления route tests воспроизвели12 failed assertions/subtests;
owner-release tests —6 failures/1 error. Первые некорректные test fixtures
(неполный DeviceCapabilities и close вместо native disconnect) отдельно
исправлены и не представлены как product bugs.

## Native и EXE provenance

Staged CPU extension SHA-256:
`0db493eb57a124098e479d8ecd9d554da32dbaa247bd5f881776784118be0f86`.
Final manifest source — c22361f, Python ABI cp313-win_amd64,
spectrum schema5, identity protocol1. Artifact лежит в
`native/sdr_core/out/build/windows-msvc-cpu/python`.

Canonical active extension остаётся прежним:
`b218486cffffbc451018e515daf0d049988d7b9dc139340f4c48d91383c8ad48`.
StageOnly его не заменил. Diagnostic EXE по approved static path остаётся
sourceaec7a85; этот EXE не содержит новые APP-06A изменения. В этой итерации
EXE не пересобирался, APP-00 installation и firewall rules не менялись.
Known-serial source caller со старым canonical native получает явный отказ
протокола; это не основание объявлять старый EXE новым или обходить guard.

## Реальная проверка подключённого Pluto

Независимый read-only IIO inspection: одно USB устройство с model label
AD9364; `hw_serial` и `usb,serial` пусты. Native same-context observation тоже
не даёт known serial. Это честный negative physical identity case; подпись
модели сама по себе не устанавливает chip/RX/transport topology.

На c22361f/staged0db493… в fresh physical source process:

- У всех трёх owners явно переданная неподтверждённая expected identity
  отклонена на identity gate. После каждого отказа новый обычный PlutoDevice
  успешно открывался и disconnect подтверждал connected=false.
  Суммарные guard+reopen времена124.239/120.086/120.197ms — одиночные
  наблюдения, не latency budget/percentiles. RX из этих negative calls не запускался.
- Обычный unknown-serial RTBW service RX: requested/applied Fs61.44MS/s,
  RF BW56MHz, center2.4GHz/gain30dB/FFT4096/CPU, unit dBFS/bin.
  Readback fields: center_hz/sample_rate_hz/analog_bandwidth_hz/gain_db.
  За8s опроса наблюдалось156 разных latest sequence values.
  Это sampled service snapshots, не FFT LPS, UI FPS или транспорт61.44MS/s.
- Stop вернул connected state, engine disconnected=true, poller joined=true;
  close очистил owner refs, release latch=false, remaining Python threads[].
  Elapsed8.426s — short lifecycle check, не benchmark/soak/recording continuity.

Физический положительный known-serial case/смена именно физического устройства
не доказаны; положительные и changed-context cases проверены mocks.
Эта проверка backend без Qt canvas, не common UI/exact frozen EXE acceptance.
Firmware/driver/ZeroTier/security settings не изменялись, TX не запускался.
Радио и процессы этой проверки завершены.

## Следующий реальный пакет

Не повторять этот короткий smoke или APP-05 profiling как «новый этап».

1. Завершить APP-06A: coherent capability observation и retained temporary
   probe cleanup; общий каталог поверх существующих DeviceCapabilitySnapshot/
   Inventory и family-specific requests. Runtime availability, declared support,
   unknown/unverified и physical evidence должны различаться.
2. APP-06B: совместимая app-local HackRF/libusb/IIO DLL closure и frozen build.
   Source/staging high-Fs RX не доказывает поддержку текущего EXE.
3. APP-06C: один Analyzer owner/router и нормальный выбор Pluto/HackRF/tinySA
   в UI V2; отсутствие cross-device/session/epoch/unit stale frames, stop
   barrier при переключении. tinySA сохраняет приборные dBm и points≤10001.
4. APP-06D: actual common UI high-load matrix; отдельно достижимый RJ45
   AD9363 и tinySA variants, только с реальным hardware evidence. Затем APP-07.

Не забывать APP-05 долги: Visual contention/freshness, strict moving-latest,
unforced memory closure, populated-area65%, QHD/DPI, RF-qualified windows/Pd
и current frozen EXE permission/physical acceptance. Они не стали PASS.

Работу и самостоятельное ревью выполнял основной агент. Субагенты/отдельные
review models в этой итерации не использовались. Main Legacy/DFL dirty work
сохранён. Этот отчёт публикуется отдельно от локальных raw evidence/history.
