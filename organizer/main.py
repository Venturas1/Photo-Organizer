import os
import io
import sys
import base64
import logging
import threading
import tkinter as tk
# pyrefly: ignore [missing-import]
from tkinter import filedialog
# pyrefly: ignore [missing-import]
from PIL import Image
# pyrefly: ignore [missing-import]
import eel

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    base_dir = sys._MEIPASS
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

# Додаємо папку src до шляху пошуку модулів
sys.path.insert(0, os.path.join(base_dir, 'src'))

# Імпорт існуючих модулів бэкенда
# pyrefly: ignore [missing-import]
import media_parser
# pyrefly: ignore [missing-import]
import face_processor
# pyrefly: ignore [missing-import]
import clustering
# pyrefly: ignore [missing-import]
import file_organizer
# pyrefly: ignore [missing-import]
import database
# pyrefly: ignore [missing-import]
from config import parse_args, save_config

logger = logging.getLogger("main_server")
CURRENT_CONFIG = {}

# --- Потокобезопасное перенаправление stdout/stderr в Eel ---
class EelStdoutRedirector:
    """
    Перенаправляет потоки вывода в JS-функцию eel.append_log.
    Фильтрует tqdm прогресс-бары для экономии ресурсов.
    """
    def __init__(self, original_stream):
        self.original_stream = original_stream

    def write(self, text):
        # Пишем в оригинальный поток (консоль/терминал)
        if self.original_stream:
            self.original_stream.write(text)
            self.original_stream.flush()
        
        # Очищаем строки от возврата каретки
        text_clean = text.replace('\r', '\n')
        for line in text_clean.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # Пропускаем сырые tqdm прогресс-бары
            if '%' in line and ('|' in line or '█' in line):
                continue
            
            # Асинхронно шлем лог во фронтенд
            try:
                eel.append_log(line)
            except Exception:
                pass
        return len(text)

    def flush(self):
        if self.original_stream:
            self.original_stream.flush()


def setup_logging():
    if getattr(sys, 'frozen', False):
        app_dir = os.path.join(os.path.dirname(sys.executable), "organizer")
    else:
        app_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(app_dir, "data", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "organizer.log")
    
    # Настраиваем логгер для записи в файл
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8")
        ]
    )
    
    # Перенаправляем необработанные ошибки python в лог
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical("Необроблений виняток", exc_info=(exc_type, exc_value, exc_traceback))
        try:
            eel.append_log(f"[КРИТИЧНА ПОМИЛКА] {exc_value}")
        except Exception:
            pass

    sys.excepthook = handle_exception
    logger.info("Логування ініціалізовано на сервері.")


def setup_redirection():
    """Перенаправляет вывод консоли в веб-интерфейс."""
    sys.stdout = EelStdoutRedirector(sys.stdout)
    sys.stderr = EelStdoutRedirector(sys.stderr)


# --- Eel API Functions (@eel.expose) ---

@eel.expose
def get_initial_config():
    """Загружает и возвращает начальный объединенный конфиг."""
    global CURRENT_CONFIG
    CURRENT_CONFIG = parse_args()
    return CURRENT_CONFIG


@eel.expose
def select_directory(initial_dir=""):
    """Открывает нативный диалог выбора папки Windows."""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    selected_dir = filedialog.askdirectory(initialdir=initial_dir)
    root.destroy()
    return selected_dir


def run_pipeline_thread(config):
    """Фоновое выполнение этапов 1-4."""
    try:
        db_path = config["db_path"]
        print("================ Підготовка конвеєра ================")
        print(f"Вихідна папка:  {config['source_dir']}")
        print(f"Цільова папка:   {config['dest_dir']}")
        print(f"База даних:      {config['db_path']}")
        print(f"Пристрій:        {config['device']}")
        
        # ЭТАП 1
        eel.update_progress(10, "Етап 1: Сканування бази", "Шукаємо фотографії...")
        print("================ Етап 1: Сканування та дедуплікація ================")
        media_parser.scan_and_deduplicate(config)
        
        # ЭТАП 2
        eel.update_progress(35, "Етап 2: Метадані", "Вилучення геолокації та дат...")
        print("================ Етап 2: Вилучення метаданих та фільтрація скріншотів ================")
        media_parser.extract_metadata_and_filter(config)
        
        # ЭТАП 3
        eel.update_progress(60, "Етап 3: Штучний Інтелект", "Пошук облич на фотографіях...")
        print("================ Етап 3: Детекція облич та генерація ембеддінгів ================")
        face_processor.process_faces(config)
        
        # ЭТАП 4
        eel.update_progress(85, "Етап 4: Кластеризація", "Групування знайдених людей...")
        print("================ Етап 4: Кластеризація та іменування ================")
        clustering.match_incremental_faces(db_path)
        clustering.run_hdbscan_clustering(config)
        clustering.deduplicate_cluster_faces(db_path)
        
        # Находим неименованные кластеры
        unnamed_clusters = database.get_unnamed_clusters(db_path)
        
        eel.update_progress(100, "Розмітка облич", "Готуємо інтерфейс розмітки...")
        eel.on_pipeline_done(unnamed_clusters)
        
    except Exception as e:
        logger.exception("Помилка конвеєра в фоновому режимі")
        try:
            eel.on_pipeline_error(str(e))
        except Exception:
            pass


@eel.expose
def start_pipeline(config):
    """
    Сохраняет конфигурацию в JSON, сбрасывает базу данных при необходимости
    и запускает обработку в отдельном потоке.
    """
    global CURRENT_CONFIG
    CURRENT_CONFIG = config
    
    # Сохраняем конфигурацию в config.json
    save_config(config, "config.json")
    
    # Сброс базы данных, если передан флаг reset_db
    if config.get("reset_db"):
        db_path = config["db_path"]
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
                print(f"[СКИДАННЯ] Базу даних успішно видалено: {db_path}.")
            except Exception as e:
                print(f"[ПОМИЛКА] Не вдалося видалити файл бази данных: {e}", file=sys.stderr)
    
    # Запускаем конвейер в отдельном потоке
    thread = threading.Thread(target=run_pipeline_thread, args=(config,), daemon=True)
    thread.start()
    return True


def run_organizer_thread(config):
    """Фоновое выполнение этапа 5."""
    try:
        eel.update_progress(15, "Етап 5: Організація", "Організація медіа-архіву по папках...")
        print("================ Етап 5: Копіювання та організація медіа-архіву ================")
        file_organizer.organize_files(config)
        
        eel.update_progress(100, "Все готово!", "Архів успішно відсортовано. Програму можна закрити.")
        eel.on_organizer_done()
    except Exception as e:
        logger.exception("Помилка організації в фоновому режимі")
        try:
            eel.on_pipeline_error(str(e))
        except Exception:
            pass


@eel.expose
def start_organizer(config):
    """Запускает пятый этап организации файлов в отдельном потоке."""
    thread = threading.Thread(target=run_organizer_thread, args=(config,), daemon=True)
    thread.start()
    return True


@eel.expose
def get_unnamed_clusters():
    """Возвращает список ID неразмеченных кластеров."""
    db_path = CURRENT_CONFIG["db_path"]
    return database.get_unnamed_clusters(db_path)


@eel.expose
def get_cluster_faces(cluster_id):
    """Возвращает список лиц для конкретного кластера."""
    db_path = CURRENT_CONFIG["db_path"]
    return database.get_faces_by_cluster(db_path, cluster_id)


@eel.expose
def get_image_base64(filepath, bbox=None, crop=True):
    """
    Кодирует изображение в Base64.
    - Если crop=True и bbox передан, вырезает лицо.
    - Иначе (для превью) масштабирует изображение до 1920x1080 и масштабирует координаты bbox.
    """
    try:
        pil_img = media_parser.load_image_to_pil(filepath)
        
        if crop and bbox is not None:
            # Вырезаем лицо
            face_img = face_processor.crop_face(pil_img, bbox)
            if face_img.mode != "RGB":
                face_img = face_img.convert("RGB")
            byte_arr = io.BytesIO()
            face_img.save(byte_arr, format='JPEG', quality=90)
            return base64.b64encode(byte_arr.getvalue()).decode('utf-8')
        else:
            # Полноразмерное превью с оптимизацией разрешения
            orig_w, orig_h = getattr(pil_img, 'original_size', pil_img.size)
            
            # Ограничиваем разрешение до 1920x1080 для быстрой передачи
            pil_img.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
            new_w, new_h = pil_img.size
            
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            
            byte_arr = io.BytesIO()
            pil_img.save(byte_arr, format='JPEG', quality=85)
            encoded = base64.b64encode(byte_arr.getvalue()).decode('utf-8')
            
            # Рассчитываем новые масштабированные координаты bbox
            scaled_bbox = None
            if bbox is not None:
                scale_w = new_w / orig_w
                scale_h = new_h / orig_h
                scaled_bbox = [
                    bbox[0] * scale_w,
                    bbox[1] * scale_h,
                    bbox[2] * scale_w,
                    bbox[3] * scale_h
                ]
            
            return {"image": encoded, "bbox": scaled_bbox}
            
    except Exception as e:
        logger.error(f"Помилка при кодуванні зображення {filepath} в base64: {e}")
        return None


@eel.expose
def update_face_cluster(face_id, target_cluster):
    """Перемещает отдельное лицо в указанный кластер (например, в 'Noise')."""
    db_path = CURRENT_CONFIG["db_path"]
    try:
        database.update_face_cluster(db_path, face_id, target_cluster)
        return True
    except Exception as e:
        logger.error(f"Помилка при переміщенні обличчя {face_id} в {target_cluster}: {e}")
        return False


@eel.expose
def save_cluster_name(cluster_id, name):
    """Сохраняет имя для всего кластера (группы лиц)."""
    db_path = CURRENT_CONFIG["db_path"]
    try:
        database.bulk_update_face_cluster_name(db_path, cluster_id, name)
        return True
    except Exception as e:
        logger.error(f"Помилка при збереженні імені для групи {cluster_id}: {e}")
        return False


@eel.expose
def get_existing_names():
    """Возвращает список уникальных имен уже размеченных кластеров."""
    db_path = CURRENT_CONFIG["db_path"]
    try:
        named_faces = database.get_named_faces(db_path)
        unique_names = sorted(list(set(f["cluster_id"] for f in named_faces)))
        unique_names = [name for name in unique_names if name not in ("Noise", "пустышка")]
        return unique_names
    except Exception as e:
        logger.error(f"Помилка при отриманні списку імен з бази: {e}")
        return []


def main():
    setup_logging()
    
    global CURRENT_CONFIG
    CURRENT_CONFIG = parse_args()
    
    # Создаем папки при необходимости
    os.makedirs(CURRENT_CONFIG["source_dir"], exist_ok=True)
    os.makedirs(CURRENT_CONFIG["dest_dir"], exist_ok=True)
    
    # Инициализация Eel (папка web лежит в той же директории, что и main.py)
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        web_dir = os.path.join(sys._MEIPASS, 'web')
    else:
        web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
    eel.init(web_dir)
    
    # Настраиваем перенаправление вывода после инициализации Eel
    setup_redirection()
    
    logger.info("Запуск веб-интерфейса через Eel.")
    print("Ініціалізація веб-інтерфейсу...")
    
    try:
        # Запускаем окно Chrome размером 850x700, port=0 выбирает случайный свободный порт
        eel.start('index.html', size=(850, 700), mode='chrome', port=0)
    except (SystemExit, MemoryError, KeyboardInterrupt):
        logger.info("Програму закрито.")


if __name__ == "__main__":
    main()
