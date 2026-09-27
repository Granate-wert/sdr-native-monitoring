# APP-06A/B: family ownership and consumed HackRF close
Дата: 2026-09-27. UI V2 only. Пакет не закрывает APP-06 или общий release.
Main dirty Legacy/DFL не использованы и не изменены. Субагенты не использовались.

## Таймер и roadmap

Четырёхчасовой active-work timebox APP-05 закончился около 08:44 MSK с
ранее учтённой паузой. Он не перезапущен. APP-05 performance debts OPEN;
текущая работа продвигает APP-06. Цель APP-00…APP-14 остаётся active.
APP-06A/B PARTIAL, C/D и APP-07 OPEN. Не объявлять Windows release готовым.

## Выполненные изменения

- 7dd6271: один RetainedReadOnlyObserver для HackRF/tinySA capabilities и
  HackRF enumeration preflight. Low-rate factory/probe/close сериализованы
  на retained provider. Facts/permit публикуются только после close.
  Failed close оставляет pending owner; новый observe не создаёт port и
  не выполняет скрытую retry. Explicit close — продолжение cleanup.
- HackRF read-only port теперь lazy: constructor возвращает owner ДО SDK
  acquisition. Failed partial open остаётся достижимым. Enumeration-only
  port также сохраняет свои ещё принадлежащие ему ресурсы.
- 8cffdcd: invalid typed payload отклоняется TypeError, tinySA import hygiene.
  Existing Snapshot/calibration truth, dBm/extra correction/no-IQ semantics
  tinySA и ограничения request admission не изменены.
- fe1dd69: исправлена важная ошибка самой первоначальной cleanup candidate
  и прежнего native same-handle guard. Official hackrf_close освобождает
  pointer даже при nonzero return. Python/native всегда инвалидируют его
  после normal return; первая ошибка остаётся видимой. Explicit cleanup
  продолжает только оставшиеся list/exit/dependency phases. Native session
  повторный Stop не вызывает SDK close с уже освобождённым pointer.
- При foreign exception вместо return ownership неизвестна: Python port
  quarantined, refs/pending сохраняются; повторный SDK close/новый probe
  запрещены. In-process recovery в этом случае НЕ доказан.
- Новый CTest компилирует реальный official wrapper с test-only SDK symbols:
  consumed close error, identity mismatch/read failure + destructor, session
  Stop retry. Vendor ABI/реальный USB close-error этим не проверяются.

Provider lock — per-instance, не process-global SDK arbiter. Common owner
ещё должен удерживать providers; temporary adapter или новый instance
не разрешены как обход outstanding cleanup. Existing explicit Legacy
HackRF application stop пока не является общим UI V2 provider recovery router.
tinySA проверен на injected capability-port, не на физическом serial collector.

## Исправление прежнего SDK предположения

Выбранный local SDK hackrf.c:2413..2448 освобождает handle до возврата
stop/thread error. Полные hackrf.c/h тексты после нормализации CRLF/LF
совпали с pinned [официальным v2026.01.3](https://github.com/greatscottgadgets/hackrf/blob/1cfe7dfe98d333450217d50e3f3a1ad0702e000f/host/libhackrf/src/hackrf.c#L2413).
Local source SHA-256 B806689A7ABCA90C0C0BF40B3FD2833E3F9EDEEA38BBE264B360A32DA11BDD09;
header 2B51D69A5C7BA04DDFB028E30BE672487239D4051575B093DB41FAF11C244391.
Это source review, не binary attestation/signed vendor release.
SDK close может отправить собственную stop command; no Configure/RX/TX
относится к явному API allowlist read-only probe, не к нулю USB OUT traffic.

Исходные fake tests ошибочно предполагали keep-pointer-on-error и были
зелёными до primary-source review. Теперь отдельны consumed-return и
ambiguous-exception cases. Предыдущий coherent-catalog report/исторический
same-handle текст не являются доказательством device-pointer leak при
nonzero close. Реальные obligations — оставшиеся resources/error/pending
и недопущение повторного обращения к consumed pointer.

## Проверки

| Gate | Результат и scope |
| --- | --- |
| Family/admission/factory/application/staging | exactfe1dd69:73 PASS,11.968s; comment-only repeat73 PASS,8.592s; 11 новых cleanup tests с subcases |
| Clean fe1dd69 official CPU StageOnly | 35/35 CTest PASS,42.08s; header freshness, manifest/ABI/schema5/self-test PASS |
| Full UI V2 fe1dd69 + staged native | 894 tests/66 skipped/0 failures,250.548s; compiled deferred[], outside product imports[] |
| Scoped mypy | 6 changed source files PASS |
| Ruff new helper/tinySA/new tests | PASS |
| Historical 5 product files | cb5edad24 → final10 findings; no new kinds/count increases, НЕ lint-clean |
| Diff-check | PASS |

После gates удалён один лишний BLE001 noqa comment: runtime/native behavior
не менялось. Intermediate fe1dd69 lint comparison нашёл новый RUF100 (unused
noqa); исправлен, не скрыт blanket PASS. Native/full/RX evidence остаётся
от exact fe1dd69, не переименовывается под последующий comment/doc SHA.
Earlier 8cffdcd fullV2 gate894/66skip/0fail251.261s также прошёл, но не
заменяет финальный fe1dd69 native-consumption gate. Persistence NaN
comparison RuntimeWarnings остаются. Native object-path-length/C4996 warnings
не доказывают portable path safety и не скрываются.

Full V2 — source/offscreen regression. Main process injects staged artifact,
subprocesses могут использовать старый canonical native. Это не actual
desktop, DWM FPS, FHD/QHD/DPI, long soak или frozen acceptance.
Независимый review данного пакета не выполнен; это собственная проверка.

## Provenance и физический HackRF

Новая staging native:
d8194e762495dd29e1fbead042ee1a49cdef9e3ba048c8a24dcb303450039283.
Manifest source fe1dd69c5397d1f10442d8e7ac90303218e3b60f,
windows-msvc-cpu-hackrf/CPU Release/cp313/schema5/factory protocol2.
Старый native C7f303… с unsafe close retry не считать current candidate.
Canonical native B218486CFFFFBC451018E515DAF0D049988D7B9DC139340F4C48D91383C8AD48
и diagnostic EXE source aec7a85 не заменены. Новый EXE не собирался.
SDK DLL/header/import hashes неизменны; shared-runtime frozen closure OPEN.

Read-only positive на exact8cffdcd: capability + отдельная enumeration
identity совпали, permit issued/not consumed, обе port closed/pending false.
No RF Configure/RX/TX API invoked; loaded-module bool в raw JSON является
runner intent, не измерением Windows module map. No physical failed-close.
Raw app06_hackrf_readonly_cleanup_01.json SHA-256
c07fb27a41f08508ad7551a228ef6d5e4ae539d62f5e0098609363ff955f412e.

Final exactfe1dd69 staged physical RX: wrong expected identity rejected at
native open−30004 before Configure/Start, затем fresh permit и нормальный RX.
Fs requested/API-accepted20MS/s, filter15MHz, center100MHz, LNA16/VGA20,
FFT4096/hop2048/reference-f64/CPU, amp/bias OFF. 3s warmup +30.000473s:
600047616 admitted samples →20.001272MS/s; analytical9766.246 FFT/s;
18428 reduced frames polled, invalid/drop flags/software-dropped samples0,
worker failures0. Device overrun counter unavailable.
Stop0.6148ms complete, worker joined, callbacks/slots/ready0/lifecycle closed.

Raw app06_hackrf_consumed_close_max20_01.json SHA-256
e4a6c4890233ffa8d88589b2266bb997da8116aaf61be16c7c588931922a6d97.
Runner SHA-25613242a6b26c76c273e0fd4d3a192b1c5896af1158769b489fe39af10c1a38293.
Evidence lives in main evidence/app06_family_cleanup_20260927, local ignored,
не пушится и не перезаписывается. Native/runner source/hashes в JSON.

Эти numbers не UI LPS/FPS, независимый ADC-Fs readback, RF calibration,
duty/Pd/continuity/USB-loss, long-term stability или common Analyzer proof.
Wrong identity — диагностическая подмена ожидания, не actual device swap.
Физический unplug/SDK failure намеренно не провоцировался. No TX/firmware/
driver/firewall/security-dialog operations. Прибор остановлен.

## Следующий bounded пакет

1. Common catalog над existing Snapshot/Inventory: runtime availability
   отдельно от declared/hardware-verified support, explicit operational↔
   canonical identity join, aliases только по подтверждённой identity.
2. No-I/O family request admission/unknown-vs-unsupported matrix, modified
   AD9363/readback и tinySA dBm/10001 cap. Не создавать вторую truth model.
3. Official shared-libusb frozen closure и current-source package, затем
   один UI V2 owner/router, retained provider recovery/source switching.
4. Family controls и одни AnalyzerFrameBundle/Spectrum/Waterfall, без
   cross-source/RX/epoch/unit stale data; SDR IQ остаётся native.
5. APP-06D actual high-Fs USB/Ethernet/HackRF/tinySA cells и common EXE,
   затем APP-07. APP-05 debts остаются для APP-12/14 release gates.

Сборка, DTO, короткий RX или документация сами по себе не закрывают APP-06.
Полная цель остаётся APP-00…APP-14, не этот checkpoint.
