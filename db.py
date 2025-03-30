import sqlite3
from sqlite3 import Error


def create_connection(db_file="database.db"):
    """Создает подключение с таймаутом для избежания deadlock"""
    conn = None
    try:
        conn = sqlite3.connect(
            db_file,
            timeout=30,  # 30 секунд ожидания при блокировке
            check_same_thread=False  # Для многопоточной работы
        )
        conn.row_factory = sqlite3.Row
        # Включаем WAL-режим для лучшей параллельной работы
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn
    except Error as e:
        app.logger.error(f"Ошибка подключения: {e}")
        return None

def init_db():
    """Инициализация БД с новой структурой"""
    tables = [
        """
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            class_id INTEGER,
            FOREIGN KEY (class_id) REFERENCES classes (id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            answer_formula TEXT,
            difficulty INTEGER DEFAULT 2,
            class_group INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY,
            class_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (class_id) REFERENCES classes (id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS lesson_tasks (
            lesson_id INTEGER,
            task_id INTEGER,
            task_order INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (lesson_id) REFERENCES lessons (id),
            FOREIGN KEY (task_id) REFERENCES tasks (id),
            PRIMARY KEY (lesson_id, task_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS hints (
            id INTEGER PRIMARY KEY,
            task_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            is_ai_generated BOOLEAN DEFAULT 0,
            FOREIGN KEY (task_id) REFERENCES tasks (id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS math_duels (
            id INTEGER PRIMARY KEY,
            class_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            current_round INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            FOREIGN KEY (class_id) REFERENCES classes (id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS duel_participants (
            duel_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            points INTEGER DEFAULT 0,
            position INTEGER DEFAULT 0,
            FOREIGN KEY (duel_id) REFERENCES math_duels (id),
            FOREIGN KEY (student_id) REFERENCES students (id),
            PRIMARY KEY (duel_id, student_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS duel_matches (
    id INTEGER PRIMARY KEY,
    duel_id INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    bracket_type TEXT NOT NULL CHECK(bracket_type IN ('upper', 'lower', 'final')),
    student1_id INTEGER,
    student2_id INTEGER,
    task_id INTEGER,
    winner_id INTEGER,
    generated_task TEXT,          -- Добавлено для хранения сгенерированных заданий
    correct_answer TEXT,          -- Добавлено для хранения правильных ответов
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (duel_id) REFERENCES math_duels(id),
    FOREIGN KEY (student1_id) REFERENCES students(id),
    FOREIGN KEY (student2_id) REFERENCES students(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (winner_id) REFERENCES students(id),
    CHECK (
        (student1_id IS NOT NULL OR student2_id IS NOT NULL) AND
        (winner_id IS NULL OR winner_id IN (student1_id, student2_id))
)
        )
        """,
        """
CREATE TABLE IF NOT EXISTS duel_round_tasks (
    duel_id INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    FOREIGN KEY (duel_id) REFERENCES math_duels(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    PRIMARY KEY (duel_id, round_number, task_id)
)
""",
"""
CREATE TABLE IF NOT EXISTS duel_answers (
    id INTEGER PRIMARY KEY,
    duel_id INTEGER NOT NULL,
    match_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    answer TEXT NOT NULL,
    is_correct BOOLEAN NOT NULL,
    answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (duel_id) REFERENCES math_duels(id),
    FOREIGN KEY (match_id) REFERENCES duel_matches(id),
    FOREIGN KEY (student_id) REFERENCES students(id)
)
""",
"""
CREATE TABLE IF NOT EXISTS duel_task_templates (
    id INTEGER PRIMARY KEY,
    duel_id INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    template TEXT NOT NULL,
    answer_formula TEXT NOT NULL,
    FOREIGN KEY (duel_id) REFERENCES math_duels(id)
)
""",
"""
CREATE TABLE IF NOT EXISTS duel_templates (
    id INTEGER PRIMARY KEY,
    duel_id INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    template TEXT NOT NULL,
    answer_formula TEXT NOT NULL,
    FOREIGN KEY (duel_id) REFERENCES math_duels(id)
)
"""
    ]
    
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            # Удаляем старые таблицы (если нужно)
            cursor.execute("DROP TABLE IF EXISTS old_tasks")
            
            for table in tables:
                cursor.execute(table)
            conn.commit()
        except Error as e:
            print(f"Ошибка при создании таблиц: {e}")
        finally:
            conn.close()

def seed_db():
    """Заполнение тестовыми данными с проверкой существования"""
    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Проверяем, есть ли уже тестовые данные
        cursor.execute("SELECT COUNT(*) FROM classes")
        if cursor.fetchone()[0] > 0:
            return  # Данные уже есть, пропускаем заполнение
        
        # Создаем классы
        classes = [
            ("5А",), ("5Б",), ("5В",),
            ("6А",), ("6Б",), ("6В",),
            ("7А",), ("7Б",), ("7В",),
            ("8А",), ("8Б",), ("8В",),
            ("9А",), ("9Б",), ("9В",),
            ("10А",), ("10Б",), ("10В",),
            ("11А",), ("11Б",), ("11В",)
        ]
        
        for class_name in classes:
            cursor.execute("INSERT INTO classes (name) VALUES (?)", class_name)
        
        # Добавляем учеников 6В класса
        class_6v_id = 6  # Предполагаем, что 6В имеет id=6
        students_6v = [
            ("Александров Артём А.", class_6v_id),
            ("Андреева Ольга", class_6v_id),
            ("Белов Михаил", class_6v_id),
            ("Васильев Максим А.", class_6v_id),
            ("Васильева Виктория", class_6v_id),
            ("Васильева Кира", class_6v_id),
            ("Васильева Мария П.", class_6v_id),
            ("Григорьев Артем", class_6v_id),
            ("Григорьев Максим В.", class_6v_id),
            ("Григорьева Елена", class_6v_id),
            ("Гунин Арсений", class_6v_id),
            ("Евграфов Олег", class_6v_id),
            ("Ефимов Захар", class_6v_id),
            ("Запольская Ульяна", class_6v_id),
            ("Исаев Роман", class_6v_id),
            ("Калашников Александр", class_6v_id),
            ("Калугин Иван", class_6v_id),
            ("Карпов Георгий", class_6v_id),
            ("Крылова Дарья А.", class_6v_id),
            ("Лапшинов Дмитрий", class_6v_id),
            ("Лемесова Светлана", class_6v_id),
            ("Мазин Михаил", class_6v_id),
            ("Михайлова Александра", class_6v_id),
            ("Никина Нина", class_6v_id),
            ("Никитин Демид В.", class_6v_id),
            ("Никифоров Евгений", class_6v_id),
            ("Николаев Матвей", class_6v_id),
            ("Павлов Арсений М.", class_6v_id),
            ("Пристромко Артем", class_6v_id),
            ("Прокопьев Андрей Н.", class_6v_id),
            ("Самуков Никита", class_6v_id),
            ("Сельцов Амир-Хан", class_6v_id),
            ("Суркова Юлиана", class_6v_id),
            ("Тупикова Дарья", class_6v_id),
            ("Федоров Денис", class_6v_id),
            ("Филимонова Валерия", class_6v_id),
            ("Хрисанова Ксения", class_6v_id)
        ]
        
        for student in students_6v:
            cursor.execute(
                "INSERT INTO students (name, class_id) VALUES (?, ?)",
                student
            )
        
        # Добавляем тестовые задания
        test_tasks = [
            ("Уравнение", "Решите: x + {A} = {B}", "{B} - {A}", 1, 6),
            ("Площадь", "Найдите площадь прямоугольника со сторонами {A} и {B}", "{A} * {B}", 2, 6),
            ("Проценты", "Сколько будет {A}% от числа {B}?", "{B} * {A} / 100", 2, 6),
            ("Степени", "Вычислите: {A}² + {B}²", "{A}**2 + {B}**2", 3, 6),
            ("Дроби", "Сложите дроби: {A}/{B} + {C}/{D}", "({A}*{D} + {C}*{B})/({B}*{D})", 3, 6)
        ]
        
        for title, content, answer, difficulty, class_group in test_tasks:
            cursor.execute("""
                INSERT INTO tasks 
                (title, content, answer_formula, difficulty, class_group) 
                VALUES (?, ?, ?, ?, ?)
            """, (title, content, answer, difficulty, class_group))
        
        conn.commit()
    except Error as e:
        print(f"Ошибка при заполнении тестовыми данными: {e}")
        conn.rollback()
    finally:
        conn.close()

def get_task_by_id(task_id):
    """Возвращает задание по ID"""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            return cursor.fetchone()
        except Error as e:
            print(f"Ошибка при получении задания: {e}")
        finally:
            conn.close()
    return None

def create_task(title, content, answer_formula="", difficulty=2, class_group=5):
    """Создает новое задание с заголовком и контентом"""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO tasks 
                (title, content, answer_formula, difficulty, class_group) 
                VALUES (?, ?, ?, ?, ?)""",
                (title, content, answer_formula, difficulty, class_group)
            )
            conn.commit()
            return cursor.lastrowid
        except Error as e:
            print(f"Ошибка при создании задания: {e}")
            conn.rollback()
            return None
        finally:
            conn.close()
    return None

def update_task(task_id, template, answer_formula, difficulty, class_group):
    """Обновляет существующее задание"""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE tasks 
                SET template = ?, answer_formula = ?, difficulty = ?, class_group = ? 
                WHERE id = ?""",
                (template, answer_formula, difficulty, class_group, task_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        except Error as e:
            print(f"Ошибка при обновлении задания: {e}")
            conn.rollback()
        finally:
            conn.close()
    return False

def delete_task(task_id):
    """Удаляет задание"""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            # Сначала удаляем связи с уроками
            cursor.execute("DELETE FROM lesson_tasks WHERE task_id = ?", (task_id,))
            # Затем само задание
            cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Error as e:
            print(f"Ошибка при удалении задания: {e}")
            conn.rollback()
        finally:
            conn.close()
    return False

def db_create_hint(task_id, content):
    """Создает подсказку для задания"""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO hints (task_id, content) VALUES (?, ?)",
                (task_id, content)
            )
            conn.commit()
            return cursor.lastrowid
        except Error as e:
            print(f"Ошибка при создании подсказки: {e}")
            conn.rollback()
            return None
        finally:
            conn.close()
    return None

def get_all_tasks(class_group=None):
    """Возвращает все задания (или для определенного класса)"""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            if class_group:
                cursor.execute("SELECT * FROM tasks WHERE class_group = ? ORDER BY created_at DESC", 
                             (class_group,))
            else:
                cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC")
            return cursor.fetchall()
        except Error as e:
            print(f"Ошибка при получении заданий: {e}")
        finally:
            conn.close()
    return []

def finish_duel(duel_id):
    """Помечает дуэль как завершенную"""
    conn = create_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE math_duels SET status = 'finished' WHERE id = ?",
            (duel_id,)
        )
        conn.commit()
        return True
    except Error as e:
        print(f"Ошибка при завершении дуэли: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()
# Инициализация базы при импорте
init_db()