"""日志配置"""
import sys
import logging
from logging.handlers import RotatingFileHandler
from config.settings import LOG_LEVEL, LOG_FILE


def setup_logger(name: str = "quant") -> logging.Logger:
    """配置并返回日志器"""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    if logger.handlers:
        return logger

    # 控制台handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(console)

    # 文件handler
    try:
        fh = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"
        ))
        logger.addHandler(fh)
    except Exception as e:
        print(f"日志文件初始化失败: {e}")

    return logger


logger = setup_logger()
