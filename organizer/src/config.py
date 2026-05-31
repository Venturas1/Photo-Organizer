import os
import sys
import json
import argparse
from typing import Dict, Any

# Дефолтные настройки программы
DEFAULT_CONFIG = {
    "source_dir": "input",
    "dest_dir": "output",
    "db_path": os.path.join("data", "database.db"),
    "yolo_weights": os.path.join("data", "models", "yolov8n-face.pt"),
    "facenet_weights": "vggface2",  # facenet-pytorch использует vggface2 или casia-webface
    "device": "cuda",  # Будет проверено при инициализации
    "face_confidence": 0.6,
    "metadata_threads": 8,
    "screenshot_rules": {
        "keywords": ["screenshot", "screen", "скриншот", "снимка"],
        "check_exif": True,
        "aspect_ratio_min": 1.7,  # Для вертикальных экранов (высота/ширина)
        "aspect_ratio_max": 2.2,
    },
    "duplicates": {
        "hash_size": 8,
        "hash_threshold": 4,  # Порог расстояния Хэмминга для visual duplicate
    }
}

def load_config(config_path: str) -> Dict[str, Any]:
    """Загрузка конфигурации из JSON-файла."""
    if not os.path.exists(config_path):
        return DEFAULT_CONFIG.copy()
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            user_config = json.load(f)
            # Рекурсивное обновление дефолтного конфига пользовательским
            config = DEFAULT_CONFIG.copy()
            for key, val in user_config.items():
                if isinstance(val, dict) and key in config:
                    config[key] = {**config[key], **val}
                else:
                    config[key] = val
            return config
    except Exception as e:
        print(f"Предупреждение: Не удалось загрузить конфигурационный файл. Использование настроек по умолчанию. Ошибка: {e}")
        return DEFAULT_CONFIG.copy()

def save_config(config: Dict[str, Any], config_path: str) -> None:
    """Сохранение конфигурации в JSON-файл."""
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Ошибка при сохранении конфигурационного файла: {e}")

def parse_args() -> Dict[str, Any]:
    """Парсинг аргументов командной строки и объединение их с конфигурационным файлом."""
    parser = argparse.ArgumentParser(description="Автоматический локальный органайзер медиа-архива")
    
    parser.add_argument("--config", type=str, default="config.json", help="Путь к файлу конфигурации (JSON)")
    parser.add_argument("--source", type=str, help="Путь к исходной папке с медиа-файлами")
    parser.add_argument("--dest", type=str, help="Путь к целевой папке для организованных медиа")
    parser.add_argument("--db", type=str, help="Путь к базе данных SQLite")
    parser.add_argument("--device", type=str, choices=["cuda", "cpu"], help="Устройство для работы нейросетей (cuda или cpu)")
    parser.add_argument("--yolo-weights", type=str, help="Путь к весам YOLOv8-Face (.pt)")
    parser.add_argument("--face-conf", type=float, help="Порог уверенности для детекции лиц")
    parser.add_argument("--save-settings", action="store_true", help="Сохранить текущие параметры командной строки в файл конфигурации")
    parser.add_argument("--reset-db", action="store_true", help="Скинути (очистити) базу даних до нуля та почати заново")

    args = parser.parse_args()
    
    # Сначала загружаем файл конфигурации
    config = load_config(args.config)
    
    # Корневая папка скрипта для разрешения относительных путей.
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        script_dir = sys._MEIPASS
        root_dir = os.path.dirname(sys.executable)
    else:
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        root_dir = os.path.dirname(script_dir)  # Корень проекта (папка "Фото")
    
    # Переопределяем параметры переданными CLI аргументами
    if args.source:
        config["source_dir"] = os.path.abspath(args.source)
    elif not config["source_dir"]:
        config["source_dir"] = os.path.join(root_dir, "input")
    elif not os.path.isabs(config["source_dir"]):
        config["source_dir"] = os.path.abspath(os.path.join(root_dir, config["source_dir"]))
        
    if args.dest:
        config["dest_dir"] = os.path.abspath(args.dest)
    elif not config["dest_dir"]:
        config["dest_dir"] = os.path.join(root_dir, "output")
    elif not os.path.isabs(config["dest_dir"]):
        config["dest_dir"] = os.path.abspath(os.path.join(root_dir, config["dest_dir"]))
        
    # Путь к БД
    if args.db:
        config["db_path"] = os.path.abspath(args.db)
    elif not os.path.isabs(config["db_path"]):
        if getattr(sys, 'frozen', False):
            config["db_path"] = os.path.abspath(os.path.join(root_dir, "organizer", config["db_path"]))
        else:
            config["db_path"] = os.path.abspath(os.path.join(script_dir, config["db_path"]))
        
    if args.device:
        config["device"] = args.device
    if args.yolo_weights:
        config["yolo_weights"] = args.yolo_weights
    elif not os.path.isabs(config["yolo_weights"]):
        config["yolo_weights"] = os.path.abspath(os.path.join(script_dir, config["yolo_weights"]))
    if args.face_conf is not None:
        config["face_confidence"] = args.face_conf
    
    config["reset_db"] = args.reset_db
    
    # Если указан флаг сохранения настроек, перезаписываем файл конфигурации
    if args.save_settings:
        save_config(config, args.config)
        print(f"Настройки успешно сохранены в файл {args.config}")
        
    return config
