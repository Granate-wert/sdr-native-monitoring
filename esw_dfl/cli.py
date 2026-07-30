from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .activity_log import log_event
from .exporters import export_document_bundle, export_full_spectrogram_csv
from .parser import DflParser
from .spectrogram import load_spectrogram_preview


LOGGER = logging.getLogger("esw_dfl.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ESW DFL Analyzer")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="Показать состав DFL в JSON")
    inspect.add_argument("file", type=Path)
    export = sub.add_parser("export", help="Экспортировать трассы и предпросмотр спектрограмм")
    export.add_argument("file", type=Path)
    export.add_argument("output", type=Path)
    export.add_argument("--spectrogram-rows", type=int, default=800)
    full = sub.add_parser("spectrogram-csv", help="Полный потоковый экспорт спектрограммы")
    full.add_argument("file", type=Path)
    full.add_argument("output", type=Path)
    full.add_argument("--index", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log_event(LOGGER, "user", "cli_command_started", command=args.command, source_path=str(args.file))
    document = DflParser().parse(args.file)
    if args.command == "inspect":
        print(json.dumps(document.summary(), ensure_ascii=False, indent=2))
        log_event(LOGGER, "program", "cli_inspect_completed", source_path=str(args.file))
        return 0
    if args.command == "export":
        previews = {}
        for info in document.spectrograms:
            print(f"Чтение {info.title}...")
            log_event(
                LOGGER,
                "program",
                "cli_spectrogram_preview_started",
                stream=info.source_stream,
                max_rows=args.spectrogram_rows,
            )
            previews[info.source_stream] = load_spectrogram_preview(
                document.path,
                info,
                max_rows=args.spectrogram_rows,
                progress=lambda fraction, text: print(text, end="\r", flush=True),
            )
            log_event(
                LOGGER,
                "program",
                "cli_spectrogram_preview_completed",
                stream=info.source_stream,
                rows=int(previews[info.source_stream].values.shape[0]),
                points=int(previews[info.source_stream].values.shape[1]),
            )
        written = export_document_bundle(document, args.output, previews)
        print(f"\nСохранено файлов: {len(written)} в {args.output}")
        log_event(
            LOGGER,
            "program",
            "cli_bundle_export_completed",
            output=str(args.output),
            file_count=len(written),
        )
        return 0
    if args.command == "spectrogram-csv":
        if not document.spectrograms:
            raise SystemExit("В файле нет спектрограммы")
        info = document.spectrograms[args.index]
        log_event(
            LOGGER,
            "user",
            "cli_full_spectrogram_export_started",
            source_path=str(document.path),
            stream=info.source_stream,
            output=str(args.output),
        )
        export_full_spectrogram_csv(
            document.path,
            info,
            args.output,
            progress=lambda fraction, text: print(text, end="\r", flush=True),
        )
        print(f"\nСохранено: {args.output}")
        log_event(
            LOGGER,
            "program",
            "cli_full_spectrogram_export_completed",
            output=str(args.output),
        )
        return 0
    return 2
