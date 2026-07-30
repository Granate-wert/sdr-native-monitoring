from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import olefile

from .codec import (
    decode_numeric_blocks,
    decode_scalar_double,
    decode_timestamp,
    parse_tag_attributes,
    scalar_value,
    unit_label,
)
from .activity_log import log_event
from .models import (
    AcquisitionTiming,
    DflDocument,
    InstrumentInfo,
    MeasurementQuality,
    MeasurementWarning,
    SettingValue,
    SpectrogramInfo,
    TraceData,
)


LOGGER = logging.getLogger("esw_dfl.parser")


class DflFormatError(RuntimeError):
    pass


class StreamHandler(Protocol):
    name: str

    def matches(self, stream_name: str) -> bool: ...

    def read(
        self,
        parser: "DflParser",
        ole: olefile.OleFileIO,
        document: DflDocument,
        stream_name: str,
    ) -> None: ...


HANDLERS: list[StreamHandler] = []


def register_handler(handler: StreamHandler) -> StreamHandler:
    """Register another result-stream handler without changing the core parser."""
    HANDLERS.append(handler)
    return handler


def _xml(ole: olefile.OleFileIO, stream_name: str) -> ET.Element:
    try:
        return ET.fromstring(ole.openstream(stream_name).read())
    except (ET.ParseError, OSError) as exc:
        raise DflFormatError(f"Не удалось прочитать XML-поток {stream_name}: {exc}") from exc


def _direct_props(element: ET.Element | None) -> dict[str, ET.Element]:
    if element is None:
        return {}
    return {
        prop.attrib.get("Name", ""): prop
        for prop in element.findall("./Prop")
        if prop.attrib.get("Name")
    }


def _prop_text(props: dict[str, ET.Element], name: str, default: str = "") -> str:
    prop = props.get(name)
    return prop.attrib.get("Value", default) if prop is not None else default


def _active_measurement_group_name(root: ET.Element) -> str | None:
    """Return the mode-specific group name based on the Root Measurement prop."""
    channel = root.find("./Root")
    if channel is None:
        return None
    measurement = _prop_text(_direct_props(channel), "Measurement", "")
    if measurement == "AnalyzerSweep":
        return "AnalyzerSweep"
    if measurement == "RealTime":
        return "RealTime"
    return None


def _group_prop(element: ET.Element | None, group_path: str, name: str) -> SettingValue | None:
    """Read a named Prop from an XML group and return a structured SettingValue."""
    if element is None:
        return None
    prop = element.find(f"./Prop[@Name='{name}']")
    if prop is None:
        return None
    raw = prop.attrib.get("Value", "")
    value = scalar_value(raw)
    unit_id = prop.attrib.get("UnitId") or None
    auto_text = prop.attrib.get("AutoMode")
    auto_mode = None if auto_text is None else auto_text.lower() == "on"
    si_value: float | None = None
    if isinstance(value, (int, float)) and np.isfinite(value):
        si_value = float(value)
    return SettingValue(
        group_path=group_path,
        name=name,
        unit_id=unit_id,
        raw_value=raw if value is None else value,
        si_value=si_value,
        auto_mode=auto_mode,
    )


def _read_numeric_setting(
    group: ET.Element | None,
    group_path: str,
    name: str,
) -> SettingValue | None:
    """Read a numeric setting if the group exists and the prop has a finite value."""
    setting = _group_prop(group, group_path, name)
    if setting is None or setting.si_value is None:
        return None
    return setting


def _point_count_from_settings(raw_settings: dict[str, SettingValue]) -> int | None:
    value = raw_settings.get("MeasPoints")
    if value is None or value.si_value is None:
        return None
    count = int(value.si_value)
    return count if count > 0 else None


def _si_value(raw_settings: dict[str, SettingValue], name: str) -> float | None:
    value = raw_settings.get(name)
    return value.si_value if value is not None else None


def _stream_mode(stream_name: str) -> str:
    parts = stream_name.split("/")
    if "FSLSpectrumAnalyzer" in parts:
        index = parts.index("FSLSpectrumAnalyzer")
        if index + 1 < len(parts):
            return parts[index + 1]
    return parts[-2] if len(parts) >= 2 else "Unknown"


def _decode_axis(props: list[ET.Element], size: int) -> tuple[np.ndarray, str]:
    if not props or size <= 0:
        return np.empty(0, dtype=np.float64), ""
    first = props[0]
    unit = unit_label(first.attrib.get("UnitId"))
    start = decode_scalar_double(first.attrib.get("Start"))
    stop = decode_scalar_double(first.attrib.get("Stop"))
    block_items = int(first.attrib.get("BlockItems", "0") or 0)
    if start is not None and stop is not None and block_items == 0:
        return np.linspace(start, stop, size, dtype=np.float64), unit
    blocks = sorted(props, key=lambda item: int(item.attrib.get("Block", "0") or 0))
    return decode_numeric_blocks(
        (item.attrib.get("Value", "") for item in blocks), expected_items=size
    ), unit


@dataclass(slots=True)
class XmlTraceHandler:
    name: str = "xml-traces"

    def matches(self, stream_name: str) -> bool:
        return stream_name.endswith("/All Traces")

    def read(
        self,
        parser: "DflParser",
        ole: olefile.OleFileIO,
        document: DflDocument,
        stream_name: str,
    ) -> None:
        root = _xml(ole, stream_name)
        root_node = root.find("./Root")
        root_props = _direct_props(root_node)
        measurement = _prop_text(root_props, "Measurement", "Unknown")
        measurement_type = _prop_text(root_props, "MeasurementType", measurement)
        mode = _stream_mode(stream_name)
        trace_group = root.find(".//Group[@Name='Trace']")
        if trace_group is None:
            return
        group_props = _direct_props(trace_group)
        active_value = scalar_value(_prop_text(group_props, "ActiveIdx", "-1"))
        active_index = int(active_value) if isinstance(active_value, (int, float)) else -1
        fallback_unit_id = parser.level_unit_for_mode(document, mode)

        for item in trace_group.findall("./ArrItem"):
            trace_index = int(item.attrib.get("Index", "0") or 0)
            props = _direct_props(item)
            result = item.find("./Group[@Name='TraceResult']")
            result_props = result.findall("./Prop") if result is not None else []
            result_by_name: dict[str, list[ET.Element]] = {}
            for prop in result_props:
                result_by_name.setdefault(prop.attrib.get("Name", ""), []).append(prop)
            size_prop = result_by_name.get("TraceLength", [])
            size = int(
                scalar_value(size_prop[0].attrib.get("Value", "0")) if size_prop else 0
            )
            x, x_unit = _decode_axis(result_by_name.get("XValue", []), size)
            y, y_unit = _decode_axis(result_by_name.get("YValue", []), size)
            if not y_unit:
                y_unit = unit_label(fallback_unit_id)
            count = min(x.size, y.size)
            x = x[:count]
            y = y[:count]
            state = _prop_text(props, "State")
            detector = _prop_text(props, "Detector")
            update_mode = _prop_text(props, "UpdateMode")
            display_mode = _prop_text(props, "DisplayMode")
            has_signal = bool(y.size and np.isfinite(y).any() and not np.allclose(y, 0.0))
            if state.lower() in {"blank", "off"} and not has_signal:
                continue
            suffix = display_mode or update_mode or state or "Trace"
            result_kind = stream_name.split("/")[-2]
            kind_prefix = f"{result_kind} / " if result_kind != mode else ""
            title = f"{mode}: {kind_prefix}Trace {trace_index + 1} - {suffix}"
            document.traces.append(
                TraceData(
                    key=f"trace:{stream_name}:{trace_index}",
                    title=title,
                    mode=mode,
                    measurement=measurement,
                    measurement_type=measurement_type,
                    source_stream=stream_name,
                    trace_index=trace_index,
                    x=x,
                    y=y,
                    x_unit=x_unit,
                    y_unit=y_unit,
                    state=state,
                    detector=detector,
                    update_mode=update_mode,
                    display_mode=display_mode,
                    active=trace_index == active_index,
                    metadata={
                        "trace_length": size,
                        "all_zero": not has_signal,
                        "valid_entries": scalar_value(
                            result_by_name.get("ValidEntries", [ET.Element("Prop")])[0].attrib.get(
                                "Value", ""
                            )
                        ),
                    },
                )
            )


@dataclass(slots=True)
class XmlSpectrogramHandler:
    name: str = "xml-spectrogram"

    def matches(self, stream_name: str) -> bool:
        return stream_name.endswith("/Spectrogram")

    def read(
        self,
        parser: "DflParser",
        ole: olefile.OleFileIO,
        document: DflDocument,
        stream_name: str,
    ) -> None:
        size = ole.get_size(stream_name)
        stream = ole.openstream(stream_name)
        prefix = stream.read(min(size, 2_000_000))
        first_start = prefix.find(b"<SgramLine")
        if first_start < 0:
            return
        first_end = prefix.find(b">", first_start)
        if first_end < 0:
            return
        first_attrs = parse_tag_attributes(prefix[first_start : first_end + 1])
        first_close = prefix.find(b"</SgramLine>", first_end)
        if first_close < 0:
            return
        first_line_blob = prefix[first_start : first_close + len(b"</SgramLine>")]
        block_payloads = re.findall(rb'Data="([^"]+)"', first_line_blob)
        stored_points = sum(len(__import__("base64").b64decode(value)) // 4 for value in block_payloads)
        first_index = int(first_attrs.get("Line", "0") or 0)
        line_count = first_index + 1
        start_hz = decode_scalar_double(first_attrs.get("Start")) or 0.0
        stop_hz = decode_scalar_double(first_attrs.get("Stop")) or 0.0
        first_timestamp = decode_timestamp(first_attrs.get("Timestamp"))

        prefix_text = prefix[:first_start].decode("utf-8", "replace")
        history_match = re.search(r'Name="HistoryDepth" Value="([^"]+)"', prefix_text)
        history_depth = int(scalar_value(history_match.group(1))) if history_match else line_count
        measurement_match = re.search(r'Name="Measurement" Value="([^"]+)"', prefix_text)
        type_match = re.search(r'Name="MeasurementType" Value="([^"]+)"', prefix_text)
        measurement = measurement_match.group(1) if measurement_match else "Unknown"
        measurement_type = type_match.group(1) if type_match else measurement
        mode = _stream_mode(stream_name)

        point_count = parser.matching_trace_length(
            document, mode, start_hz, stop_hz, stored_points
        )
        last_timestamp = None
        if size > 0:
            tail_size = min(size, 500_000)
            stream.seek(size - tail_size)
            tail = stream.read(tail_size)
            tags = re.findall(rb"<SgramLine[^>]+>", tail)
            if tags:
                last_timestamp = decode_timestamp(parse_tag_attributes(tags[-1]).get("Timestamp"))
        timestamps = [value for value in (first_timestamp, last_timestamp) if value is not None]
        oldest_timestamp = min(timestamps) if timestamps else None
        newest_timestamp = max(timestamps) if timestamps else None
        level_unit_id = parser.level_unit_for_mode(document, mode)
        document.spectrograms.append(
            SpectrogramInfo(
                key=f"spectrogram:{stream_name}",
                title=f"{mode}: Spectrogram",
                mode=mode,
                measurement=measurement,
                measurement_type=measurement_type,
                source_stream=stream_name,
                line_count=line_count,
                point_count=point_count,
                start_hz=start_hz,
                stop_hz=stop_hz,
                newest_timestamp=newest_timestamp,
                oldest_timestamp=oldest_timestamp,
                y_unit=unit_label(level_unit_id) or "dBm",
                history_depth=history_depth,
                metadata={"stored_points_per_line": stored_points, "stream_size": size},
            )
        )


register_handler(XmlTraceHandler())
register_handler(XmlSpectrogramHandler())


class DflParser:
    """Parse a DFL container using a registry of independent stream handlers."""

    KEY_SETTINGS = {
        "CenterFreq",
        "Span",
        "StartFreq",
        "StopFreq",
        "AcquisitionTime",
        "RealTimeBandwidth",
        "SweepTime",
        "Rbw",
        "ResolutionBandwidth",
        "Vbw",
        "Unit",
        "Level",
        "LevelOffset",
        "AttenuationValue",
        "Detector",
        "SweepMode",
        "RealTimeMode",
    }

    def parse(self, path: str | Path) -> DflDocument:
        source = Path(path)
        log_event(LOGGER, "program", "dfl_parse_started", source_path=str(source))
        if not source.is_file():
            raise FileNotFoundError(source)
        if not olefile.isOleFile(source):
            raise DflFormatError("Файл не является контейнером OLE/CFB формата DFL")
        with olefile.OleFileIO(source) as ole:
            streams = ["/".join(parts) for parts in ole.listdir(streams=True, storages=False)]
            log_event(
                LOGGER,
                "program",
                "dfl_stream_inventory_completed",
                source_path=str(source),
                stream_count=len(streams),
            )
            instrument = self._instrument_info(ole, streams)
            document = DflDocument(path=source, instrument=instrument, streams=streams)
            self._read_settings(ole, document)
            # Traces define the exact logical point count used to trim padded spectrogram blocks.
            for handler in HANDLERS:
                for stream_name in streams:
                    if handler.matches(stream_name):
                        try:
                            log_event(
                                LOGGER,
                                "program",
                                "dfl_handler_started",
                                source_path=str(source),
                                handler=handler.name,
                                stream=stream_name,
                            )
                            handler.read(self, ole, document, stream_name)
                            log_event(
                                LOGGER,
                                "program",
                                "dfl_handler_completed",
                                source_path=str(source),
                                handler=handler.name,
                                stream=stream_name,
                            )
                        except Exception as exc:
                            document.warnings.append(
                                f"{handler.name}: {stream_name}: {type(exc).__name__}: {exc}"
                            )
                            log_event(
                                LOGGER,
                                "program",
                                "dfl_handler_failed",
                                level=logging.WARNING,
                                source_path=str(source),
                                handler=handler.name,
                                stream=stream_name,
                                exception_type=type(exc).__name__,
                                message=str(exc),
                            )
            document.traces.sort(
                key=lambda trace: (
                    bool(trace.metadata.get("all_zero")),
                    trace.x_unit != "Hz",
                    not trace.active,
                    trace.source_stream,
                    trace.trace_index,
                )
            )
            self._finalize_acquisition_timing(document)
            self._finalize_spectrogram_axes(document)
            if not document.traces and not document.spectrograms:
                raise DflFormatError("В DFL не найдены поддерживаемые трассы или спектрограммы")
            log_event(
                LOGGER,
                "program",
                "dfl_parse_completed",
                source_path=str(source),
                trace_count=len(document.traces),
                spectrogram_count=len(document.spectrograms),
                warning_count=len(document.warnings),
            )
            return document

    def _instrument_info(self, ole: olefile.OleFileIO, streams: list[str]) -> InstrumentInfo:
        info = InstrumentInfo()
        if "Header" in streams:
            root = _xml(ole, "Header")
            system = root.find("./System")
            firmware = root.find("./FirmwareVersion")
            device = root.find("./DeviceType")
            if system is not None:
                info.system = system.attrib.get("Value", info.system)
            if firmware is not None:
                info.firmware_version = firmware.attrib.get("Value", info.firmware_version)
            if device is not None:
                info.device_type = device.attrib.get("Value", info.device_type)
        if "LogicalInstrumentManager/Data" in streams:
            root = _xml(ole, "LogicalInstrumentManager/Data")
            for channel in root.findall(".//Channel"):
                name = channel.attrib.get("ChannelName", "")
                mode = channel.attrib.get("LIMode", "")
                if name and name not in info.channel_names:
                    info.channel_names.append(name)
                if mode and mode not in info.modes:
                    info.modes.append(mode)
        return info

    def _read_settings(self, ole: olefile.OleFileIO, document: DflDocument) -> None:
        for stream_name in document.streams:
            if not stream_name.endswith("/Current Settings"):
                continue
            mode = _stream_mode(stream_name)
            root = _xml(ole, stream_name)
            channel = root.find("./Root")
            measurement = _prop_text(_direct_props(channel), "Measurement", "")
            active_group_name = _active_measurement_group_name(root)
            active_group: ET.Element | None = None
            group_path = ""
            if active_group_name and channel is not None:
                active_group = channel.find(f"./Group[@Name='{active_group_name}']")
                group_path = f"Root/Channel/{active_group_name}" if active_group is not None else ""

            # Keep flat legacy settings for adapter compatibility.
            settings: dict[str, Any] = {}
            units: dict[str, str] = {}
            for prop in root.iter("Prop"):
                name = prop.attrib.get("Name", "")
                if name not in self.KEY_SETTINGS:
                    continue
                value = scalar_value(prop.attrib.get("Value", ""))
                settings[name] = value
                if prop.attrib.get("UnitId"):
                    units[name] = prop.attrib["UnitId"]

            settings["_units"] = units
            document.settings[mode] = settings

            raw_settings: dict[str, SettingValue] = {}
            warnings: list[MeasurementWarning] = []
            quality = MeasurementQuality.EXACT

            if active_group is None:
                quality = MeasurementQuality.UNKNOWN
                warnings.append(
                    MeasurementWarning(
                        "mode_specific_group_missing",
                        f"Не найдена mode-specific группа для Measurement={measurement!r}; "
                        "timing metadata unavailable",
                        {"measurement": measurement, "mode": mode},
                    )
                )
            else:
                for sub_name in ("MeasPoints", "ResolutionBandwidth", "VideoBandwidth", "SweepTime"):
                    sub = active_group.find(f"./Group[@Name='{sub_name}']")
                    sub_path = f"{group_path}/{sub_name}"
                    if sub is None:
                        continue
                    value_setting = _read_numeric_setting(sub, sub_path, "Value")
                    if value_setting is not None:
                        raw_settings[sub_name] = value_setting
                    auto_setting = _group_prop(sub, sub_path, "AutoMode")
                    if auto_setting is not None and sub_name in raw_settings:
                        raw_settings[sub_name].auto_mode = auto_setting.raw_value in ("On", True, 1)

                # Warn if SweepTime was taken from an ambiguous location.
                if "SweepTime" not in raw_settings:
                    quality = MeasurementQuality.APPROXIMATE
                    warnings.append(
                        MeasurementWarning(
                            "sweep_time_missing",
                            "SweepTime не найден в mode-specific группе",
                            {"group_path": group_path},
                        )
                    )

            document.acquisition_timing[mode] = AcquisitionTiming(
                mode=mode,
                measurement=measurement,
                point_count=_point_count_from_settings(raw_settings),
                rbw_hz=_si_value(raw_settings, "ResolutionBandwidth"),
                vbw_hz=_si_value(raw_settings, "VideoBandwidth"),
                instrument_sweep_time_s=_si_value(raw_settings, "SweepTime"),
                deadline_source="instrument_settings" if raw_settings else "unknown",
                quality=quality,
                warnings=warnings,
                raw_settings=raw_settings,
            )

            # Overwrite ambiguous flat legacy values with authoritative mode-specific ones.
            structural_aliases: dict[str, list[str]] = {
                "ResolutionBandwidth": ["Rbw", "ResolutionBandwidth"],
                "VideoBandwidth": ["Vbw", "VideoBandwidth"],
                "SweepTime": ["SweepTime"],
                "MeasPoints": ["MeasPoints"],
            }
            flat_settings = document.settings[mode]
            flat_units = flat_settings.setdefault("_units", {})
            for source_name, target_names in structural_aliases.items():
                setting = raw_settings.get(source_name)
                if setting is not None and setting.si_value is not None:
                    for target in target_names:
                        flat_settings[target] = setting.si_value
                        if setting.unit_id:
                            flat_units[target] = setting.unit_id

    def _finalize_acquisition_timing(self, document: DflDocument) -> None:
        """Fill timing fields that depend on traced/spectrogram data."""
        for timing in document.acquisition_timing.values():
            if timing.point_count is not None:
                continue
            for spectrogram in document.spectrograms:
                if spectrogram.mode == timing.mode and spectrogram.point_count > 0:
                    timing.point_count = spectrogram.point_count
                    break
            if timing.point_count is None:
                for trace in document.traces:
                    if trace.mode == timing.mode and trace.x.size > 0:
                        timing.point_count = int(trace.x.size)
                        break

    def _finalize_spectrogram_axes(self, document: DflDocument) -> None:
        """Repair spectrogram frequency axes that the container left at 0..0.

        Some swept DFLs store the frequency axis only on the trace, not on the
        SgramLine tag.  In that case map the spectrogram to the matching trace
        so the waterfall is not rendered with zero width.
        """
        for spectrogram in document.spectrograms:
            if spectrogram.start_hz != 0.0 or spectrogram.stop_hz != 0.0:
                continue
            for trace in document.traces:
                if trace.mode != spectrogram.mode or trace.x.size < 2:
                    continue
                if trace.x.size == spectrogram.point_count:
                    spectrogram.start_hz = float(trace.x[0])
                    spectrogram.stop_hz = float(trace.x[-1])
                    spectrogram.metadata.setdefault("axis_source", trace.source_stream)
                    break

    @staticmethod
    def level_unit_for_mode(document: DflDocument, mode: str) -> str:
        settings = document.settings.get(mode, {})
        value = str(settings.get("Unit", ""))
        return value if value.startswith("LEVEL_") else "LEVEL_DBM"

    @staticmethod
    def matching_trace_length(
        document: DflDocument,
        mode: str,
        start_hz: float,
        stop_hz: float,
        stored_points: int,
    ) -> int:
        for trace in document.traces:
            if trace.mode != mode or trace.x.size < 2:
                continue
            tolerance = max(1.0, abs(stop_hz - start_hz) * 1e-8)
            if abs(float(trace.x[0]) - start_hz) <= tolerance and abs(
                float(trace.x[-1]) - stop_hz
            ) <= tolerance:
                return int(trace.x.size)
        return stored_points
