# APP-06A: coherent Pluto observation and capability catalog

Дата: 2026-09-27. Product source: `b7446bf5b1d299b0fc55e8d1729463172ce0a1f0`.
Final tested source: `9a8a5c1c8891a9e4e4d8aa5590109628cd3de661`;
второй commit меняет только terminal-cleanup test fixture, не product/native.
UI V2 only. Main dirty Legacy/DFL product не использован и не изменён.

## Таймер и состояние roadmap

Четырёхчасовой active-work timebox APP-05 закончился около08:44MSK с ранее
учтённой пользовательской паузой. Он не перезапускается этой итерацией.
APP-05 performance acceptance остаётся OPEN; работа продолжена в APP-06.
APP-06A/B PARTIAL, APP-06C/D и APP-07 OPEN. Общая APP-00…APP-14 цель active;
готовность финального Windows release не заявлена.

## Полученный integration результат

1. Native PlutoDevice имеет owned receiver_topology(). Probe, capabilities
   и scan-layout topology читаются из одного удерживаемого IIO context.
   Общий private helper копирует PHY input IDs и форматы stream elements,
   не открывает второй context, не включает каналы и не создаёт RX buffer.
   Windows и Linux source обновлены; Linux build не выполнялся.
2. Новый PLUTO_OBSERVATION_PROTOCOL_VERSION=1 — exact integer protocol для
   coherent observation, отдельно от identity protocol1 и wire schema5.
   Shared PlutoReadOnlyObserver выполняет одну low-rate транзакцию под RLock,
   проверяет contradictory URI/serial/firmware и отдаёт facts только после
   успешного disconnect. Facts transfer object — не новая truth model.
3. При failed close owner остаётся достижимым. Новый observe не открывает
   SDK/context и не делает скрытую retry; explicit close/Stop — recovery.
   NativeLive discovery/selection, Start и Sweep lease учитывают это
   обязательство. После ошибки закрытия нет fallback на следующий alias.
4. AD936x mapper переиспользует эти facts без дополнительного topology open.
   DeviceDescriptor ссылается на existing DeviceCapabilitySnapshot, а
   capability_inventory() возвращает existing DeviceCapabilityInventory
   без SDK/discovery I/O. Stable admission требует known serial, firmware
   и coherent protocol. Старый/malformed runtime не создаёт stable evidence.
5. Discovery deduplicates exact URI; проверяет64 scan routes/32 logical
   devices. Serial-only aliases сохранены. Contradictory/unverified alias
   capabilities не превращаются в один подтверждённый snapshot.
   Selection rereads chosen route и обновляет scalar/profile facts.
6. Unknown serial сохраняет usable explicit route и реальные observed
   диапазоны, но не stable physical/calibration entry. Structural digital
   RX pairs не доказывают физический RF path; shared LO остаётся unknown.

Это AD936x service catalog, не готовый общий multi-family registry/router.
Existing Live operational device/identity tokens и canonical capability/
calibration identity имеют разные namespaces; их explicit join/migration
для общего router остаётся задачей. Не выбирать источник только по сходству
model/URI и не считать route-scoped key stable physical identity.

Legacy standalone topology diagnostic не используется новым путем. Его
independent-context поведение не объявляется coherent hardware evidence.
Новые raw-I/Q Python/Qt loops, FFT/DSP/rendering/cadence изменения не добавлены.

## Проверки и первая регрессия

| Gate | Результат / scope |
| --- | --- |
| Clean b7446bf CPU StageOnly native | 34/34 CTest PASS,40.28s; manifest/source/ABI/schema5/self-test PASS |
| Compiled Python bindings + test-only IIO DLL | 2 tests /3 owner subtests PASS; exactly one context, closed before publication, no RF mutations/channel enables/buffers |
| Final combined service/application/observer tests | 109 PASS,0.417s; включают16 новых observation/catalog tests |
| Full UI V2, final9a8a5c1 + staged b7446bf | 894 tests/66 skipped/0 failures,249.723s; compiled deferred[], outside modules[] |
| New observer/root tests | Ruff PASS; observer + AD936x mapper scoped mypy2 files PASS; diff-check PASS |
| Historical terminal observer test lint | 20→20 findings,0 new kinds; это не lint-clean |

Первый full V2 прогон на b7446bf:894 tests/66skip,2 ERROR subcases.
Тест вызывал _release_stream на SimpleNamespace без нового observation owner.
9a8a5c1 дополняет fixture и требует observation_close после engine disconnect;
product cleanup не ослаблен. Повторный полный gate PASS. Ошибка не скрыта
как intermittency/hardware failure. Persistence NaN comparison RuntimeWarnings
остались; не заявлять zero-warning evidence.

Full V2 — source/offscreen regression, не desktop/DWM/RF acceptance.
Main process explicitly injects staged native. Subprocess tests могут читать
старый canonical module. Нативный код b7446bf идентичен в9a8a5c1, но manifest
source не переименован задним числом.

## Native / EXE provenance

Staged native SHA-256:
`383085b1441110a4e99d5d7d27170bf83998aca14d34477a8b472129bda39723`.
Manifest source b7446bf; CPU Release/cp313-win_amd64/schema5, self-test PASS.
Active canonical native SHA:
`b218486cffffbc451018e515daf0d049988d7b9dc139340f4c48d91383c8ad48`.
Он не заменён. Diagnostic EXE по approved stable path всё ещё sourceaec7a85;
новые изменения в него не включены. APP-00 installation/firmware/TX/amp/bias/
driver/firewall/security-dialog state в этой итерации не менялись.

## Physical USB check — b7446bf/staged383085…

Fresh USB scan дал один route. Owned observation coherent=true, два digital
scan elements, pending=false. Serial пустой; discovery вернул один usable
route, stable catalog entries0. Это ожидаемая честная граница, не отсутствие
подключённого SDR. Genuine RJ45 AD9363/known-serial positive case не доказаны.

Source backend RTBW: requested/readback Fs61.44MS/s/RF BW56MHz/FFT4096/CPU,
center2.4GHz/gain30dB. Readback fields center_hz/sample_rate_hz/
analog_bandwidth_hz/gain_db. За6.038s наблюдались116 разных latest sequence,
unit dBFS/bin. Это sampled reduced snapshots, не FFT LPS/UI FPS/USB61.44MS/s
continuity или metrology/long-soak acceptance.

Stop→connected; native engine disconnected, poller joined. Close очистил
engine/poller references; observation pending=false/release latch=false,
remaining Python threads[]. Evidence file
`app06_coherent_catalog_physical_01.json` сохранён локально, не опубликован;
SHA-256 `a9674974633b7a5303d600eca80569d972ddd28d4a1edd28630e3f6b6c957af0`.

## Следующий bounded пакет

1. До общего catalog/router исправить retained read-only cleanup для других
   family providers. На fake SDK воспроизведён LibhackrfReadOnlyPort.close:
   hackrf_close=-1, но closed=true, device/DLL refs cleared; следующий close
   делает0 SDK calls. Это новый известный cleanup defect, не аппаратный тест
   и не исправление в этом commit. Existing HackRF/tinySA adapter ownership
   также требует согласованной проверки; не обходить это новым adapter.
2. Common catalog над existing snapshots: runtime availability отдельно от
   declared/hardware-verified capability, explicit operational↔canonical
   identity join, unknown/unsupported distinctions и no-I/O invalid-request
   admission; preserve tinySA instrument dBm/10001 points.
3. APP-06B official HackRF/shared-DLL frozen closure; source/staged proof
   не заменяет actual package support.
4. APP-06C один Analyzer UI V2 owner/router и family controls, same canvas/
   units/source-generation validation, confirmed release before switching.
5. APP-06D high-Fs physical matrix и актуальный EXE; затем APP-07. APP-05
   Visual freshness/unforced memory/populated-area/QHD-DPI/Pd/50ms debts
   сохраняются до соответствующих release gates.

Субагенты в этой итерации не использовались. Независимый review этого нового
пакета ещё не выполнен; собственная scoped проверка не объявляется независимой.
