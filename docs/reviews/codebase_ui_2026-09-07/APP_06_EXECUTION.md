# APP-06 — общий UI V2 для Pluto, HackRF и tinySA

## Текущий coherent catalog checkpoint — 2026-09-27

Читайте [APP_06A_COHERENT_CATALOG_CHECKPOINT_2026-09-27.md](APP_06A_COHERENT_CATALOG_CHECKPOINT_2026-09-27.md).
Product b7446bf / final tested9a8a5c1 (fixture-only): owned topology + shared
retained read-only transaction; NativeLive discovery/selection uses those
same facts, existing DeviceCapabilitySnapshot/Inventory without second SDK
probe or truth model. Unknown serial retains operational route, not stable
catalog evidence. Invalid observation protocol/conflicting alias facts fail
stable admission; pending close blocks new RX/discovery until explicit Stop.

Clean native34/34CTest40.28s, compiled2 tests/3owner subtests,109 combined
tests, final fullV2 gate894/66skip/0fail249.723s PASS. First full gate2 errors
were an incomplete terminal test fixture, corrected without weakening cleanup.
Native staged383085… manifestb7446bf; canonicalB218/EXEaec7a85 unchanged.
Physical USB coherent observation + Fs61.44/RF56/FFT4096 RTBW Stop/close PASS;
serial empty, admitted stable catalog0. Not desktop/LPS/frozen acceptance.

NEXT: retained cleanup of other family providers (fake HackRF close-failure
repro loses refs), common runtime/capability registry and explicit canonical
identity join, official shared-runtime frozen package, one UI V2 owner/router,
high-Fs matrix. AD936x service catalog ≠ completed common Analyzer support.
APP-05 timebox ended~08:44MSK/not restarted; performance debts OPEN.
APP-06A/B PARTIAL,C/D and APP-07/release OPEN. No subagents this iteration.

## Текущий checkpoint APP-06A — 2026-09-27, source c22361f

Читайте [APP_06A_IDENTITY_AND_OWNER_CHECKPOINT_2026-09-27.md](APP_06A_IDENTITY_AND_OWNER_CHECKPOINT_2026-09-27.md)
для новых identity/owner gates, exact native SHA, full V2/physical results и
следующей работы. Коммиты3397de3/2340d5b/6495f14/7734b12/c22361f: один native
context; retained owner при failed Stop; aliases только observed serial;
same-owner expected-serial admission RTBW/Sweep и exact protocol1 guard.
34/34 native CTest,71 focused service tests,compiled binding test и894/66skip
full UI V2 PASS. Physical unknown-serial maxFs61.44/RF56/FFT4096 RX Stop/close
PASS; stable physical identity, common multi-family UI и frozen EXE не приняты.
Active nativeB218/diagnostic EXEaec7a85 не заменены; новая native0db493… staged.

Уточнение старого inventory текста ниже: ip:pluto.local с похожей моделью и
один USB **не доказывают alias** при пустом serial. Это route candidates,
не подтверждение отдельного RJ45 AD9363 или одной physical identity.
APP-05 timebox завершён около08:44MSK и не перезапущен; acceptance долги OPEN.
APP-06A/B PARTIAL, C/D OPEN. NEXT — coherent capability/catalog/owner routing,
не повторение короткого physical smoke или новый бесконечный APP-05 профиль.

Подготовка handoff 2026-09-27. APP-05 не закрыт. Четырёхчасовая итерация
APP-05 имеет прежнюю границу около08:44 MSK с исключением паузы; этот файл
не запускает новый таймер и не заменяет normative roadmap. Изменения только
UI V2; Legacy/DFL не переписывать и не использовать как новый product route.

## Начатый APP-06B — реальный scoped результат 2026-09-27

`08862a9` (pushed): отдельный `windows-msvc-cpu-hackrf` preset и explicit
SDK paths в native build, только `-StageOnly -Lane CPU -Configuration Release`.
Baseline CPU/CUDA presets явно держат official factory OFF, чтобы stale
CMake cache не включал другую device family. Staging не заменяет active
module/manifest и не допускает `-Clean`/activation. Header/import library и
три runtime DLL хешируются; manifest и preflight проверяют app-local identity
и callable factory presence, не запускают RX. Missing/pair/activation/CUDA
negative guards, tampered/missing/extra DLL/SDK hash tests PASS;16 focused
tests, Ruff/scoped mypy/diff PASS.

Exact08862a9 stage build115 targets/34 CTest PASS,43.38s. Artifact SHA
`02ff21ec4849c5babe37f040c5f49f1f07cee37374cf6ef932c7574947f561f2`,
manifest/source/ABI/schema5/self-test PASS. ActiveCPU SHA ee9a376b...,
manifest SHA ff5cd82f... неизменны. CMake показал object-path-length warning;
на этом пути все targets собрались, не объявлять этим portable path safety.

`a42f2ac` (pushed): bounded max-Fs probe с обязательным `--rx`, реальными
read-only capability и enumeration identity gates, issued permit и существующей
native factory. Native artifact имеет source08862a9; runner sourcea42f2ac
записаны отдельно, не объявлять их одной сборкой. Counter-delta/argument
negative tests PASS,6 combined focused tests/Ruff/diff PASS.

`app06_hackrf_max20_native_01.json`,30.0017s measure после3s warmup:
requested/API-accepted Fs20MS/s, RF filter15MHz/LNA16/VGA20/FFT4096/hop2048,
amp/bias OFF. Native admitted **20.00043 MS/s**, analytical **9765.83 FFT/s**,
18414 reduced frames polled (не UI LPS). Invalid contracts0, observed software
drops0, frame drop flags0, worker failures0; hardware overrun counter unavailable.
Explicit native Stop complete, worker joined, callbacks/slots/ready0 и
lifecycle closed. Source/opaque identity/runner/native/runtime SHA в JSON.
Независимый hardware Fs readback отсутствует, короткий ingress measurement
не доказывает RF continuity/duty/calibration/soak или common UI поддержку.

APP-06B пока **PARTIAL**: package closure/activation общего EXE и hardware
source routing ещё не выполнены. APP-06A/C/D OPEN. Исторический artifact08862a9
ещё не имел serial-bound revalidation на том же opened handle; этот пробел
устранён следующим source checkpoint, но старый JSON не становится доказательством
новой версии задним числом. No TX/firmware/firewall changes.

### Same-handle identity guard — b8fffbb, 2026-09-27

`b8fffbb` опубликован normal push. Issued permit хранит adapter-private четыре
serial words (без UI/log/repr раскрытия). Native factory protocol2 обязателен,
отделён от Spectrum wire schema5; старый/неверный module version отклоняется до
SDK call и без consumption permit. Binding требует expected words без default.
Native читает serial именно на handle, который затем настраивает RF/Start RX;
несовпадение возвращает open-stage status−30004. Failed close не теряет handle
перед destructor cleanup. Existing manual C++ tools могут явно оставаться без
optional expectation, product Python factory — нет. Exactly-one limitation
сохранён: это не выбор среди нескольких HackRF и не common Analyzer support.

37 focused tests (factory/identity/coordinator/application/explicit activation
workspace contract, не новая UI V2 composition/
staging/observer/artifact), scoped mypy3 files, diff-check PASS. Ruff новых
observer/staging files PASS;6 modified historical files:14 baseline findings,
14 current,0 new (не blanket Ruff PASS). Дополнительно запущенные5 Legacy
shell registration tests ERROR: DiagnosticsService не имеет snapshot; их shell/
presenter/service sources не менялись относительно HEAD. Legacy ошибки отдельно,
не скрывать в формулировке «все тесты прошли» и не чинить Legacy вместо UI V2.

Exact b8fffbb staged official module SHA
`c7f303eb5d166218c8a747cbf643fae90bd05a6de4f2a8fd0a0b56ed6c53b958`:
19 rebuilt targets,34/34 CTest PASS42.10s; manifest protocol2/source/SDK DLL
hashes/schema5/self-test PASS. Active CPU ee9a376b… и manifest ff5cd82f… unchanged.

`app06_hackrf_identity_guard_max20_native_02.json`, runner SHA
`13242a6b26c76c273e0fd4d3a192b1c5896af1158769b489fe39af10c1a38293`:
deliberately wrong native expectation отклонён ровно на identity-open gate,
затем fresh physical enumeration permit и30s normal RX at requested/API-accepted
20MS/s. Native admitted **20.00103 MS/s**, analytical **9766.13 FFT/s**,18389
reduced frames polled. Invalid frame contracts0, frame drop flags0, observed
software dropped samples0, worker failures0. Native Stop0.898ms в одном опыте
(не p95):complete/joined,callbacks/slots/ready0,lifecycle closed. Этот negative
test проверяет реальный comparator/cleanup, но не физическую USB device-swap
ситуацию. Hardware overrun counter/readback/continuity/calibration/long soak и
общий UI по-прежнему не доказаны. Published source contract:
`docs/implementation/APP06_OFFICIAL_HACKRF_STAGING_CONTRACT.md`.

### Shared runtime closure and sequential RX — 886820d / aec7a85

`886820d` adds a pre-freeze static guard without loading SDKs: canonical
libiio seven-file identity/presence, official factory manifest/protocol2,
and exact equality of the chosen shared libusb SHA with admitted HackRF SDK.
The local IIO libusb1.0.26 is missing two symbols required by SDK libusb1.0.30;
fresh processes reproduce official-extension import failure with old first
loaded DLL, and import/CPU self-test PASS with the admitted new DLL.17 focused
packaging tests PASS. Clean exactaec7a85 static reports `shared_runtime_*_02`
show baselineCPU PASS /official-old-IIO expected FAIL /explicit candidate PASS.
No system DLL overwritten, no silent runtime activation.

`aec7a85` adds a mandatory-opt-in bounded one-process RX compatibility probe,
not an Analyzer source selector. `app06_mixed_runtime_rx_01*` FAIL preserved:
old USB URI3.4.5 was stale under the new libusb, no Pluto RX and no next HackRF
phase. Read-only discovery returned URI2.4.5; route fallback with empty serial
is not an established stable physical Pluto identity. APP-06A must address it.
Explicit current-URI run `app06_mixed_runtime_rx_02*` PASS:
HackRF15s19.98943MS/s/9760.40FFT/s → PlutoUIV2 Fs61.44/RF56/CPU/FFT4096/Visual
and18 HideShow/Stop checks → HackRF15s19.98984MS/s/9760.60FFT/s. Separate
source generations1/3, invalid/drop flags/software drops0 and worker failures0;
both native owners stopped/joined, callbacks/slots/ready0/lifecycleclosed.
Pluto native ingress7.53945MS/s, UI workers[]/reserved0 after close. Loaded
first-name libusb path/file hash before/after59823a17… remains the explicit
candidate; this is not duplicate-DLL exclusion or in-memory attestation.

Full UI V2 gate exactb8fffbb with staged official C7 module injected in the
main process:894 tests/66 skipped/0 failures/289.246s, compiled deferred[] and
modules outside[]. Subprocesses use their canonical CPU module, not necessarily
the injected official module. Currentaec7a85 **CPU diagnostic EXE remains
official-HackRF OFF**, although its fresh34/34CTest/frozen/runtime/357-file
pipeline passed. Current nativeCPU SHA b218486c… supersedes earlier ee9a376b…
only for this rebuilt baseline. APP-06B frozen official/shared closure and
activation, APP-06A stable identity/common discovery, APP-06C UI routing and
APP-06D device matrix are still OPEN. No firmware/TX/amp/bias/firewall changes.

## Уже известные реальные границы

### AD936x capability cleanup and physical identity — e5ef91d

Published source e5ef91d retains the existing mapper's failed-disconnect owner,
rejects successful-looking evidence and new observe calls until explicit close
succeeds. No hidden cleanup/reopen, successful close idempotent. Two regression
reproductions FAIL before /PASS after;8 new +15 related domain/HackRF/tinySA
tests PASS, new-test Ruff/scoped mypy/diff PASS, historical product Ruff11→9
findings/0 new (not all lint clean). Source contract
`docs/implementation/APP06_AD936X_CAPABILITY_OWNERSHIP_CONTRACT.md`.

Real read-only USB check rejects mapping: native probe.serial and capability
serial both empty, firmware present. Direct readback gives tuning70MHz–6GHz,
Fs ranges2.083333–61.44MS/s, RF0.2–56MHz, gain−3..71dB. Native disconnect
confirmed connected=false; no RX/configure. Missing serial was already rejected
by the previous mapper; this is a stable-identity gap, not the new close guard.
Do not use a transient URI as admitted stable identity. Physical identity
enrichment from a genuine device witness and before-Start revalidation belong
to APP-06A; actual same-handle guarantees require native producer work, not
an inference from a separate ContextProbe. APP-06A remains OPEN.

Current frozen CPU package is exactaec7a85, not this later Python source;
full UI V2 with that rebuilt baselineCPU894/66skip/0fail290.468s. Repeated
clean e5ef91d source gate894/66skip/0fail290.466s PASS, compiled deferred[] /
modules outside[]. These do not close common device routing.

- Общий Analyzer UI V2 сейчас обслуживает Pluto через NativeLiveSessionService
  и continuous Sweep display adapter. Discover этого service ищет Pluto,
  а не все семейства; отсутствие HackRF в списке не доказывает неисправность.
- Подключённый HackRF One отвечает через существующий read-only libhackrf port:
  firmware2026.01.1/API0x0109. Probe закрыт, RX/TX из него не запускался.
  Локальные SDK/runtime компоненты есть, но CPU package35c645a собран с
  SDR_CORE_ENABLE_HACKRF_OFFICIAL=OFF: реального factory export в нём нет.
- В коде есть optional official native RX/DSP factory и HackrfLiveRequest,
  отдельные gain stages, permit/identity admission. Использовать существующий
  путь, не заменять его Python raw-I/Q циклом или обходом в Legacy widget.
- tinySA имеет deferred activation/analyzer binding и собственный Sweep API;
  это ещё не одна общая Analyzer задача. Максимум UI10001 points, приборный
  dBm сохранять; host FFT/RTBW/IQ не выдумывать. Наличие Ultra не доказывает
  аппаратную проверку Standard. Текущий physical tinySA port не подтверждён.
- Сейчас read-only inventory подтвердил USB AD9364; ip:pluto.local — его
  alias, а не доказательство отдельного RJ45 AD9363. Не объявлять Ethernet
  matrix проверенной по USB или по номинальному1Gbit link.

## Пакеты реализации и порядок

### APP-06A — честный общий capability/control contract

1. Сопоставить existing DeviceCapabilitySnapshot, DeviceDescriptor и
   family-specific request types; не создавать независимую вторую truth model.
2. Явно разделить runtime availability, declared capability и hardware-verified
   cell. Identity — opaque physical key; UI label — family/model/transport/RX,
   aliases не превращаются во второй владеющий radio.
3. Фиксировать frame units, acquisition kind и mode support. Pluto: AD936x
   readback выше chip-name предположений. HackRF: 8-bit IQ, Fs≤20MS/s и
   настоящие LNA/VGA/amp/bias; tinySA: calibrated dBm trace/points/RBW.
4. Unknown/unsupported/unverified не превращать в False/Supported без
   evidence. Запретить несовместимые requests до Start, без device call.

Проверки: fake family/runtime matrix, invalid/stale identity, unsupported
mode/units, отсутствующий runtime, modified AD9363 ranges, tinySA10001 cap.

### APP-06B — воспроизводимый native/runtime package HackRF

1. Изолированно включить existing official factory с проверенными SDK paths
   и DLL dependencies. CPU baseline без HackRF должен продолжать собираться.
2. Factory export, read-only probe и exception/cleanup contracts; никаких
   hidden TX/amp/bias/start. Manifest включает реальные dependency hashes.
3. Package/runtime closure gate отдельно от physical RX; тестовая fake factory
   не заменяет официальный hardware export.

### APP-06C — маршрутизация одной Analyzer задачи

1. Один application owner/router над existing services. Common discovery
   соединяет low-rate results, не стартует capture; выбранный adapter владеет
   configure/Start/Stop, family requests не приводятся к фиктивному common gain.
2. Переключение источника только после подтверждённого Stop/release. Failure
   cleanup не разрешает захват следующего device поверх старого owner.
3. Snapshot/frame normalization через existing immutable AnalyzerFrameBundle,
   coherent source/RX/epoch/generation/unit. SDR I/Q остаётся в native backend.
4. Один Spectrum/Waterfall/persistence pipeline, RTBW/Sweep на одной странице;
   controls соответствуют выбранному family. tinySA source/trace — состояния
   той же задачи, а не декоративная третья вкладка с отдельным UX.

Проверки: removal/busy/runtime absent/readback failure; Stop в каждой фазе,
late ACK после source change; no stale cross-device frame/units; bounded close.

### APP-06D — high-load hardware cells и общий EXE

- Pluto USB и реально доступный Ethernet отдельно; max verified applied Fs и
  ≥80%-profile, native delivered-rate отдельно от configured Fs и RF duty.
- HackRF CPU at20MS/s и high-load profile, правильные 8-bit normalization,
  board/API identity, спектр на том же canvas, Stop/Hide/Show/close.
- tinySA Standard/Ultra только при реально доступном приборе/firmware:
  points3100/8192/10001 где поддерживаются, RBW/atten/LNA/speed settings,
  dBm сохранён, unsupported прогресс не имитируется serial chunking.
- Exact-source package, normal UI selection + Start/Stop, source switching,
  frame periods/quality/supported matrix. Системные permission prompts не
  нажимать автоматически; static EXE promotion — recoverable archive policy.

## Что не считать завершением

План/новый DTO/SDK build сами по себе не закрывают APP-06. Общий интерфейс
должен физически обслуживать заявленные Supported combinations. Непроверенные
cells остаются OPEN; нет одностороннего исключения их из release scope.
APP-05 global freshness/Visual contention/long soak, populated-area65% и
RF-qualified pulse ledger остаются явным долгом для APP-12/14, а не PASS.
APP-07 multi-RX/multipane требует этого общего owner/capability пути; два RX
одного устройства не считаются независимыми LO/Fs без опубликованной topology.
