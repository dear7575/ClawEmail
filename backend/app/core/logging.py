import logging
import os
import sys
from datetime import datetime


LEVEL_NAMES = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "fatal": logging.CRITICAL,
    "critical": logging.CRITICAL,
}


class Log4jFormatter(logging.Formatter):
    """输出接近 log4j/Spring Boot 的单行日志格式。"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化单条日志记录。

        参数:
            record: Python logging 生成的日志记录。

        返回:
            接近 log4j/Spring Boot 风格的单行日志；异常会追加堆栈。
        """

        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        millis = int(record.msecs)
        thread_name = getattr(record, "request_id", None) or record.threadName
        logger_name = record.name
        message = record.getMessage()
        line = f"{timestamp}.{millis:03d} {record.levelname:<5} {os.getpid()} --- [{thread_name}] {logger_name} : {message}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def configure_logging(level: str) -> None:
    """配置 UTF-8 控制台输出，避免 Windows 控制台中文乱码。"""

    normalized_level = LEVEL_NAMES.get(level.lower(), logging.INFO)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    if os.name == "nt" and sys.stdout.isatty():
        os.system("chcp 65001 > nul")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(Log4jFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(normalized_level)
    root_logger.addHandler(handler)

    logging.getLogger("uvicorn").handlers.clear()
    logging.getLogger("uvicorn.access").handlers.clear()
