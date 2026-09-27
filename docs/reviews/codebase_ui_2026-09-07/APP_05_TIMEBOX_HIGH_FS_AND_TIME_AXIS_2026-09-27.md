# APP-05 — ограниченная итерация, высокая Fs и стабильная ось времени

Дата: 2026-09-27. Инженерный лимит: 4 часа активной работы с 04:42 MSK.
Пауза пользователя 05:06:20–примерно 05:07:53 исключена; контрольный рубеж
примерно 08:44 MSK. APP-05/APP-07/release не объявлены закрытыми.
Изменения относятся к UI V2, скалярной телеметрии/qualification, low-rate
cleanup/admission адаптеров и изолированным build/runtime guards. Official
HackRF private factory получил same-handle identity guard; это не изменение
численной FFT/DSP математики. Native artifacts пересобраны в явно раздельных
CPU/CUDA/official staging scopes ниже. Legacy/DFL, firmware и правила Windows
Firewall не менялись.

## Новая политика измерения

Основная нагрузка — высокая **подтверждённая applied Fs**, а не попытка
притвориться непрерывным raw-I/Q recorder. Для текущего USB Pluto применены
61.44 MS/s, CPU, FFT 16384, default buffer 262144 samples. Это не обещание
получать в ПК 61.44 миллионов комплексных samples/с. Delivered rate,
host drops, timestamp quality и неизвестные RF gaps указываются отдельно.
Первоначальные 5/6.5/10 MS/s — диагностические контролы, не основной target.

По окончании лимита выбрать лучший проверенный вариант без регрессии и
продолжить APP-06 с явно открытым performance-долгом APP-05. Timer не
превращает недоказанный 50-ms/soak/hardware/release gate в PASS.

## Изменения исходников

Чистый изолированный checkout:
`C:\Users\posta\.codex\worktrees\app05-exact-witness-opt\SDR Native Monitoring`.

- `e9fe541`: opt-in наблюдатель считает фактический прирост native
  `iq_samples_received`/`iq_blocks_received` между двумя reads того же
  engine; это не rolling цифра из status. Sweep observer после Stop
  дожидается обоих terminal-gap paints (до 2 с), прежде чем закрыть окно.
- `f44b6ab`: ось RTBW Waterfall предлагает постоянную 1/2/5-шкалу возраста
  с положением по реальным producer timestamps. Масштаб не пересчитывается
  от одной новой строки при заполнении ring; hysteresis защищает границу.
  Длительные producer-time интервалы не заполняются фиктивными временными
  ticks. Обычная частота строк ниже configured cap не объявляется паузой.
  Gutter не сжимается на каждом кадре. Sweep `#sequence P/C/G` сохранён.
- `c9bd8c8`: bounded геометрическая модель временной возможности встречи
  одного импульса при равномерном случайном времени его начала. Использует
  union квалифицированных окон захвата, ширину импульса и минимальную
  длительность перекрытия. Требует явно полного ledger и hardware либо
  synthetic timing quality; estimated/unknown/replay дают `None`.
  Это не вероятность амплитудного обнаружения Pd и не оценка из FPS/Fs.
- `8bb3657`: скалярный bounded meter уникальных source revisions в
  завершившихся Qt paints. Repaint от density/zoom/chrome не считается
  новым кадром; Hide и новый epoch начинают отсчёт заново. Backend добавляет
  observed host block rate. Отдельная строка UI различает кадр, ingress
  буфера и reciprocal complete-pass LPS; RF duty/revisit не выдумываются.
  Числа форматируются по existing 4-Hz status pacing, без нового worker
  или численного pipeline. Observer умеет сохранить один Live QWidget PNG.
- `e3a48ab`: исправлен единственный сбой первого полного прогона —
  непереведённое слово в RU timing tooltip; продуктовые timing scopes
  переведены целиком. Повтор на exact HEAD: **892 tests / 66 skipped / 0
  failures, 258.607 s**. Это UI V2 source gate, не EXE/hardware acceptance.
- `b572cf9`: opt-in layout/axis capture на реальной product composition
  с fake-портами; явные size/DPR/контуры текста, font/clip, collision и
  optional заранее заданный area gate. Не рисует вымышленный интерфейс.
- `55582a8`: отклонить stale partial revision/partial после terminal в
  скалярном fresh-paint meter. Новый тест сначала воспроизвёл FAIL 3!=2,
  после исправления 36 focused tests PASS. Это telemetry correctness,
  не ускорение renderer и не изменение алгоритмов Sweep.

Репродукция старой оси: один deterministic full-ring jitter сценарий с
неизменной геометрией дал **7** разных комплектов подписей на `e9fe541`,
на новом коде — **1**. Отдельные тесты проверяют зеркальную ориентацию,
нерегулярные строки/паузу, неизвестное время, resize, locale и gutter.
Это доказательство исправления алгоритма подписи, ещё не desktop-видео
или приёмка всех мониторов. 73 focused проверки до telemetry PASS;
66 focused после telemetry PASS; scoped mypy/Ruff/diff-check PASS.
Финальный полный повтор на `55582a8` после meter-fix: **893 tests / 66
skipped / 0 failures, 257.833 s**. Четыре scoped mypy files, 32 rate/physical
observer tests и static gates PASS. Это source UI V2, не EXE release gate.

## Физические наблюдения на максимальной Fs

Evidence: `evidence/app05_timebox_20260927/`, JSON содержит exact source,
runner SHA, native module SHA и параметры. Окно фактически 1400×850 logical,
DPR 1.75, не exact FHD/QHD desktop. Это instrumented Qt paint-return, не
DWM scanout, не hardware FFT LPS и не абсолютная RF-accuracy.

| Проверка | Delivered I/Q MS/s | Уникальные Spectrum paints | p95 промежутка нового спектра | p95 Waterfall paint |
| --- | ---: | ---: | ---: | ---: |
| Fs61.44 / FFT16K / Direct baseline, 45 с | 7.5610 | 541 | 104.21 ms | 8.76 ms |
| Fs61.44 / FFT16K / Visual baseline, 45 с | 7.5631 | 498 | 112.36 ms | 7.30 ms |
| Fs61.44 / FFT16K / Direct, стабильная ось, 45 с | 7.5402 | 579 | 94.16 ms | 6.74 ms |
| Fs61.44 / FFT16K / Visual, periods/Hide–Show, 60 с, e3a48ab | 7.5399 | 685 | 108.37 ms | 6.26 ms |
| Fs61.44 / FFT4096 / Visual, 60 с, 55582a8 | 7.5645 | 1364 | 71.16 ms | 6.69 ms |

В минутном exact-source run `usb_pluto_maxfs_visual_periods_01.json`
все 18 lifecycle checks PASS, native samples=452460544/blocks=1726 за
60.0086 s. Live capture показывает «Новый кадр Qt: ≈81 мс · Буфер → ПК:
≈37 мс · Радионаблюдение: нет оценки». Последний scalar native block
rate и 4-Hz UI readout сняты не в один момент: их reciprocal не обязан
совпасть до миллисекунды. Private-memory delta +8.08 MB — не long-soak
доказательство отсутствия утечки. workers[]/reserved0 после close.

В первых двух runs count ratio delivered/applied ~0.123. **Это не измеренный
RF duty 12.3% и не P_detect=12.3%.** Software drop counters в этих runs
нулевые, но hardware overflow/capture continuity ими не подтверждены.
Наблюдатель `result=pass` означает bounded Start/Stop/close и достаточное
число samples, не PASS нормативной производительности. Два sequential
baseline и один после исправления не являются matched ABBA доказательством
ускорения. Несопоставимые более ранние 6.5 MS/s Visual цифры нельзя
приписывать одной Fs или новому коду.

Sweep Fs61.44/FFT4096, 2300–2600 MHz, usable36/overlap2: повторный run
`usb_pluto_sweep_61p44_fft4096_dark_02.json` прошёл partial→final одного
прохода на обеих поверхностях **до Stop**, затем terminal-gap на обеих
поверхностях. Нативный terminal gap и закрытие с workers[]/reserved0
подтверждены. Предыдущий run `_dark_01` закрывал окно раньше верхнего gap
paint; FAIL сохранён, не переписан. После изменения observer он ожидал
реальные paints, а не изменял продукт, чтобы замаскировать результат.

Повтор на чистом `55582a8`, high-contrast, те же Fs61.44/FFT4096 и 9
segments: `current_maxfs_sweep_contrast_01.json`, все 11 gates PASS,
включая one-pass partial→final на обоих canvas до Stop и post-Stop gap.

`current_usb_maxfs_fft4096_visual_01.json`: 453771264 native samples / 1731
blocks за 59.9872 s, analytical FFT/s≈3687.98, host snapshots/s≈59.71.
Все 18 lifecycle gates PASS; capture показывает ≈42 ms для нового Qt кадра
и ≈31 ms для host-буфера. Это разные временные границы. Qt publication,
analytical FFT и реальная перерисовка не подменяют друг друга. Max gap≈1.089 s
включает намеренное Hide, не является one-second visible stall. Private-memory
delta +13.47 MB, workers[]/reserved0; не long-soak proof. Полный source suite
начался в конце/на teardown этого run: не использовать результат как чистое
межверсионное speed A/B. Все эти физические JSON сняты до последующего
release-pipeline rebuild native module; их exact native SHA хранится в JSON.
Последующая прямая сверка JSON показала: native binary SHA этих трёх
current-source физических runs **совпадает** с `ee9a376b...` после pipeline.
Rebuild/обновление manifest source_commit не изменили этот binary; нельзя
приписывать им новый native speed gain или объявлять SHA mismatch без проверки.

## FHD/QHD — числовой Qt layout, не приёмка физического монитора

Все final captures/JSON — exact `b572cf9` + один runner SHA. Fake input,
полный normal UI V2 и SYNTHETIC producer-time ring из 300 rows; локальное
кормление Waterfall относится только к layout/axis, не к RF/coherence speed.
`QT_QPA_PLATFORM=offscreen`, свежий процесс на каждый scale. Нет изменения
параметров Windows Display, Firewall или реального прибора.

| Масштаб | FHD 1920×1080: доля двух ViewBox | QHD: доля двух ViewBox |
| --- | ---: | ---: |
| 100% | 71.46% | 78.26% |
| 125% | 64.88% | 73.13% |
| 150% | 58.52% | 68.14%* |
| 175% | 52.39% | 63.27% |
| 200% | 46.49% | 58.52% |

Чередовались RU/EN, dark/light/high_contrast и top/bottom Waterfall. В
10 final runs: primary/source/mode/status/periods целиком внутри client,
однострочный текст периода помещается, осевые подписи не пересекаются,
gutter не меняется на Hide/Show, workers[]/reserved0. Layout `passed` здесь
не означает PASS доли площади, если `--minimum-plot-fraction` не задан.

\* QHD при DPR1.5: integer QWidget logical width1707 округляется до
**2561**, не 2560 pixels. Первоначальный exact2560 gate честно FAIL и
сохранён; отдельный rounded2561 run PASS. Не объявлять это exact QHD.

Важный открытый дефект: при nominal1366×768 **после данных** полезная
площадь=60.88%, ниже нормативных65%. Opt-in area gate `nominal_1366_area_gate`
FAIL сохранён. Existing APP-02 area test проходит *до* данных и не
покрывает этот state. Новая строка периодов также расходует часть площади.
Задача: компактный responsive measurement footer/header с теми же
метриками и доступными controls; не подгонять gate скрытием Waterfall,
уменьшением шрифта или обрезанием quality. Текущая компоновка на high-DPR
читаема в просмотренных captures, но не удовлетворяет цели площади.

## Совместные visible synthetic ABBA/BAAB

Чистый `b572cf9`, 65K×64, Windows Qt1400×850/DPR1.75, default15-Hz
ImageItem; 15 s на каждый steady block. Одинаковые runner/native, all layers
ON, no split lane/override. Оба порядка PASS integrity/fixed-target/declared
**500-ms diagnostic** silence gate и close workers[]/reserved0.
Не путать этот diagnostic threshold с APP-05 50-ms requirement.

- ABBA: Direct1018 vs Visual862 уникальных Spectrum first-paints за
  примерно30 s на mode; Visual gap p95=51.47/53.71 ms, heartbeat density
  age p95=153.03/158.36 ms.
- BAAB: Direct1023 vs Visual884; Visual gap p95=50.96/52.33 ms,
  heartbeat density age p95=147.14/153.38 ms.
- ABBA Stop intent→idle34.59 ms, click-return0.436 ms. Один fake Stop,
  не p95 campaign и не physical driver latency.

Это повтор текущего совместного pipeline, не A/B speed gain нового
алгоритма. Degradation Visual cadence против Direct остаётся открытой;
достаточные средние/ready-age не отменяют cadence/Visual freshness gates.

## Что пока остаётся открытым

1. Долгий control/Stop/Hide/Show campaign; полный source suite после meter-fix
   уже PASS в указанном scope.
2. Physical monitor transitions/DWM/EXE; закрыть новый explicit measured
   area FAIL. High-Fs source readouts и короткие Qt size/DPR checks уже
   проверены в указанном ограниченном scope.
3. Получение квалифицированных полного ledger окон RF capture и реального
   revisit для адаптивной вероятности встречи импульса. Пока UI правильно
   указывает unknown, новая модель не вызывается с host-estimated окнами.
4. Общая ready→paint 50-ms, свежесть всех слоёв, long soak/memory/control
   campaign и поддерживаемые device/transport cells. Для HackRF/tinySA
   production composition и собственные high-load профили относятся к APP-06.
5. Новый EXE/проверка сборки и окончательная APP-12…14 приёмка.

Этот отчёт локальный ignored документ; коммиты source не включают
автоматически JSON/PNG/документацию в origin. Проверки и результаты ниже
дополняются по факту, не предвосхищают PASS.

## Публикация и сборка — текущая проверка

Source-коммиты до `c865521` включительно нормально опубликованы в
`origin/codex/app-00-v2-backend-integration`; основной dirty workspace не
переключался. `c865521` содержит отдельно force-added source contract
`docs/implementation/APP05_SOURCE_TIME_AND_PERIOD_CONTRACT.md`; остальные
локальные ignored JSON/PNG/reports этим push не публикуются.

Полный CPU pipeline с exact `c865521`: 34/34 CTest, source/native gates PASS,
но freeze FAIL из-за установленного PyInstaller 6.22.0: генератор записал
`hide_console=''hide-early''`. Это известная upstream regression; EXE не
создан, старый approved static EXE и Firewall не менялись. Source/native
продуктовая ошибка этим failure не установлена. Native rebuild активировал
ignored `.pyd` SHA `ee9a376b184894b5dc3f43bb9edbbae66eb089140aec257af94376294d2d4664`;
Сверка max-Fs RTBW/Sweep JSON этой итерации подтвердила identical binary SHA;
manifest source_commit и product Python source при этом различаются. Synthetic
ABBA runner без `--native-module` записывает native_smoothing_sha256=null;
не заявлять для него загрузку/замер нативного smoothing kernel.

`35c645a`: pinned dev freezer PyInstaller 6.22.3 и ранний fail-closed preflight
реального генератора до native build. Три теста PASS (real generator,
malformed/changed-policy negatives, ordering), Ruff/diff PASS; commit pushed.
Обновлены только PyInstaller и его hooks, не Qt/Numpy/SDR runtime. Повторный
полный pipeline на exact `35c645a` **PASS**: 34/34 CTest, неизменность source
snapshot, native identity, 357-file manifest verification, frozen native/UI V2
offscreen/default-native, package-local libiio и tinySA runtime checks.
Source snapshot SHA `58a99506a251e63a5dbdeea3e128945afa32a2dafe03bce27727c35d6a25f88c`.
357 против старых 660 files отражает новый freezer/hooks output; не заявлять
старую manifest count или binary-attested/signed provenance. Это diagnostic
CPU build, не финальная performance/release приёмка. Artifact:
`C:\Users\posta\.codex\worktrees\app05-exact-witness-opt\SDR Native Monitoring\dist\SDRNativeMonitoring-CPU-APP05-35c645a-20260927\SDRNativeMonitoring\SDRNativeMonitoring.exe`.
Сохранение `console=True + hide-early` оставляет stdout CLI diagnostics;
исправление spec syntax само по себе не доказывает desktop console behaviour.

Computer Use (`@oai/sky`) открыл exact-35c645a EXE, получил настоящий
UIA tree и Windows capture UI V2; отдельной консоли не наблюдалось. Discover
нашёл один источник, но показал новый Windows Security network-permission
prompt для нового пути. Никаких действий с prompt/security settings не
выполнено. Единственный exact-path test PID 22572 остановлен; RX из этого
EXE не запускался. Это desktop startup smoke, не packaged physical RX/soak
acceptance. Старый approved static path не заменялся.

## Stop под нагрузкой — расширенный bounded campaign

Clean `35c645a`, visible Windows Qt1400×850, SYNTHETIC65K×64/200Hz/Visual,
10 отдельных Start→Stop cycles по5 s. Stop наблюдал `projection:running`
каждый раз; искусственная driver задержка120 ms. JSON
`current_stop_projection_10cycles_01.json`, exit0:

- click-return p95 **0.729 ms**, max0.766 ms;
- intent→worker p95 **1.486 ms**;
- intent→idle p95 **155.577 ms**, max155.688 ms;
- driver120 ms включён в idle delay; 12–13 GUI heartbeat events во время
  задержки каждой остановки, без GUI-blocking ожидания драйвера;
- remaining_workers[], post-close reserved0, budget rejections0, peak≈190.55 MB;
- after-context private bytes +2.54 MB против initial, normal GC без forced
  collection. Fixture/source locals и непокрытые allocators ограничивают
  утверждения; не использовать это как long-term no-leak proof.

Это fake-driver/control test, не физический libiio Stop p95 и не DWM.
Следующий distinct gate — пятиминутный same-source memory/page/viewport churn.

`current_visual_memory_churn_300s_01.json`, clean35c645a, exit0:300 s
Visual65K×64/200Hz, 25 page switches, 42 viewport changes, 64 scalar memory
samples. Heartbeat p50/p95/p99/max=10.97/23.54/26.98/42.78 ms; единственный
Stop→idle27.91 ms без fake delay. После normal close/context return workers[],
reserved0/rejections0, budget peak156.44 MB. Private bytes initial648097792,
after-context653869056 (+5.77 MB). Это bounded five-minute stress, не часовой
physical-RX soak и не доказательство отсутствия всех allocator/Qt leaks.
Default15Hz Visual и single-worker pipeline сохранены; strict moving-latest
freshness gate в этом churn observer не включался, не объявлять его PASS.

## Размер RX-буфера на максимальной Fs — matched physical ABBA

Clean35c645a, один script/native SHA (`ee9a376b...`), USB `usb:3.4.5`,
Fs61.44MS/s/**RF BW56MHz по actual readback**, CPU FFT4096/Visual/default15Hz,
locked vertical range,
Windows Qt1400×850/DPR1.75. Каждый отдельный процесс:3s warmup и45s measure.
Без Hide/Show, split worker, resource profiler или product default changes.
Физический RF сигнал не контролировался; это instrumented one-device profile.

| JSON stem | Buffer samples | Native ingress MS/s (whole measured interval) | Unique Spectrum paints | Interpaint p95/p99 ms | Density uploads | GUI model→paint p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| maxfs_buffer_abba_A1_262144 | 262144 | 7.5291 | 839 | 84.09 / 103.37 | 586 | 36.05 |
| maxfs_buffer_abba_B1_65536 | 65536 | 6.9771 | 872 | 76.06 / 97.15 | 595 | 32.55 |
| maxfs_buffer_abba_B2_65536 | 65536 | 6.9820 | 836 | 81.65 / 101.25 | 583 | 34.37 |
| maxfs_buffer_abba_A2_262144 | 262144 | 7.5144 | 767 | 86.22 / 98.81 | 579 | 37.11 |

Четыре bounded lifecycle runs PASS, workers[]/reserved0, tracked_dirty_paths[].
В A1 один unmapped paint; не выдавать его за mapped/zero ambiguity. Analytical
FFT/s A≈3664–3668, B≈3405–3406; это не UI LPS. Меньший буфер уменьшил
delivered ingress и FFT throughput примерно7.2%, а modest paint/cadence
разница не повторена в BAAB и не достигла50ms. Сдвиг timestamp-age может
частично зависеть от nominal buffer duration, не hardware RF→screen proof.
Whole interval count — не усреднение двух rate readouts и не RF duty.

**Решение: default262144 сохранить.** Новый high-Fs profile отличается от
исторического 3-MS/s short negative `APP_05_PHYSICAL_RX_BUFFER_GEOMETRY_2026-09-25.md`,
но не оправдывает global buffer policy для USB/Ethernet/других SDR/recording.
В конце теста прибор остановлен; его last applied RF configuration изменена
этим RX тестом и не восстановлена, TX/firmware не использовались.

## Изолированная CUDA lane и настоящие golden checks

`f05790a` добавил `build_native_sdr.ps1 -StageOnly`, запрещающий сочетание
с `-Clean` до любых файловых/tool действий. Alternate lane проходит обычные
build/CTest/header/manifest/preflight проверки, но не заменяет application
module/manifest. Два guard tests PASS; промежуточный Ruff import-spacing FAIL
исправлен в `b9a6f2e`, затем Ruff/diff и tests PASS. Оба commit pushed normally.

Exact `b9a6f2e`, явный Python3.13 common venv, CUDA13.3/RTX3050 Laptop4GB:
staged build **38/38 CTest PASS**,45.78s, включая availability/runtime/device,
self-test, backend selection и C++ CPU/CUDA parity (max bindifference
1.52588e-05dB в одном tonal test). Старые missing-header-dependency objects
пересобраны clean-first; это не принятие stale ABI. Первая попытка без явного
Python выбрала checkout .venv без pybind11 и остановилась до configure; не PASS.

Staged artifact `native/sdr_core/out/build/windows-msvc-cuda/python/`
`_sdr_native.cp313-win_amd64.pyd`, SHA
`2ba948670759fde54bc8646e4e822014eb065a821881693389cf45f29c5d160a`.
Manifest source b9a6f2e/schema5/cuda_compiled=true/preflight PASS. Active CPU
artifact SHA ee9a376b... и его manifest проверены до/после, не изменились.
Никакой CUDA-default/EXE/SDR/firewall activation этим build не выполнен.

Дополнительно fresh-process canonical injection **того же staged module**
в `sdr_monitor._sdr_native` запустил существующие BackendSelectionTests и
CudaExecutionTests: **8 tests /0 skips PASS**. Deterministic generator exact
source создал12 NPZ+manifest в новом `cuda_goldens_b9a6f2e/`; весь набор
проверен reference-f64 и3 выбранных vectors accurate-f32/f64-accum со
существующими неизменёнными tolerance/axis/integrated-power constraints.
CLI/Legacy UI tests не запускались; generator и compatibility contracts
не использованы как product UI route. Это numerical/backend evidence,
не physical CUDA RX, GPU Qt rendering, full performance matrix или release.

## Текущий strict Hide/Show и Windows client geometry

Clean `b9a6f2e`, `current_visual_strict_lifecycle_01.json`, SYNTHETIC65K×64,
200Hz producer/default15Hz Visual,30s/5s page interval, Windows Qt1400×850
DPR1.75:3Hide/2Show. Fixed Show-target upload **2/2 PASS**,70.81/74.01ms от
request; строгие3последовательных heartbeat `latest==uploaded && idle`
**0/2 FAIL**, exit1, сохранён без ослабления правила. Worker/reservation0
после close. Это не свидетельство отсутствия первого восстановления или
RF потерь; sustained freshness остаётся OPEN, не превращать failure в PASS.

`windows_fhd175_layout_01.json/.png`: actual Windows-QPA client1920×1080,
logical1097×617/DPR1.75, RU/high-contrast/newest-at-bottom: geometry,
labels/period fit/controls/axis collision/HideShow gutter/cleanup PASS.
Plot area52.933% при этой уменьшенной logical geometry;65% area criterion
не включался, не выдавать overall layout PASS за полезную площадь PASS.

`windows_qhd175_layout_01.json/.png`: requested logical1463×823 expected
2560×1440, actual **2524×1440**, Qt/Windows clamp на текущем дисплее.
Exit1/geometry FAIL сохранён. Это не exact-QHD monitor/DWM acceptance;
OS display/resolution/DPI settings не менялись. Offscreen scale matrix
выше и эти real-QPA surfaces имеют разные scopes и не заменяют друг друга.

## Physical CPU/CUDA: два порядка, максимум applied Fs

Observer-only `8b48d97` добавляет explicit requested backend и actual applied
backend. Не повышает product cadence и не включает native CUDA по умолчанию.
Все восемь процессов используют **тот же staged CUDA binary**, SHA
`2ba948670759fde54bc8646e4e822014eb065a821881693389cf45f29c5d160a`,
с отдельно выбранным CPU/CUDA DSP. Applied Fs61.44MS/s, RF56MHz, FFT4096,
Visual/default15Hz, buffer262144, actualWindowsQt1400×850/DPR1.75,
vertical lock после warmup. FFT/s, ingress и distinct Qt paints — разные метрики.

| Серия/слот | Backend | Measure | Unique Spectrum paints | Paint-gap p95 |
| --- | --- | --- | --- | --- |
| ABBA A1 | CPU |45s|837|84.25ms|
| ABBA B1 | CUDA |45s|528|125.11ms|
| ABBA B2 | CUDA |45s|475|142.39ms|
| ABBA A2 | CPU |45s|768|90.79ms|
| BAAB B1 | CUDA |30s|357|120.86ms|
| BAAB A1 | CPU |30s|522|85.36ms|
| BAAB A2 | CPU |30s|540|84.12ms|
| BAAB B2 | CUDA |30s|300|140.56ms|

BAAB exact source `b8fffbb`, clean before/through/after campaign, native/
runner SHA identical across modes; no source edits or heavy concurrent tests.
GUI-model→paint p95 CPU34.48/34.28ms vsCUDA70.77/77.56ms. Native ingress
~7.48–7.54MS/s, terminal analytical rate~3601–3670 FFT/s; workers[]/reserved0
во всех случаях. Initial ABBA source8b48 starts clean, но более поздние source
edits вокруг конца кампании не позволяют заявить неизменность whole checkout
по всей первой серии. Поэтому отдельный clean BAAB важен; старые JSON сохранены.

Решение: **CPU остаётся default/рабочим выбором этого профиля**. Numerical
CUDA parity не ускоряет Qt rendering; этот результат не запрещает CUDA для
других FFT/batch/GPU profiles. 50ms/Waterfall LPS/DWM/RF duty/Pd не приняты.
Raw counts45s и30s нельзя напрямую сравнивать как одинаковые интервалы.

## APP-06B подготовка: identity и shared runtime, не APP-05 PASS

Подробнее `APP_06_EXECUTION.md` и опубликованный source contract
`docs/implementation/APP06_OFFICIAL_HACKRF_STAGING_CONTRACT.md`.
Official HackRF private factory protocol2 проверяет serial на **том же opened
handle** до RF configuration/start; intentionally wrong expectation физически
отклонён at open, потом fresh permit запускает20MS/s. No TX/amp/bias/firmware.

Full UI V2 source gate exact b8fffbb с staged official C7f303… в main process:
**894 tests /66 skipped /0 failures,289.246s**, compiled deferred[], product
modules outside checkout[]. Subprocess tests используют их штатную canonical
active CPU installation; не приписывать им автоматически staged module injection.

Проблема общей упаковки воспроизведена: local IIO libusb1.0.26 не содержит
`libusb_endpoint_set_raw_io` и `libusb_endpoint_supports_raw_io`, required
by exact HackRF SDK dll1.0.30. Старый first-loaded libusb вызывает ImportError
official extension; admitted new DLL позволяет import/CPU self-test. Source
`886820d` (published) добавляет pre-freeze static guard: full libiio7-file
presence/hash и identical chosen libusb SHA с admitted HackRF manifest;
никаких silent overwrite/system DLL changes.17 packaging tests PASS.

Source `aec7a85` (published) добавляет bounded one-process compatibility
probe. `app06_mixed_runtime_rx_01*` **FAIL** сохранён: stale USB URI3.4.5,
selection error/timeout, Pluto RX не стартовал, next HackRF phase не запущен.
Read-only discovery с новой DLL вернул URI2.4.5; transport route не является
вечным physical identity, serial в scan description пуст. Это отдельная
APP-06A catalog/identity задача, не доказанный stable physical key Pluto.

Новый explicit run `app06_mixed_runtime_rx_02*` exact clean aec7a85 **PASS**:
HackRF15s≈19.9894MS/s → Pluto UI V2 appliedFs61.44/RF56/CPU/FFT4096/Visual/
HideShowStop → HackRF15s≈19.9898MS/s. HackRF≈9760.4/9760.6 analyticalFFT/s,
source ID/generation1 vs3 verified, invalid/drop flags/software drops0,
Stop complete/joined/callbacks/slots/ready0/lifecycle closed. Pluto ingress
≈7.53945MS/s; all18 HideShow/Stop checks PASS, workers[]/reserved0.
Before/after first-name loaded libusb witness points to explicit shared
candidate, file SHA59823a17…; witness is not duplicate-basename exclusion or
in-memory binary attestation. Candidate contains six original IIO DLLs and
one exact SDK libusb in ignored evidence directory; system/active DLLs untouched.

Observed Pluto period readout: `Новый кадр Qt: ≈53 мс · Буфер → ПК: ≈36 мс ·
Радионаблюдение: нет оценки`. This distinguishes GUI and host ingress without
inventing RF duty/Pd. Short source-process RX compatibility is **not common
Analyzer HackRF selection**, packaged closure, long stability or final release.
APP-06A/C/D and remaining APP-06B package activation remain OPEN.

## Current CPU diagnostic package — aec7a85, 2026-09-27

Full clean exact-source CPU onedir pipeline **PASS**: PyInstaller6.22.3,
34/34 native CTest (42.47s), immutable source before/after native and freeze,
357-file manifest and frozen/default-UI-V2/native/libiio/tinySA runtime gates.
Source SHA256 `7342ded29ac47d4b4c98f8ee13c2f9ada964072278af6e6fbf2d6bdfd8c73552`;
rebuilt active CPU module SHA256
`b218486cffffbc451018e515daf0d049988d7b9dc139340f4c48d91383c8ad48`;
EXE SHA256 `638e556a327683cd03829c02070f1028cbc06e29e81b9ee38c8654fdfa9492a9`.
The active CPU artifact changed through this reproducible rebuild; earlier
unchanged-module statements apply only to their earlier checkpoints.
Official HackRF factory remains **OFF in this CPU package**. The staged C7
official module and mixed-runtime source tests are not its capabilities.
Provenance is pipeline-bound, not binary-attested/signed release acceptance.

The earlier exact2f9bd8b diagnostic package was recoverably moved into
`C:\Users\posta\.codex\worktrees\app05-exe-head-3128cc4\SDR Native Monitoring\dist\archive\SDRNativeMonitoring-CPU-2F9BD8B-before-AEC7A85-20260927`.
Current package was copied and its 357-file manifest reverified at the same
diagnostic path:
`C:\Users\posta\.codex\worktrees\app05-exe-head-3128cc4\SDR Native Monitoring\dist\SDRNativeMonitoring-CPU-APP05-HEAD-3128cc4-20260924\SDRNativeMonitoring\SDRNativeMonitoring.exe`.
The directory name is historical; its source is now **aec7a85**, not3128cc4.
The separate stable APP-00 installation and firewall rules are unchanged.

Computer Use @oai/sky launched this exact EXE at ~08:13MSK. Fresh window
inventory and UIA/screenshot showed UI V2 and no separate CMD window. Windows
nevertheless displayed a new network-permission prompt at this reused path.
No action on the security dialog and no firewall/security-rule changes.
Only the path-verified diagnostic PID3928 was stopped. No RX or physical
axis/period acceptance on this current frozen EXE; existing source-process
RX evidence retains its separate scope. Reusing a path is **not a guarantee**
that Windows will never ask again. The four-hour checkpoint stays ~08:44MSK.

## Final source gates and bounded APP-06A correction

Full UI V2 gate after currentCPU B218 rebuild, sourceaec7a85 product:
**894 tests /66 skipped /0 failures,290.468s**, compiled deferred[], product
modules outside checkout[]. A new non-UI test file was written during the
tail of this run; no product source changed until it finished. Do not label
the whole working tree immutable across that interval. The official staged
C7 full gate above remains a separate run, not this baselineCPU module.

Published `e5ef91d` fixes the existing AD936x read-only capability adapter:
disconnect failure cannot publish valid-looking capability evidence; the
owner remains reachable, another observe is rejected without reopening, and
only explicit successful close unblocks the instance. Single-caller low-rate
ownership, not a global device arbiter or a new common UI route. Two old
behavior reproductions FAIL before /PASS after;8 new tests plus15 related
domain/HackRF/tinySA mapping tests PASS. New-test Ruff and scoped mypy PASS;
historical product Ruff11 baseline/9 current/0 new, not blanket lint PASS.
Published `APP06_AD936X_CAPABILITY_OWNERSHIP_CONTRACT.md` specifies the scope.
Current EXE remains exactaec7a85; this later source correction is not frozen
into it. No native DSP or display cadence change in e5ef91d.

Physical read-only check on e5ef91d with nativeB218 rejected the AD936x
observation. Direct native diagnostics establish the reason: both probe and
capability serial are empty; firmware exists, tuning70MHz–6GHz, sample-rate
range2.083333–61.44MS/s, analogBW0.2–56MHz, gain−3..71dB. After explicit
disconnect, connected=false. No RX/configuration. Missing serial already
failed the previous mapper; not an introduced cleanup regression. Do not
turn a USB route fallback into proven stable physical identity. Note JSON
`app06_ad936x_capability_readonly_01.json` records this failure and the earlier
diagnostic formatting error separately. APP-06A stable identity remains OPEN.

Full UI V2 repeated on clean published e5ef91d product source: **894 tests /
66 skipped /0 failures,290.466s**, compiled deferred[], modules outside[].
Native is rebuilt baselineB218/sourceaec7a85, not officialC7. No isolated
product edits or heavier tests overlapped the next physical campaign.

### Rebuilt baseline CPU: physical max-Fs FFT-size observations

Two sequential45s source-process Windows Qt runs, same e5ef91d runner SHA
`1c973d72ffb04910173b815ef5f60e505eb45269dacb8edd22a7df9cf8e32c9e`,
same nativeB218 and actual1400×850/DPR1.75. Both appliedFs61.44MS/s/RF56MHz/
CPU/Visual/default15Hz/buffer262144 with diagnostic vertical lock and one
Hide/Show. Both JSONs show tracked_dirty[], lifecycle18/18 PASS,
workers_after_close[], allocation reserved0, no observed software drop counters.
Separate files `current_b218_cpu_maxfs_fft{4096,16384}_visual_01.json/.png`.

| FFT | Delivered I/Q mean | Analytical FFT/s | Native reduced publications | Unique Spectrum Qt paints | Paint-gap p95 | GUI-model→paint p95 |
| --- | --- | --- | --- | --- | --- | --- |
|4096|7.53840MS/s|3678.01|2586|813|82.70ms|36.15ms|
|16384|7.51255MS/s|916.34|1288|375|158.06ms|46.80ms|

Unique counts are over44.9983s /44.9787s; Hide includes about1s intentionally
without analyzer paints. These are single sequential observations, not a
matched order-reversed speed promotion or causal attribution. Native scalar
rates printed at Stop are instantaneous and differ; the table uses deltas
over the measured intervals. Qt counts are not DWM FPS, FFT LPS or a count
of observed RF pulses. Density-preparation p95 grows50.11→106.11ms;
shared optional Visual work remains a performance concern, not accepted50ms.

Period labels respectively: `Новый кадр Qt: ≈61 мс · Буфер → ПК: ≈31 мс ·
Радионаблюдение: нет оценки` and `≈116 мс · ≈36 мс · нет оценки`.
They are pre-Stop rolling readouts, not reciprocals of the entire run's mean.
The range/ADC Fs is not raw transport rate, and no RF duty/Pd is manufactured
from the ~12.2% delivered/applied ratio. Missing native serial means this
campaign does not admit a stable physical identity/common family matrix.
Source-process passes do not unblock the current frozen EXE security prompt.

### Rebuilt CPU: final five-minute all-layer resource/control check

`current_b218_visual_memory_churn_300s_01.json/.png`: clean exacte5ef91d,
canonical nativeB218 injected only into the observer process, Visual kernel
available, runner SHAa8616fa3…, WindowsQt1400×850/DPR1.75. Synthetic65K×64,
offered200 source/s, fresh density every10, default15Hz, combined worker;
no alternate scheduler, no radio.300.138s,50424 generated publications,
25 page changes/42 viewport changes. GUI heartbeat p99/max31.34/43.62ms,
Stop while preparation running:click0.516ms, intent→idle25.48ms. Closure
workers[], modules outside[], reservation0, rejections0. Exit0 confirms this
bounded harness's checks, not moving-latest/50ms or physical timing acceptance.

Private bytes before Start649379840, after normal context return655863808:
delta6.18MiB. After normal return, composition/presenter/scene/waterfall weak
owners still alive and observed ledger17.56MB; after explicit **diagnostic GC**
all those weak roots false, observed/reserved0, private635744256. GC is not
new product behavior or a leak fix. Do not claim complete normal-release
ownership closure or absence of a long-term leak from this five-minute run.
Long unforced-GC lifecycle/steady memory acceptance remains OPEN.

## Timebox handoff decision

This is the existing four-hour active-work iteration, not a restarted timer.
After the bounded final gates, retain published e5ef91d product state and the
provenance-checked aec7a85 CPU diagnostic package. Keep CPU/default262144/
default15Hz; do not promote rejected cadence/split-lane/COUNT/witness/CUDA
experiments, hide layers, lower applied Fs to manufacture PASS, or add GC as
a product workaround. Legacy/DFL changes preserved; radios/test EXE stopped.
No subagents were used in this iteration; no invented model identity claim.

APP-05 **engineering checkpoint recorded**, not full performance PASS.
Open debts retained: Visual contention/freshness and strict moving-latest;
full qualified ready→paint/unique cadence/Waterfall LPS; long unforced memory
closure; populated65% area and exact-QHD/complete monitor scaling; stable
physical identity/RJ45 and family cells; RF-qualified capture windows/Pd;
current frozen EXE manual network permission and hardware acceptance.
APP-07 and final release remain OPEN; the overall APP-00…14 goal stays active.

Next implementation order in APP_06_EXECUTION.md:

1. **APP-06A**: genuine physical identity and alias admission, before-Start
   revalidation, shared capability/request catalog without a second truth model.
   Existing Pluto ContextProbe is not a same-opened-handle identity proof;
   currently empty serial is an explicit negative case, not a stable URI key.
2. **APP-06B**: finish official HackRF shared DLL/frozen package closure;
   baselineCPU stays usable, selected new libusb must pass both-device tests.
   The staged source-process PASS does not authorize a Supported EXE claim.
3. **APP-06C**: one Analyzer owner/router and normal UI V2 source selection;
   no stale cross-source/session/epoch/unit frame or unresolved Stop owner.
   HackRF LNA/VGA/amp/bias and tinySA dBm/trace settings stay family-specific.
4. **APP-06D**: high-load physical common UI/device matrix, including only
   actually reachable Ethernet and tinySA variants; repeat exact-EXE gates.

Detailed raw evidence remains local. This aggregate report, APP_05_EXECUTION
and APP_06_EXECUTION are explicitly copied into the isolated source checkout
and committed/pushed; earlier statements about ignored local reports do not
mean this explicitly published final selection is absent from Git. Raw JSONs,
PNGs, main AGENTS history and unrelated dirty files are not part of that push.
