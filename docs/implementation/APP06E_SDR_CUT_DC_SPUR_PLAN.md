# APP-06E — Cut DC и spur-фильтр для SDR: полный план реализации

Дата: 2026-09-30. Статус ветки: PLANNED / OPEN.
Основание: прямой запрос владельца добавить полноценную ветку реализации Cut DC и spur-фильтра для SDR.

Это спецификация будущей реализации, а не отчёт о готовых фильтрах.
Ветка входит в действующую цель APP-00…APP-14. Разработка интерфейса — только UI V2,
в общем Анализаторе RTBW/Sweep. Старый Legacy UI не является целевым продуктом.
Исторические APP-06A/B/C/D и APP-07 не переименовываются и не объявляются завершёнными.
Истёкший четырёхчасовой таймер APP-05 не перезапускается.

## 1. Что получит пользователь

Два понятных средства очистки спектра с расширенными настройками:

- Cut DC — реальная обработка комплексных I/Q, уменьшающая постоянное смещение
  в центре принимаемого окна. Пользователь видит, где она может повлиять на сигнал.
- Spur filter — обнаружение внутренних паразитных составляющих и применение
  проверенной стратегии подавления. Неопределённые кандидаты остаются видимыми,
  а удалённые или искажённые участки не выдаются за достоверно измеренный фон.

Режимы работают в одной вкладке Анализатора. Spectrum, Waterfall, persistence,
holds, measurements, recording и Replay получают согласованный контекст обработки.
Настройки объясняют эффект, ограничения и цену по времени сканирования.

Полноценность означает рабочий путь от native DSP и ownership до измерений,
UI V2, файлов, Replay и аппаратной квалификации. Две декоративные галочки,
простой косметический разрыв кривой или замена пиков шумовой полкой не закрывают ветку.

## 2. Проверенная исходная точка и границы

Инвентаризация относится к продуктовому source 0df150cd11bb320c37f93c5e123d80e1927e0089:

- В native DSP есть DcRemovalMode Off/BlockMean. CPU реализация вычитает среднее
  комплексного FFT-блока перед оконной функцией.
- В Pluto конфигурации dc_removal_block_mean по умолчанию false.
  HackRF live factory и host Sweep analysis используют Off.
- Наличие низкоуровневой опции не означает законченного включаемого Cut DC в UI V2.
- Общего host spur-фильтра для AD936x/HackRF в продукте пока нет.
- У tinySA есть отдельная настройка встроенного firmware spur removal.
  ACK команды не доказывает обратное чтение этого состояния; это не SDR host DSP.

Основные точки поиска для агента, а не запрет архитектурных изменений:

- native/sdr_core/include/sdr_core/dsp_backend.hpp;
- native/sdr_core/src/dsp/cpu_dsp_backend.cpp;
- native/sdr_core/src/cuda/cuda_dsp_backend.cpp;
- native/sdr_core/src/pluto/fixed_band_engine.cpp и continuous_sweep_coordinator.cpp;
- native/sdr_core/src/hackrf/hackrf_live_factory.cpp и hackrf_sweep_analysis.cpp;
- sdr_monitor/services, общий source/RX owner и resource-scoped APP-07 graph;
- sdr_monitor/ui/v2, включая общий Analyzer, Spectrum, Waterfall и persistence;
- существующие native bindings, manifests, calibration/recording/Replay contracts.

Охват: AD9361/AD9363/AD9364, включая перепрошитые AD9363, и HackRF по реально
доступным capabilities. USB/IP/genuine Ethernet проверяются раздельно.
Новая модель SDR не получает поддержку по одному имени чипа: нужны формат I/Q,
нормировка, capabilities, ownership и квалификация.
tinySA не получает фиктивный Cut DC по I/Q, которых она не передаёт.

## 3. Разделить три разных операции

| Операция | Где действует | Что можно утверждать |
| --- | --- | --- |
| Native DC removal | Комплексные I/Q до window/FFT | Постоянная составляющая уменьшена выбранным алгоритмом; сигнал около DC также может быть изменён |
| Маска/исключение bins | Аналитическая validity/coverage и отображение | Участок исключён из измерений; отсутствующие значения не восстановлены |
| Подавление подтверждённого spur | Native коррекция/notch либо реально повторно измеренная Sweep coverage | Применён конкретный проверенный метод; его затронутая полоса и ограничения известны |

Недопустимо называть скрытие центрального bin реальным удалением DC.
Недопустимо называть интерполяцию измеренным спектром.
Недопустимо считать любой узкий, сильный или неподвижный сигнал внутренней помехой.

DC removal способен повлиять на полезный сигнал около нулевой частоты.
Это отмечают и разработчики HackRF; offset tuning — один из способов вынести
интересующий сигнал из области DC. Это обоснование ограничения, а не доказательство
реализации такого режима в нашем приложении.
[Официальная документация HackRF](https://hackrf.readthedocs.io/en/latest/troubleshooting.html).

Встроенные RF/baseband DC tracking механизмы AD936x отличаются от нашего host DSP;
их влияние на околонулевые сигналы также требует учёта. Не считать наличие чипа
доказательством конкретного включённого состояния tracking.
[Ответ Analog Devices о DC и полезном сигнале](https://ez.analog.com/rf/wide-band-rf-transceivers/design-support/f/q-a/543201/about-ad936x_dcoffset_issue/412370).

Из одного power spectrum в общем случае нельзя достоверно отличить внутренний spur
от настоящего RF-сигнала на той же частоте. Следствие для дизайна: недостаточное
основание даёт статус «кандидат», а не разрешение автоматически стирать пик.

## 4. Общие контракты и pipeline

### 4.1. Единственная модель политики

Версионированная immutable политика обработки содержит:

- source/RX и область applicability; algorithm/version и digest политики;
- DC mode, параметры и reset policy;
- spur mode, profile ID/revision, диапазон применимости и метод подтверждения;
- затронутые интервалы в Hz и bins, признаки modified/excluded/unknown;
- requested/applied/unsupported/unverified состояния по доступному readback;
- processing revision, config generation, source epoch, grid, unit и value context.

Имена будущих типов определяются в первом пакете: не создавать отдельные
несогласованные truth models в UI, Python service и C++.
Не перегружать существующий quality bitmask новыми неоднозначными значениями.
Если требуется новый schema/ABI contract — versioned изменение с тестами.
Неизвестные flags сохраняются; loss metadata не скрывается фильтром.

По умолчанию обе host обработки OFF. Это безопасная стартовая политика этой ветки;
последующее изменение дефолта требует отдельного решения и повторной квалификации.
Состояние аппаратной коррекции, когда его нельзя прочесть, отображается UNKNOWN,
а не автоматически OFF.

### 4.2. Положение в обработке

Минимальная схема:

I/Q ingress одного RX
    → исходный capture tap и метаданные
    → native DC / подтверждённая I/Q spur-коррекция
    → window / FFT / detector
    → частотная привязка, validity и Sweep stitching
    → аналитические measurements / накопление
    → bounded presentation queues
    → общий Spectrum / Waterfall / persistence в UI V2

Данные для аналитических вычислений и native persistence должны получать одну
согласованную processing revision до lossy presentation queue.
Специальная presentation-only маска допустима как отдельный view control,
но не должна менять аналитический результат без явной политики.

Не переносить массовые I/Q в Python ради этих фильтров.
Не добавлять неограниченную очередь, per-frame перестройку FFT plan или
allocation большого рабочего буфера. Состояние и scratch storage bounded на RX.
CPU путь обязателен; CUDA поддержка квалифицируется только там, где она реально
скомпилирована и заявлена. Отсутствие support не заменяется скрытым semantic fallback.

Сравнение «до/после» по одному принятому I/Q-блоку — bounded opt-in analytical
ветка или offline reprocess. Её цена учитывается отдельно. Разные RF epochs
нельзя показывать как одновременные парные измерения.

### 4.3. Изменения настройки и несколько панелей

- Draft, раскрытие drawer, смена языка/темы не вызывают SDK/retune/restart.
- В первой реализации acquisition/DSP policy применяется через явные
  Stop → Stage/Apply → Start. Скрытый restart запрещён.
- RF epoch меняется при фактическом перезапуске/перестройке; изменение только
  view mask меняет value context, но не создаёт выдуманную RF epoch.
- Один RX, отданный нескольким панелям, имеет одну общую acquisition/DSP policy.
  Перед применением показываются все затронутые панели.
- Нельзя незаметно изменять общий Cut DC только потому, что выбрана другая панель.
  Локальное скрытие кандидатов в одной view остаётся presentation-only.
- Независимые RX имеют независимые политики и filter state. Общий LO/sample clock
  нескольких RX одного прибора учитывается tuning-group capabilities.
- Смена политики сбрасывает либо раздельно хранит holds/average/persistence;
  старые и новые обработанные значения не накапливаются в одной статистике.
  История Waterfall может сохраняться только с явной границей контекста.

## 5. Cut DC: целевая реализация

Обязательный первый алгоритм — существующий native BlockMean, доведённый до
production конфигурации AD936x/HackRF для RTBW и Sweep:

1. Вычитать комплексное среднее до window/FFT, сохранив существующие
   normalization, detector, hop и precision contracts.
2. Не путать среднее I/Q с усреднением spectrum frames.
3. Документировать частотный эффект для Fs/FFT/window: DC и околонулевые сигналы
   получают ограничение, не обещать идеальную однобиновую операцию.
4. Публиковать факт обработки и затронутую область; применять существующий
   DcRemoved flag только по его фактической семантике.
5. RTBW, dual RX и Sweep используют одну проверенную DSP семантику, а не
   разные Python replacements.
6. Доказать OFF parity с baseline. По умолчанию обработка не меняет данные.

Stateful DC blocker/tracker — отдельный расширенный режим, а не обязательная
замена BlockMean без доказанного выигрыша. Он имеет коэффициенты/полосу в Hz,
ограниченное состояние, transient/warm-up описание и reset при source/epoch/
LO/Fs/gain change, discontinuity и потере непрерывности.
Для burst/буферного захвата отсутствие принятого I/Q не трактуется как нулевой вход.
Если такой режим не включён в первый supported scope, он честно остаётся отдельно
PLANNED и не маскирует неполноту обязательного BlockMean пути.

Аппаратные DC/IQ tracking настройки AD936x не переключаются скрыто.
Они могут быть future capability-driven опцией с отдельными admission/readback
контрактами, но не условием выполнения этой host DSP ветки.

## 6. Spur filter: целевая реализация

### 6.1. Профили и обнаружение

Хранить bounded versioned profiles внутренних паразитных составляющих.
Применимость проверяется по устройству/RX, firmware observation, Fs, RF BW,
gain/configuration и частотному диапазону; не распространять профиль одного
приёмника на все «Pluto» или все HackRF.

Различать RF frequency и baseband offset относительно actual LO.
Оценка кандидата может учитывать повторяемость в baseband, известный device
profile, проверку при разных LO и соседнюю независимую coverage.
Сила/ширина/стационарность сами по себе недостаточны.
Score — оценка алгоритма, не калиброванная вероятность без отдельной проверки.

Unknown/mismatched/устаревший профиль не применяется молча.
Повреждённый профиль, чрезмерное число зон и out-of-range параметры дают
bounded отказ до RX; исходный спектр остаётся доступным.

### 6.2. RTBW

Обязательны OFF, «показать кандидатов» и реально действующий режим подавления
подтверждённых профильных паразитных составляющих:

- Native узкополосная I/Q correction либо цифровой notch с определённой АЧХ,
  устойчивостью и bounds; выбор конкретного метода фиксируется численным review.
- Область, в которой filter способен ослабить настоящий RF-сигнал,
  отмечается как modified/measurement-limited.
- Отдельная кнопка исключения bins остаётся маской, не подменяет native filter.
- Коэффициенты не переоцениваются из любого сильного live пика.
  Настоящий тон или короткий burst не становится spur лишь за повторяемость.
- Сравнение raw/processed и прозрачное отключение обязательно.
- RTBW не превращается в Sweep серией скрытых перестроек для классификации.
  LO-offset диагностика запускается как отдельный явный measurement plan.
- Offset tuning / digital frequency shift допустимы только с точной
  RF-привязкой, actual LO, usable coverage, ограничением полосы и metadata.

Если подтверждённый spur совпал с полезным RF-сигналом, notch не «восстанавливает»
этот сигнал. UI и measurements обязаны сообщить ограничение.

### 6.3. Sweep

Применять ту же DC/spur политику в каждом реальном приёмном окне,
до окончательного stitching. Центр текущего сегмента не равен центру всей
составной панорамы: нельзя вырезать только середину готового Sweep.

Разработать отдельную стратегию clean coverage:

1. При известной проблемной baseband зоне использовать пригодные данные
   другого реально измеренного LO/window, если они действительно покрывают
   ту же RF-частоту и имеют согласованные units/configuration.
2. Обычное небольшое перекрытие соседних Sweep окон не обязано покрывать
   центральную DC-зону. При необходимости добавить явно объявленное
   ограниченное дополнительное offset окно/visit.
3. При подтверждении по LO shift учитывать несовпадение времени измерений:
   исчезнувший burst или изменившийся эфир не является доказательством spur.
4. Выбор/взвешивание пригодной coverage выполняется в power domain с
   documented normalization; не выбирать «минимум всех сканов» ради красивого фона.
5. При отсутствии подходящей coverage оставить explicit limited/excluded/unknown
   интервал. Не заполнять его интерполированным «измерением».
6. Дополнительные окна учитываются scheduler, scan period, RF gaps, revisit,
   LPS и overload budgets. Невыполнимый план отклоняется до запуска.
7. Progressive Spectrum обновляется по готовности сегментов. Дополнительная
   обработка не возвращает поведение «обновить всё лишь в конце прохода».
8. Не менять скрыто максимальную Fs, usable 36-MHz AD936x policy,
   FFT/hop/detector/group, settling/discard или требования к readback ради скорости.

Измерять baseline OFF, простую native обработку и coverage repair раздельно.
Фильтрация с дополнительными RF visits не обещает бесплатного ускорения.

## 7. UI V2 и измерительная честность

### 7.1. Схема управления

В существующем drawer источника общего Анализатора разместить секцию
«Очистка спектра». Быстрые controls не создают новую вкладку/второй renderer.

    Очистка спектра                  [свернуть]
    Cut DC            [OFF / BlockMean]      [настройки]
    Spur filter       [OFF / Кандидаты / Подтверждённый профиль]
    Профиль           [имя, revision, совместимость]
    Область эффекта   DC: … Hz; spur: … интервалов
    Область действия  RX… → панели …
    Сравнение         [Исходный / Обработанный / Оба]
    Состояние         Draft / Applied / Unsupported / Unverified
    Apply             явное действие; не скрытый Start

«Оба» доступно только при наличии действительно сопоставимых данных;
иначе показать причину, а не синтетическую raw trace.
Во время Live draft не изменяет applied policy. Требуемое Stop видно заранее.
На графике — компактный индикатор applied DC/spur policy и затронутых областей;
tooltip раскрывает алгоритм, профиль и ограничения. Нельзя мерцать длинными
строками состояния или перекрывать loss/quality readouts.

RU/EN, Dark/Light/HC, клавиатура, accessibility, toolbar/drawer overflow,
FHD/QHD и применимые DPI 100/150/200/300% входят в проверки UI V2.
Открытие секции не должно отнимать постоянную большую часть графиков.

### 7.2. Measurements, запись и Replay

- Markers/peak search/power/OBW знают, какие данные raw, processed или excluded.
  Excluded bins не интегрируются как нулевой сигнал; affected-result имеет статус.
- DC/spur фильтр не преобразует dBFS/bin в dBm и не создаёт RF calibration.
  Порядок calibration и nonlinear filtering фиксируется, проверяется и
  входит в applicability/value context.
- Holds/average/persistence не смешивают разные политики и raw/processed.
- Spectrum recording сохраняет policy/schema/digest, validity и affected zones.
  CSV/export содержит достаточную информацию об обработке и units.
- Native I/Q recording по умолчанию сохраняет принятые I/Q до host фильтрации,
  а не обработанные данные под видом raw. Предшествующую аппаратную коррекцию
  отменить таким tap нельзя; известное/неизвестное её состояние сохраняется.
- Ранее действующие RTBW-only I/Q ограничения и честные transport loss/rate
  остаются. Новые фильтры не повышают приоритет записи над live спектром.
- Replay воспроизводит сохранённый processed spectrum без повторного фильтра.
  Повторная обработка исходного I/Q — новый versioned результат, не перепись файла.
- Старые файлы/настройки мигрируют в host filters OFF/UNKNOWN, а не получают
  задним числом утверждение «DC/spur подавлены».

## 8. Пакеты реализации и критерии готовности

Все пакеты ниже OPEN. Назначенный исполнитель после каждого пакета фиксирует
source/hash, тесты, незакрытые клетки и следующий ограниченный шаг.

| Пакет | Работа и артефакт | Приёмка пакета |
| --- | --- | --- |
| DCSP-01 | Inventory RTBW/Sweep/dual-RX paths; canonical policy, validity/schema/ABI, capability matrix и diagram | Нет второй truth model; OFF compatibility; hardware/readback и host DSP разделены; план тестов известен до кода |
| DCSP-02 | Production wiring native BlockMean в AD936x/HackRF, CPU numerical regression и actual applied metadata | OFF parity; complex DC suppression; проверены window/hop/group/precision, near-DC effect и reset; никакой Python I/Q копии |
| DCSP-03 | Bounded spur profiles, applicability, детектор кандидатов и набор golden true-signal/false-spur fixtures | Незнакомый или несовместимый профиль не фильтрует; сильный неподвижный RF тон и burst не удаляются автоматически |
| DCSP-04 | Native RTBW suppression подтверждённых зон; АЧХ/устойчивость/affected coverage; raw/processed comparison | Подавление численно измерено; полезные сигналы вне declared affected band сохраняются в установленной tolerance; внутри зоны ограничения честны |
| DCSP-05 | Общая обработка Sweep сегментов; LO-offset clean coverage/stitching и bounded дополнительный план | Progressive updates сохранены; центры каждого окна учтены; нет синтетических измеренных bins, межэпоховой смеси или скрытых visits |
| DCSP-06 | UI V2 controls, applied/draft states, shared-RX impact preview, local masks и immutable policy change lifecycle | Общий RX не открыт повторно; peers не перезапущены скрыто; Stop/error/clear и RU/EN/темы/клавиатура проверены |
| DCSP-07 | Measurement/calibration/holds/persistence value context и limited-coverage результаты | Power/OBW/markers не трактуют исключённую область как измеренный ноль; units и uncertainty корректны |
| DCSP-08 | Spectrum/IQ/export schemas, миграции и Replay/reprocess | Старые файлы читаются; raw I/Q не изменён; policy round-trip; обработка не применяется повторно к processed spectrum |
| DCSP-09 | High-Fs physical/fake/numerical/performance и multipane матрица, OFF/ON comparison | Caps/retained memory/Stop и signal preservation; настоящие транспортные ограничения отделены от накладных расходов фильтра |
| DCSP-10 | Exact-source frozen EXE, видимые сценарии, документация и независимый review; APP-12/13/14 integration | Тестируется тот же source/package; нет открытых release blockers; непроверенная RF accuracy остаётся NOT_VERIFIED |

Основной порядок: DCSP-01 → DCSP-02/03 → DCSP-04/05 → DCSP-06/07/08
→ DCSP-09 → DCSP-10. UI и numerical fixtures могут готовиться раньше,
но их завершение не заменяет реальные native data paths.
Stateful DC extension планируется отдельно после DCSP-02 и не используется
как бесконечная доводка, блокирующая остальные обязательные пакеты.

Привязка к roadmap:

- APP-06E / DCSP-01…05 — capabilities, native алгоритмы и source wiring;
- APP-07 / DCSP-06 — ресурсы/RX, shared subscriptions и time-sliced ownership;
- APP-08 / DCSP-07 — измерения и calibration semantics;
- APP-09…10 / DCSP-08 — запись, export и Replay;
- APP-11 — видимая UX/DPI/accessibility доводка controls из DCSP-06;
- APP-12 / DCSP-09 — сквозная численная, аппаратная и нагрузочная квалификация;
- APP-13…14 / DCSP-10 — exact frozen поставка, review и финальная приёмка.

Текущий APP-07 можно продолжать, пока native основание этой ветки готовится.
Это не разрешение объявить APP-06E или весь APP-06 полностью завершённым до
готовности обязательных частей. Новый release scope не сужается самим агентом.
Основание для дальнейшей интеграции — DCSP-01…06. Сквозные DCSP-07…10
проверяются в соответствующих APP-08…14: они не являются циклическим
предусловием старта APP-07/08. «Основание готово» и «вся ветка принята»
имеют разные статусы; обязательные поздние gates остаются открытыми.

## 9. Матрица тестов

### 9.1. Численные и отрицательные сценарии

Обязательные fixtures: complex DC; no DC; off-bin/on-bin tone; настоящий
тон в DC и рядом; шум; слабый сигнал рядом с сильным; несколько tones;
широкополосный сигнал через центр; импульсы/bursts; изменяющийся эфир;
IQ image; known baseband spur; настоящий RF-сигнал, совпавший со spur;
gaps, incomplete detector group, source/LO/gain/Fs/policy changes.

Проверять Fs/FFT/window/hop/group/precision combinations по supported paths;
near-DC ширина и numerical tolerance фиксируются до объявления теста PASS.
CPU обязателен; CUDA parity обязательна для заявленной CUDA cell.
NaN/Inf/out-of-range profile и excessive zones не запускают RX.
Тесты unknown profile, old native ABI, malformed metadata, epoch/grid/unit
mismatch, ownership failure и terminal cleanup не ослабляются ради красивого UI.

### 9.2. Аппаратные и высоконагруженные профили

- AD9364 USB и перепрошитый AD9363 genuine Ethernet: максимальная доказанно
  применённая Fs, ориентир 61.44 MS/s; отдельные transport cells.
- AD9361/доступные dual RX — по фактической capabilities matrix, без выдуманного
  второго RX у одноканального устройства.
- HackRF: номинальные 20 MS/s и ~80% профиль 16 MS/s; actual/admitted/requested
  rates не объединяются в одну цифру и не копируют AD936x normalization.
- FFT 1024/4096/16384 и тяжёлые поддерживаемые N; host Sweep использует только
  реально admitted размеры, не переносит RTBW предел автоматически.
- RTBW/Sweep; OFF/ON/кандидаты/подтверждённый профиль; разные gain stages,
  span/overlap; spectrum+waterfall+density; 1 pane, independent 2×2,
  shared RX и поддерживаемые несколько RX одного прибора.

USB2.0/1-Gbit Ethernet не объявляются способными передавать непрерывный raw
61.44-MS/s поток только из-за requested sample clock.
Нельзя снизить Fs, FFT, detector density или отключить Waterfall/Visual
незаметно, чтобы пройти тест с фильтром.

### 9.3. Производительность и устойчивость

Matched OFF/ON/по-возможности ABBA профили: native filter/FFT/stitching time,
host/bridge time, ready→paint p50/p95/p99/max, Qt cadence, source age,
analytical FFT/s, native publications/s, complete Sweep LPS, partial updates,
scan period, gaps, loss/supersession и retained memory — раздельно.

Бюджеты и numerical tolerances задаются в DCSP-01/09 на основании измеренного
baseline и действующих APP-05/12 contracts. Этот документ не вводит новый
непроверенный универсальный SLA или автоматическое исключение устройства.
Дополнительные LO visits имеют измеримую цену; её не прятать средним FPS.
Потери I/Q и presentation coalescing не переименовывать друг в друга.
Filter confidence не заменяет RF duty, вероятность обнаружения импульса
или measured Pd; неизвестность этих величин остаётся явной.

Финальные soak durations и exact EXE gates наследуются из APP-12/13/14.
Короткий успешный smoke не закрывает sustained qualification.

Без внешнего CW генератора можно доказать software/numerical correctness,
ownership, видимую работу и bounded latency/cleanup. Нельзя объявить по этому
абсолютную RF accuracy и проверенную физическую false-positive rate.
Такие доказательства остаются отдельными NOT_VERIFIED клетками до подходящего
reference signal setup; отсутствие генератора не останавливает все пакеты.

## 10. Финальный DoD ветки

Ветка завершена только когда:

1. Cut DC реально проходит native pipeline заявленных AD936x/HackRF modes,
   а host spur suppression работает, а не только скрывает линию.
2. Есть OFF, truthful applied metadata, сравнение и прозрачное отключение;
   настоящие сигналы защищены в пределах объявленных numerical/affected-band limits.
3. Sweep остаётся progressive; clean coverage реально измерена, gaps честны.
4. Shared/independent RX не конфликтуют, state не протекает между epochs,
   режимами, профилями или устройствами; Stop/error/close bounded.
5. Измерения, calibration, history/persistence, запись/export и Replay
   согласованы с обработкой и declared validity.
6. Высоконагруженные source/transport/mode cells квалифицированы на точном
   native/package. Все непроверенные обязательные клетки перечислены.
7. Есть документация, numerical/physical/performance evidence и независимый
   review без открытых release-blocking findings.

Не выполнять TX, RF amp/bias enable, firmware/driver modification,
firewall/security/network rule mutation ради этой ветки.
Не считать этот документ реализацией и не завершать по нему всю цель APP-00…APP-14.
