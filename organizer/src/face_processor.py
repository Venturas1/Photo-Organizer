import os
import logging
import urllib.request
from typing import Dict, Any, List, Optional
# pyrefly: ignore [missing-import]
from PIL import Image
from tqdm import tqdm
# pyrefly: ignore [missing-import]
import torch
# pyrefly: ignore [missing-import]
import torchvision.transforms as transforms
# pyrefly: ignore [missing-import]
from ultralytics import YOLO
# pyrefly: ignore [missing-import]
from facenet_pytorch import InceptionResnetV1

import database
import media_parser

logger = logging.getLogger("face_processor")

def get_torch_device(config: Dict[str, Any]) -> torch.device:
    """Определяет устройство (CUDA или CPU) на основе конфигурации и доступности."""
    config_device = config.get("device", "cuda").lower()
    if config_device == "cuda" and torch.cuda.is_available():
        logger.info("Используется GPU-ускорение (CUDA).")
        return torch.device("cuda")
    else:
        logger.info("Используется CPU.")
        return torch.device("cpu")

def ensure_yolo_weights(yolo_weights_path: str) -> None:
    """Проверяет наличие весов YOLOv8-Face и скачивает их при отсутствии."""
    if os.path.exists(yolo_weights_path):
        return
        
    url = "https://huggingface.co/junjiang/GestureFace/resolve/main/yolov8n-face.pt"
    logger.info(f"Веса YOLOv8-Face отсутствуют. Загрузка из {url} в {yolo_weights_path}...")
    print(f"Завантаження ваг YOLOv8-Face (близько 6 МБ)...")
    
    try:
        # Создаем директорию, если она не существует
        os.makedirs(os.path.dirname(os.path.abspath(yolo_weights_path)), exist_ok=True)
        # Скачиваем файл
        urllib.request.urlretrieve(url, yolo_weights_path)
        logger.info("Веса YOLOv8-Face успешно загружены.")
    except Exception as e:
        logger.error(f"Не удалось загрузить веса YOLOv8-Face: {e}")
        # Если загрузка не удалась, программа не сможет продолжить
        raise RuntimeError(f"Критическая ошибка: невозможно скачать веса модели YOLOv8-Face. Подробности: {e}")

def crop_face(pil_img: Image.Image, bbox: List[int]) -> Image.Image:
    """Вырезает лицо из исходного изображения с небольшим отступом (10%)."""
    x1, y1, x2, y2 = bbox
    w = x2 - x1
    h = y2 - y1
    
    # Добавляем 10% отступа с каждой стороны для лучшего распознавания Facenet
    pad_w = int(w * 0.1)
    pad_h = int(h * 0.1)
    
    img_w, img_h = pil_img.size
    
    nx1 = max(0, x1 - pad_w)
    ny1 = max(0, y1 - pad_h)
    nx2 = min(img_w, x2 + pad_w)
    ny2 = min(img_h, y2 + pad_h)
    
    cropped = pil_img.crop((nx1, ny1, nx2, ny2))
    
    # Preserve EXIF metadata to maintain correct orientation
    if hasattr(pil_img, 'info') and 'exif' in pil_img.info:
        cropped.info['exif'] = pil_img.info['exif']
        
    # pyrefly: ignore [missing-import]
    from PIL import ImageOps
    return ImageOps.exif_transpose(cropped)

def process_faces(config: Dict[str, Any]) -> None:
    """Стадия 3: Обнаружение лиц на фотографиях и генерация эмбеддингов."""
    db_path = config["db_path"]
    
    # Получаем список фотографий со статусом METADATA_EXTRACTED
    records = database.get_media_by_status(db_path, "METADATA_EXTRACTED")
    
    if not records:
        logger.info("Нет новых фотографий для обработки лиц.")
        print("Нові фотографії для обробки облич відсутні.")
        return
        
    # Инициализируем устройство (GPU/CPU)
    device = get_torch_device(config)
    print(f"Ініціалізація ІІ-моделей на пристрої: {device}...")
    
    # Обеспечиваем веса YOLOv8-Face
    ensure_yolo_weights(config["yolo_weights"])
    
    # Загружаем YOLOv8-Face
    try:
        yolo_model = YOLO(config["yolo_weights"])
    except Exception as e:
        logger.error(f"Не удалось инициализировать YOLOv8-Face: {e}")
        raise
        
    # Загружаем InceptionResnetV1 (Facenet)
    try:
        facenet_weights = config.get("facenet_weights", "vggface2")
        if os.path.exists(facenet_weights):
            # Загрузка локальных весов (оффлайн режим)
            facenet_model = InceptionResnetV1(device=device).eval()
            facenet_model.load_state_dict(torch.load(facenet_weights, map_location=device))
            logger.info(f"Локальные веса Facenet загружены из {facenet_weights}")
        else:
            # Стандартная загрузка предобученной модели (из интернета / кэша torch)
            logger.info("Загрузка предобученной модели Facenet (vggface2)...")
            facenet_model = InceptionResnetV1(pretrained="vggface2", device=device).eval()
    except Exception as e:
        logger.error(f"Не удалось инициализировать Facenet (InceptionResnetV1): {e}")
        raise

    # Препроцессинг для Facenet: ресайз до 160x160, перевод в тензор и нормализация
    preprocess = transforms.Compose([
        transforms.Resize((160, 160)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5])
    ])
    
    print(f"Обробка облич на {len(records)} фотографіях...")
    
    # Запускаем конвейер
    for record in tqdm(records, desc="Пошук та розпізнавання облич", unit="фото"):
        media_id = record["id"]
        filepath = record["filepath"]
        filename = record["filename"]
        media_type = record.get("media_type", "photo")
        
        # Якщо це відео, облич на ньому немає, тож просто маркуємо як оброблене
        if media_type == "video":
            database.update_media_status(db_path, media_id, "FACES_PROCESSED")
            continue
        
        try:
            # Удаляем старые лица для этого файла (защита от дубликатов при пересканировании)
            conn = database.get_connection(db_path)
            conn.execute("DELETE FROM faces WHERE media_id = ?;", (media_id,))
            conn.commit()
            conn.close()
            
            # 1. Загружаем PIL изображение (для RAW используется быстрое превью)
            pil_img = media_parser.load_image_to_pil(filepath)
            
            # Делаем проверку размера на всякий случай
            if pil_img.width == 0 or pil_img.height == 0:
                database.update_media_status(db_path, media_id, "CORRUPTED")
                continue
                
            # Конвертируем в RGB (обязательно для Facenet)
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
                
            # 2. Детектируем лица с помощью YOLOv8-Face
            # conf - порог уверенности детекции
            results = yolo_model(pil_img, conf=config["face_confidence"], device=device, verbose=False)
            
            # 3. Извлекаем bounding box'ы
            boxes = results[0].boxes
            
            if len(boxes) > 0:
                logger.info(f"Найдено лиц: {len(boxes)} на фото {filepath}")
                
                # Собираем эмбеддинги для всех лиц
                for box in boxes:
                    xyxy = box.xyxy[0].cpu().numpy()
                    bbox = [int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3])]
                    
                    # Вырезаем лицо
                    face_img = crop_face(pil_img, bbox)
                    
                    # Подготавливаем тензор
                    face_tensor = preprocess(face_img).unsqueeze(0).to(device)
                    
                    # Генерируем эмбеддинг
                    with torch.no_grad():
                        embedding = facenet_model(face_tensor)[0].cpu().numpy().tolist()
                        
                    # Сохраняем в БД (пока без кластера, cluster_id = None)
                    database.add_face(db_path, media_id, bbox, embedding)
            
            # Обновляем статус файла на FACES_PROCESSED (даже если лиц 0)
            database.update_media_status(db_path, media_id, "FACES_PROCESSED")
            
        except Exception as e:
            logger.error(f"Ошибка при поиске лиц на файле {filepath}: {e}")
            database.update_media_status(db_path, media_id, "CORRUPTED")
