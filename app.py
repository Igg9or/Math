from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3
import json, os, logging, io, sys
from dotenv import load_dotenv
from sqlite3 import Error
import random, logging
import secrets
import requests  # ← Добавьте эту строку вверху файла!
from flask import request  # Убедитесь, что это есть (если используется Flask)
from db import (
    init_db, 
    seed_db, 
    create_connection, 
    get_task_by_id, 
    get_all_tasks, 
    create_task as db_create_task, 
    update_task, 
    delete_task,
    db_create_hint
)

from flask import abort
from flask_session import Session
import os
from werkzeug.utils import secure_filename
import re
from flask import Flask, request, jsonify
from logging.handlers import RotatingFileHandler

sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("api_errors.log"),
        logging.StreamHandler()
    ]
)

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация приложения
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_THRESHOLD'] = 100
app.config['PERMANENT_SESSION_LIFETIME'] = 1800  # 30 минут
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
app.config['UPLOAD_FOLDER'] = 'static/uploads'
Session(app)

handler = RotatingFileHandler(
    'app.log', 
    encoding='utf-8',  # Важно!
    maxBytes=1024 * 1024,
    backupCount=3
)
handler.setLevel(logging.INFO)
app.logger.addHandler(handler)

DEEPSEEK_API_KEY = "sk-0eebbaabbab648099f7e507d6e30f03a"
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Создаем папку для загрузок, если ее нет
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Инициализация БД при старте
init_db()
seed_db()

def parse_deepseek_response(response_text):
    # Ищем JSON в ответе с помощью регулярки
    json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            return None
    return None

@app.before_request
def check_concurrent_users():
    if request.path.startswith('/student'):
        if len(session) > 40:
            abort(503, "Сервер перегружен, попробуйте позже")

@app.route("/logout")
def logout():
    session.pop('student_id', None)
    session.pop('is_admin', None)
    return redirect(url_for('index'))

@app.route("/", methods=["GET", "POST"])
def index():
    conn = create_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM classes")
        classes = cursor.fetchall()

        if request.method == "POST":
            if "student_id" in request.form:
                session['student_id'] = int(request.form["student_id"])
                return redirect(url_for("student_lessons", student_id=session['student_id']))
            elif request.form.get("password") == "12345":
                session["is_admin"] = True
                return redirect(url_for("admin_dashboard"))
            else:
                flash("Неверный пароль", "error")

        return render_template("index.html", classes=classes)
    except Error as e:
        flash("Ошибка базы данных", "error")
        return render_template("index.html", classes=[])
    finally:
        conn.close()

@app.route('/admin')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    
    conn = create_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM classes")
        classes = cursor.fetchall()
        return render_template('admin_dashboard.html', classes=classes)
    except Error as e:
        flash(f"Ошибка базы данных: {e}", "error")
        return redirect(url_for('index'))
    finally:
        conn.close()

@app.route('/admin/class/<int:class_id>/lessons')
def class_lessons(class_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    
    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Информация о классе
        cursor.execute("SELECT name FROM classes WHERE id = ?", (class_id,))
        class_info = cursor.fetchone()
        if not class_info:
            flash("Класс не найден", "error")
            return redirect(url_for('admin_dashboard'))
        
        # Получаем обычные уроки
        cursor.execute("""
            SELECT id, name, created_at, 'lesson' as type, NULL as status
            FROM lessons 
            WHERE class_id = ?
            ORDER BY created_at DESC
        """, (class_id,))
        lessons = cursor.fetchall()
        
        # Получаем дуэли
        cursor.execute("""
            SELECT id, name, created_at, 'duel' as type, status
            FROM math_duels
            WHERE class_id = ?
            ORDER BY created_at DESC
        """, (class_id,))
        duels = cursor.fetchall()
        
        # Все задания для выбора
        cursor.execute("SELECT id, title FROM tasks")
        all_tasks = cursor.fetchall()
        
        # Объединяем уроки и дуэли
        all_lessons = lessons + duels
        
        return render_template('class_lessons.html',
                           class_id=class_id,
                           class_name=class_info['name'],
                           lessons=all_lessons,
                           all_tasks=all_tasks)
    except Error as e:
        flash(f"Ошибка базы данных: {e}", "error")
        return redirect(url_for('admin_dashboard'))
    finally:
        conn.close()

@app.route('/create_lesson', methods=['POST'])
def create_lesson():
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    
    class_id = request.form.get('class_id')
    name = request.form.get('name')
    task_ids = request.form.getlist('task_ids')
    
    if not class_id or not name:
        flash("Заполните все обязательные поля", "error")
        return redirect(url_for('class_lessons', class_id=class_id))
    
    conn = create_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lessons (class_id, name) VALUES (?, ?)",
            (class_id, name)
        )
        lesson_id = cursor.lastrowid
        
        for order, task_id in enumerate(task_ids, 1):
            cursor.execute(
                "INSERT INTO lesson_tasks (lesson_id, task_id, task_order) VALUES (?, ?, ?)",
                (lesson_id, int(task_id), order)
            )
        
        conn.commit()
        flash("Урок успешно создан", "success")
    except Error as e:
        conn.rollback()
        flash(f"Ошибка при создании урока: {e}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('class_lessons', class_id=class_id))

@app.route('/admin/lessons/<int:lesson_id>/edit', methods=['GET', 'POST'])
def edit_lesson(lesson_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    
    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        if request.method == 'POST':
            name = request.form.get('name')
            task_ids = request.form.getlist('task_ids')
            
            if not name:
                flash("Введите название урока", "error")
                return redirect(url_for('edit_lesson', lesson_id=lesson_id))
            
            cursor.execute("UPDATE lessons SET name = ? WHERE id = ?", 
                         (name, lesson_id))
            
            cursor.execute("DELETE FROM lesson_tasks WHERE lesson_id = ?", 
                         (lesson_id,))
            
            for order, task_id in enumerate(task_ids, 1):
                cursor.execute(
                    "INSERT INTO lesson_tasks (lesson_id, task_id, task_order) VALUES (?, ?, ?)",
                    (lesson_id, task_id, order)
                )
            
            conn.commit()
            flash("Урок успешно обновлен", "success")
            
            cursor.execute("SELECT class_id FROM lessons WHERE id = ?", (lesson_id,))
            class_id = cursor.fetchone()['class_id']
            return redirect(url_for('class_lessons', class_id=class_id))
        
        cursor.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,))
        lesson = cursor.fetchone()
        
        if not lesson:
            flash("Урок не найден", "error")
            return redirect(url_for('admin_dashboard'))
        
        cursor.execute("""
            SELECT t.id, t.title, t.content, lt.task_order
            FROM tasks t
            JOIN lesson_tasks lt ON t.id = lt.task_id
            WHERE lt.lesson_id = ?
            ORDER BY lt.task_order
        """, (lesson_id,))
        lesson_tasks = cursor.fetchall()
        
        cursor.execute("SELECT id, title, content FROM tasks")
        all_tasks = cursor.fetchall()
        
        return render_template('edit_lesson.html',
                           lesson=lesson,
                           lesson_tasks=lesson_tasks,
                           all_tasks=all_tasks)
    except Error as e:
        conn.rollback()
        flash(f"Ошибка базы данных: {e}", "error")
        return redirect(url_for('admin_dashboard'))
    finally:
        conn.close()

@app.route('/admin/lessons/<int:lesson_id>/delete', methods=['POST'])
def delete_lesson(lesson_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    
    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        cursor.execute("SELECT class_id FROM lessons WHERE id = ?", (lesson_id,))
        lesson = cursor.fetchone()
        
        if not lesson:
            flash("Урок не найден", "error")
            return redirect(url_for('admin_dashboard'))
        
        class_id = lesson['class_id']
        
        cursor.execute("DELETE FROM lesson_tasks WHERE lesson_id = ?", (lesson_id,))
        cursor.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
        
        conn.commit()
        flash("Урок успешно удален", "success")
        return redirect(url_for('class_lessons', class_id=class_id))
    except Error as e:
        conn.rollback()
        flash(f"Ошибка при удалении урока: {e}", "error")
        return redirect(url_for('admin_dashboard'))
    finally:
        conn.close()

@app.route('/get_students')
def get_students():
    class_id = request.args.get('class_id')
    conn = create_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM students WHERE class_id = ?", (class_id,))
        students = [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]
        return jsonify({'students': students})
    except Error as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route("/student/<int:student_id>/lessons")
def student_lessons(student_id):
    if 'student_id' not in session or session['student_id'] != student_id:
        return redirect(url_for('index'))
    
    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Получаем информацию об ученике
        cursor.execute("SELECT name, class_id FROM students WHERE id = ?", (student_id,))
        student = cursor.fetchone()
        if not student:
            flash("Ученик не найден", "error")
            return redirect(url_for("index"))
        
        # Получаем обычные уроки
        cursor.execute("""
            SELECT id, name, created_at, 'lesson' as type, NULL as status
            FROM lessons 
            WHERE class_id = ?
            ORDER BY created_at DESC
        """, (student['class_id'],))
        lessons = cursor.fetchall()
        
        # Получаем дуэли для класса
        cursor.execute("""
            SELECT id as duel_id, name, created_at, 'duel' as type, status
            FROM math_duels
            WHERE class_id = ?
            ORDER BY created_at DESC
        """, (student['class_id'],))
        duels = cursor.fetchall()
        
        # Объединяем уроки и дуэли
        all_lessons = lessons + [
            {**duel, 'id': duel['duel_id']} for duel in duels
        ]
        
        return render_template("student_lessons.html",
                            student_id=student_id,
                            student_name=student['name'],
                            lessons=all_lessons)
    except Error as e:
        flash(f"Ошибка базы данных: {str(e)}", "error")
        return redirect(url_for("index"))
    finally:
        conn.close()

@app.route('/student/<int:student_id>/lesson/<int:lesson_id>')
def show_student_tasks(student_id, lesson_id):
    conn = create_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM students WHERE id = ?", (student_id,))
        student = cursor.fetchone()
        if not student:
            flash("Ученик не найден", "error")
            return redirect(url_for('index'))
        
        cursor.execute("SELECT name FROM lessons WHERE id = ?", (lesson_id,))
        lesson = cursor.fetchone()
        if not lesson:
            flash("Урок не найден", "error")
            return redirect(url_for('student_lessons', student_id=student_id))
        
        cursor.execute("""
            SELECT t.id, t.title, t.content, t.answer_formula, 
                   COALESCE(lt.task_order, 1) as task_order
            FROM tasks t
            JOIN lesson_tasks lt ON t.id = lt.task_id
            WHERE lt.lesson_id = ?
            ORDER BY COALESCE(lt.task_order, 1)
        """, (lesson_id,))
        
        tasks = []
        for task in cursor.fetchall():
            a = random.randint(1, 10)
            b = random.randint(1, 10)
            
            text = task['content'].replace('{A}', str(a)).replace('{B}', str(b))
            example_answer = eval(task['answer_formula'].replace('{A}', str(a)).replace('{B}', str(b)))
            
            tasks.append({
                'id': task['id'],
                'text': text,
                'answer_formula': task['answer_formula'],
                'example_answer': example_answer,
                'order': task['task_order']
            })
        
        return render_template('student_tasks.html',
                            student_id=student_id,
                            student_name=student[0],
                            lesson_id=lesson_id,
                            lesson_name=lesson[0],
                            tasks=tasks)
    except Error as e:
        flash(f"Ошибка базы данных: {e}", "error")
        return redirect(url_for('student_lessons', student_id=student_id))
    finally:
        conn.close()

@app.route('/admin/tasks')
def admin_tasks():
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    
    class_group = request.args.get('class_group', type=int)
    tasks = get_all_tasks(class_group)
    
    return render_template('admin_tasks.html', 
                         tasks=tasks,
                         active_tab=class_group)

@app.route('/admin/tasks/create', methods=['GET', 'POST'])
def create_task():
    if not session.get('is_admin'):
        return redirect(url_for('index'))

    if request.method == 'POST':
        try:
            task_data = {
                'title': request.form['title'],
                'content': request.form['content'],
                'answer_formula': request.form.get('answer_formula', ''),
                'difficulty': int(request.form.get('difficulty', 2)),
                'class_group': int(request.form['class_group'])
            }

            if 'task_file' in request.files:
                file = request.files['task_file']
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    task_data['content'] += f'<br><img src="/static/uploads/{filename}">'

            task_id = db_create_task(**task_data)

            hint_content = request.form.get('hint_content', '')
            if hint_content:
                if 'hint_file' in request.files:
                    file = request.files['hint_file']
                    if file and allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                        hint_content += f'<br><img src="/static/uploads/{filename}">'
                
                db_create_hint(task_id, hint_content)

            flash('Задание успешно создано!', 'success')
            return redirect(url_for('admin_tasks'))

        except Exception as e:
            flash(f'Ошибка: {str(e)}', 'error')

    return render_template('admin_create_task.html')

@app.route('/admin/tasks/<int:task_id>/edit', methods=['GET', 'POST'])
def edit_task(task_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    
    task = get_task_by_id(task_id)
    if not task:
        flash("Задание не найдено", "error")
        return redirect(url_for('admin_tasks'))
    
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        answer_formula = request.form.get('answer_formula')
        difficulty = request.form.get('difficulty', 2, type=int)
        class_group = request.form.get('class_group', 5, type=int)
        
        if not title or not content:
            flash("Заполните все обязательные поля", "error")
            return redirect(url_for('edit_task', task_id=task_id))
        
        if update_task(task_id, title, content, answer_formula, difficulty, class_group):
            flash("Задание успешно обновлено", "success")
            return redirect(url_for('admin_tasks'))
        else:
            flash("Ошибка при обновлении задания", "error")
    
    return render_template('admin_create_task.html', task=task)

@app.route('/admin/tasks/<int:task_id>/delete', methods=['POST'])
def delete_task_route(task_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    
    if delete_task(task_id):
        flash("Задание успешно удалено", "success")
    else:
        flash("Ошибка при удалении задания", "error")
    
    return redirect(url_for('admin_tasks'))

@app.route('/admin/class/<int:class_id>/create_duel', methods=['GET', 'POST'])
def create_duel(class_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))

    if request.method == 'POST':
        name = request.form.get('name')
        if not name:
            flash("Введите название дуэли", "error")
            return redirect(url_for('create_duel', class_id=class_id))

        conn = create_connection()
        try:
            cursor = conn.cursor()
            
            # Получаем всех учеников класса
            cursor.execute("SELECT id FROM students WHERE class_id = ?", (class_id,))
            participants = [row['id'] for row in cursor.fetchall()]
            
            if len(participants) < 2:
                flash("Для дуэли нужно минимум 2 участника", "error")
                return redirect(url_for('create_duel', class_id=class_id))
            
            # Создаем дуэль
            cursor.execute(
                "INSERT INTO math_duels (class_id, name) VALUES (?, ?)",
                (class_id, name)
            )
            duel_id = cursor.lastrowid
            
            # Добавляем участников
            for student_id in participants:
                cursor.execute(
                    "INSERT INTO duel_participants (duel_id, student_id) VALUES (?, ?)",
                    (duel_id, student_id)
                )
            
            # Определяем стадию
            stage = determine_stage(len(participants))
            print(f"Создаем дуэль с {len(participants)} участниками, начальная стадия: {stage}")
            
            # Формируем пары для первого раунда
            random.shuffle(participants)
            for i in range(0, len(participants), 2):
                if i+1 < len(participants):
                    # Для обычного матча с двумя участниками
                    cursor.execute(
                        """INSERT INTO duel_matches 
                        (duel_id, round_number, bracket_type, student1_id, student2_id) 
                        VALUES (?, ?, ?, ?, ?)""",
                        (duel_id, 1, stage, participants[i], participants[i+1])
                    )
                else:
                    # Для автоматического прохода при нечетном количестве
                    cursor.execute(
                        """INSERT INTO duel_matches 
                        (duel_id, round_number, bracket_type, student1_id, winner_id) 
                        VALUES (?, ?, ?, ?, ?)""",
                        (duel_id, 1, stage, participants[i], participants[i])
                    )
            
            conn.commit()
            flash("Дуэль успешно создана!", "success")
            return redirect(url_for('view_duel', duel_id=duel_id))
        except Error as e:
            conn.rollback()
            flash(f"Ошибка при создании дуэли: {str(e)}", "error")
            return redirect(url_for('class_lessons', class_id=class_id))
        finally:
            conn.close()

    return render_template('create_duel.html', class_id=class_id)


@app.route('/duel/<int:duel_id>')
def view_duel(duel_id):
    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Получаем информацию о дуэли
        cursor.execute("""
            SELECT md.*, c.name as class_name 
            FROM math_duels md
            JOIN classes c ON md.class_id = c.id
            WHERE md.id = ?
        """, (duel_id,))
        duel = cursor.fetchone()

        # Получаем все матчи
        cursor.execute("""
            SELECT dm.*, 
                   s1.name as student1_name, 
                   s2.name as student2_name,
                   t.title as task_title
            FROM duel_matches dm
            LEFT JOIN students s1 ON dm.student1_id = s1.id
            LEFT JOIN students s2 ON dm.student2_id = s2.id
            LEFT JOIN tasks t ON dm.task_id = t.id
            WHERE dm.duel_id = ?
            ORDER BY dm.round_number, dm.bracket_type
        """, (duel_id,))
        matches = cursor.fetchall()

        # Получаем участников для таблицы лидеров
        cursor.execute("""
            SELECT s.id, s.name, dp.points, 
                   (SELECT COUNT(*) FROM duel_matches 
                    WHERE (student1_id = s.id OR student2_id = s.id) 
                    AND winner_id = s.id) as wins
            FROM duel_participants dp
            JOIN students s ON dp.student_id = s.id
            WHERE dp.duel_id = ?
            ORDER BY dp.points DESC
        """, (duel_id,))
        participants = cursor.fetchall()

        # Получаем все задания для выбора
        cursor.execute("SELECT id, title FROM tasks")
        all_tasks = cursor.fetchall()

        return render_template('view_duel.html',
                            duel=duel,
                            matches=matches,
                            participants=participants,
                            all_tasks=all_tasks)

    except Error as e:
        flash(f"Ошибка базы данных: {e}", "error")
        return redirect(url_for('admin_dashboard'))
    finally:
        conn.close()

@app.route('/duel/<int:duel_id>/generate_round', methods=['POST'])
def generate_round(duel_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))

    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Получаем текущий раунд
        cursor.execute("SELECT current_round FROM math_duels WHERE id = ?", (duel_id,))
        current_round = cursor.fetchone()['current_round']
        new_round = current_round + 1
        
        # Получаем победителей предыдущего раунда
        cursor.execute("""
            SELECT winner_id FROM duel_matches 
            WHERE duel_id = ? AND round_number = ? AND winner_id IS NOT NULL
        """, (duel_id, current_round))
        winners = [row['winner_id'] for row in cursor.fetchall()]
        
        if len(winners) < 2:
            flash("Недостаточно победителей для следующего раунда", "error")
            return redirect(url_for('view_duel', duel_id=duel_id))
        
        # Определяем следующую стадию
        next_stage = {
            '1/64': '1/32',
            '1/32': '1/16',
            '1/16': '1/8',
            '1/8': '1/4',
            '1/4': '1/2',
            '1/2': 'final'
        }.get(get_current_stage(duel_id, current_round), 'final')
        
        # Создаем матчи нового раунда
        for i in range(0, len(winners), 2):
            if i+1 < len(winners):
                cursor.execute("""
                    INSERT INTO duel_matches 
                    (duel_id, round_number, bracket_type, student1_id, student2_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (duel_id, new_round, next_stage, winners[i], winners[i+1]))
        
        # Обновляем текущий раунд
        cursor.execute("""
            UPDATE math_duels 
            SET current_round = ? 
            WHERE id = ?
        """, (new_round, duel_id))
        
        conn.commit()
        flash(f"Раунд {new_round} ({next_stage}) сгенерирован!", "success")
    except Error as e:
        conn.rollback()
        flash(f"Ошибка при генерации раунда: {str(e)}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('view_duel', duel_id=duel_id))

def get_current_stage(duel_id, round_number):
    """Получает текущую стадию турнира"""
    conn = create_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT bracket_type FROM duel_matches 
            WHERE duel_id = ? AND round_number = ?
            LIMIT 1
        """, (duel_id, round_number))
        result = cursor.fetchone()
        return result['bracket_type'] if result else '1/2'
    finally:
        conn.close()

@app.route('/duel/<int:duel_id>/match/<int:match_id>/set_task', methods=['POST'])
def set_duel_match_task(duel_id, match_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))

    task_id = request.form.get('task_id')
    if not task_id:
        flash("Выберите задание", "error")
        return redirect(url_for('view_duel', duel_id=duel_id))

    conn = create_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE duel_matches 
            SET task_id = ? 
            WHERE id = ?
        """, (task_id, match_id))
        conn.commit()
        flash("Задание добавлено к матчу", "success")
    except Error as e:
        conn.rollback()
        flash(f"Ошибка: {e}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('view_duel', duel_id=duel_id))

@app.route('/duel/<int:duel_id>/match/<int:match_id>/set_winner', methods=['POST'])
def set_duel_match_winner(duel_id, match_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))

    winner_id = request.form.get('winner_id')
    if not winner_id:
        flash("Выберите победителя", "error")
        return redirect(url_for('view_duel', duel_id=duel_id))

    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Обновляем матч
        cursor.execute("""
            UPDATE duel_matches 
            SET winner_id = ? 
            WHERE id = ?
        """, (winner_id, match_id))

        # Добавляем очки победителю
        cursor.execute("""
            UPDATE duel_participants 
            SET points = points + 3 
            WHERE duel_id = ? AND student_id = ?
        """, (duel_id, winner_id))

        conn.commit()
        flash("Победитель сохранен", "success")
    except Error as e:
        conn.rollback()
        flash(f"Ошибка: {e}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('view_duel', duel_id=duel_id))

@app.route('/duel/<int:duel_id>/finish', methods=['POST'])
def finish_duel(duel_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))

    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Определяем финалистов
        cursor.execute("""
            SELECT winner_id FROM duel_matches 
            WHERE duel_id = ? AND bracket_type = 'upper'
            ORDER BY round_number DESC LIMIT 1
        """, (duel_id,))
        finalist1 = cursor.fetchone()
        
        cursor.execute("""
            SELECT winner_id FROM duel_matches 
            WHERE duel_id = ? AND bracket_type = 'lower'
            ORDER BY round_number DESC LIMIT 1
        """, (duel_id,))
        finalist2 = cursor.fetchone()
        
        if finalist1 and finalist2:
            # Создаем финальный матч
            cursor.execute("""
                INSERT INTO duel_matches 
                (duel_id, round_number, bracket_type, student1_id, student2_id)
                VALUES (?, ?, 'final', ?, ?)
            """, (duel_id, 999, finalist1['winner_id'], finalist2['winner_id']))
        
        # Помечаем дуэль как завершенную
        cursor.execute("""
            UPDATE math_duels 
            SET status = 'finished' 
            WHERE id = ?
        """, (duel_id,))
        
        conn.commit()
        flash("Дуэль завершена!", "success")
    except Error as e:
        conn.rollback()
        flash(f"Ошибка: {e}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('view_duel', duel_id=duel_id))

@app.route('/admin/duels/<int:duel_id>/delete', methods=['POST'])
def delete_duel(duel_id):
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    
    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Получаем class_id перед удалением для редиректа
        cursor.execute("SELECT class_id FROM math_duels WHERE id = ?", (duel_id,))
        class_id = cursor.fetchone()['class_id']
        
        # Удаляем связанные записи
        cursor.execute("DELETE FROM duel_matches WHERE duel_id = ?", (duel_id,))
        cursor.execute("DELETE FROM duel_participants WHERE duel_id = ?", (duel_id,))
        cursor.execute("DELETE FROM math_duels WHERE id = ?", (duel_id,))
        
        conn.commit()
        flash("Дуэль успешно удалена", "success")
    except Error as e:
        conn.rollback()
        flash(f"Ошибка при удалении дуэли: {e}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('class_lessons', class_id=class_id))


@app.route('/duel/<int:duel_id>/round/<int:round_number>')
def duel_round(duel_id, round_number):
    if 'student_id' not in session:
        return redirect(url_for('index'))
    
    conn = create_connection()
    cursor = conn.cursor()
    
    # Получаем матч ученика
    cursor.execute("""
        SELECT * FROM duel_matches 
        WHERE duel_id = ? AND round_number = ? 
        AND (student1_id = ? OR student2_id = ?)
    """, (duel_id, round_number, session['student_id'], session['student_id']))
    match = cursor.fetchone()
    
    # Получаем противника
    opponent_id = match['student1_id'] if match['student1_id'] != session['student_id'] else match['student2_id']
    cursor.execute("SELECT name FROM students WHERE id = ?", (opponent_id,))
    opponent = cursor.fetchone()['name']
    
    # Получаем задания для раунда
    cursor.execute("""
        SELECT t.* FROM tasks t
        JOIN duel_round_tasks drt ON t.id = drt.task_id
        WHERE drt.duel_id = ? AND drt.round_number = ?
    """, (duel_id, round_number))
    tasks = cursor.fetchall()
    
    return render_template(
        'duel_round.html',
        duel_id=duel_id,
        round_number=round_number,
        opponent=opponent,
        tasks=tasks
    )


@app.route('/duel/<int:duel_id>/add_tasks', methods=['POST'])
def add_tasks_to_round(duel_id):
    if not session.get('is_admin'):
        abort(403)
    
    round_number = request.form.get('round_number')
    task_ids = request.form.getlist('task_ids')
    
    conn = create_connection()
    try:
        cursor = conn.cursor()
        for task_id in task_ids:
            cursor.execute(
                "INSERT INTO duel_round_tasks (duel_id, round_number, task_id) VALUES (?, ?, ?)",
                (duel_id, round_number, task_id)
            )
        conn.commit()
        flash("Задания успешно добавлены для раунда!", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {str(e)}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('view_duel', duel_id=duel_id))

@app.route('/duel/<int:duel_id>/generate_first_round', methods=['POST'])
def generate_first_round(duel_id):
    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Получаем участников и перемешиваем
        cursor.execute("SELECT student_id FROM duel_participants WHERE duel_id = ?", (duel_id,))
        participants = [row['student_id'] for row in cursor.fetchall()]
        random.shuffle(participants)
        
        # Определяем стадию
        stage = determine_stage(len(participants))
        
        # Создаем матчи
        for i in range(0, len(participants), 2):
            if i+1 < len(participants):
                cursor.execute("""
                    INSERT INTO duel_matches 
                    (duel_id, round_number, bracket_type, student1_id, student2_id)
                    VALUES (?, 1, ?, ?, ?)
                """, (duel_id, stage, participants[i], participants[i+1]))
            else:
                # Автоматический проход при нечетном количестве
                cursor.execute("""
                    INSERT INTO duel_matches 
                    (duel_id, round_number, bracket_type, student1_id, winner_id)
                    VALUES (?, 1, ?, ?, ?)
                """, (duel_id, stage, participants[i], participants[i]))
        
        # Обновляем статус дуэли
        cursor.execute("UPDATE math_duels SET current_round = 1 WHERE id = ?", (duel_id,))
        conn.commit()
        flash("Первый раунд успешно создан!", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {str(e)}", "error")
    finally:
        conn.close()
    return redirect(url_for('view_duel', duel_id=duel_id))


@app.route('/duel/<int:duel_id>/match/<int:match_id>')
def duel_match(duel_id, match_id):
    if 'student_id' not in session:
        return redirect(url_for('index'))

    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Проверяем, относится ли ученик к этому матчу
        cursor.execute("""
            SELECT * FROM duel_matches
            WHERE id = ? AND (student1_id = ? OR student2_id = ?)
        """, (match_id, session['student_id'], session['student_id']))
        match = cursor.fetchone()
        
        if not match:
            flash("Вы не участвуете в этом матче", "error")
            return redirect(url_for('student_lessons', student_id=session['student_id']))
        
        # Получаем задание
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (match['task_id'],))
        task = cursor.fetchone()
        
        if not task:
            flash("Задание для этого матча еще не назначено", "error")
            return redirect(url_for('student_lessons', student_id=session['student_id']))
        
        # Получаем имя противника
        opponent_id = match['student1_id'] if match['student1_id'] != session['student_id'] else match['student2_id']
        cursor.execute("SELECT name FROM students WHERE id = ?", (opponent_id,))
        opponent = cursor.fetchone()['name']
        
        return render_template('duel_match.html',
                            duel_id=duel_id,
                            match_id=match_id,
                            task=task,
                            opponent=opponent)
    
    except Error as e:
        flash(f"Ошибка базы данных: {e}", "error")
        return redirect(url_for('index'))
    finally:
        conn.close()

@app.route('/duel/<int:duel_id>/match/<int:match_id>/submit', methods=['POST'])
def submit_duel_answer_handler(duel_id, match_id):
    if 'student_id' not in session:
        return redirect(url_for('index'))

    answer = request.form.get('answer')
    if not answer:
        flash("Введите ответ", "error")
        return redirect(url_for('duel_match', duel_id=duel_id, match_id=match_id))

    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Получаем задание и правильный ответ
        cursor.execute("""
            SELECT t.answer_formula FROM tasks t
            JOIN duel_matches dm ON t.id = dm.task_id
            WHERE dm.id = ?
        """, (match_id,))
        task = cursor.fetchone()
        
        if not task:
            flash("Задание не найдено", "error")
            return redirect(url_for('student_lessons', student_id=session['student_id']))
        
        # Проверяем ответ (простая проверка для примера)
        correct_answer = str(eval(task['answer_formula']))
        is_correct = answer.strip() == correct_answer
        
        # Сохраняем результат
        cursor.execute("""
            INSERT INTO duel_answers 
            (duel_id, match_id, student_id, answer, is_correct)
            VALUES (?, ?, ?, ?, ?)
        """, (duel_id, match_id, session['student_id'], answer, is_correct))
        
        conn.commit()
        
        if is_correct:
            flash("Правильно! Ответ засчитан", "success")
        else:
            flash(f"Неправильно. Правильный ответ: {correct_answer}", "error")
            
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка при проверке ответа: {str(e)}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('duel_match', duel_id=duel_id, match_id=match_id))


# Добавление шаблонов заданий
@app.route('/duel/<int:duel_id>/add_templates/<int:round_number>', methods=['GET', 'POST'])
def add_duel_templates(duel_id, round_number):
    if not session.get('is_admin'):
        abort(403)
    
    conn = create_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM math_duels WHERE id = ?", (duel_id,))
    duel = cursor.fetchone()
    
    if request.method == 'POST':
        templates = request.form.getlist('templates[]')
        answer_formulas = request.form.getlist('answer_formulas[]')
        
        try:
            # Удаляем старые шаблоны для этого раунда
            cursor.execute("DELETE FROM duel_task_templates WHERE duel_id = ? AND round_number = ?", 
                         (duel_id, round_number))
            
            # Сохраняем новые шаблоны
            for template, formula in zip(templates, answer_formulas):
                cursor.execute("""
                    INSERT INTO duel_task_templates (duel_id, round_number, template, answer_formula)
                    VALUES (?, ?, ?, ?)
                """, (duel_id, round_number, template, formula))
            
            # Генерируем задания для всех матчей раунда
            generate_tasks_for_round(duel_id, round_number)
            
            conn.commit()
            flash("Шаблоны заданий успешно сохранены и применены", "success")
            return redirect(url_for('view_duel', duel_id=duel_id))
        except Exception as e:
            conn.rollback()
            flash(f"Ошибка: {str(e)}", "error")
    
    return render_template('add_duel_tasks.html', duel=duel, round_number=round_number)

# Генерация заданий для всех матчей раунда


# Маршрут для выполнения задания учеником
@app.route('/duel/<int:duel_id>/match/<int:match_id>/view')  # Добавили /view в URL
def view_duel_match(duel_id, match_id):
    if 'student_id' not in session:
        return redirect(url_for('index'))
    
    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Проверяем доступ ученика к матчу
        cursor.execute("""
            SELECT dm.*, s1.name as student1_name, s2.name as student2_name,
                   md.name as duel_name, md.current_round
            FROM duel_matches dm
            JOIN math_duels md ON dm.duel_id = md.id
            LEFT JOIN students s1 ON dm.student1_id = s1.id
            LEFT JOIN students s2 ON dm.student2_id = s2.id
            WHERE dm.id = ? AND (dm.student1_id = ? OR dm.student2_id = ?)
        """, (match_id, session['student_id'], session['student_id']))
        match = cursor.fetchone()
        
        if not match:
            flash("Вы не участвуете в этом матче", "error")
            return redirect(url_for('student_lessons', student_id=session['student_id']))
        
        # Получаем противника
        opponent_id = match['student1_id'] if match['student1_id'] != session['student_id'] else match['student2_id']
        cursor.execute("SELECT name FROM students WHERE id = ?", (opponent_id,))
        opponent = cursor.fetchone()['name']
        
        # Разбиваем задания на список
        tasks = match['generated_task'].split('\n') if match['generated_task'] else []
        answers = match['correct_answer'].split('\n') if match['correct_answer'] else []
        
        return render_template('duel_match.html',
                            duel_id=duel_id,
                            match_id=match_id,
                            duel_name=match['duel_name'],
                            round_number=match['current_round'],
                            opponent=opponent,
                            tasks=zip(tasks, answers),
                            student_id=session['student_id'])
    
    except Exception as e:
        flash(f"Ошибка: {str(e)}", "error")
        return redirect(url_for('index'))
    finally:
        conn.close()


@app.route('/submit_duel_answers', methods=['POST'])
def submit_duel_answers():
    if 'student_id' not in session:
        return redirect(url_for('index'))

    # Для отладки
    print("Полученные данные:", request.form)
    
    # Получаем данные по НОВОМУ формату
    duel_id = request.form.get('duel_id')
    match_id = request.form.get('match_id')
    user_answer = request.form.get('answer')          # Теперь просто 'answer'
    correct_answer = request.form.get('correct_answer') # И 'correct_answer'
    
    # Проверка наличия всех данных
    missing = []
    if not duel_id: missing.append('duel_id')
    if not match_id: missing.append('match_id')
    if not user_answer: missing.append('answer')
    if not correct_answer: missing.append('correct_answer')
    
    if missing:
        flash(f"Отсутствуют обязательные данные: {', '.join(missing)}", "error")
        return redirect(url_for('student_duel_view', duel_id=duel_id))
    
    # Проверка ответа
    try:
        is_correct = float(user_answer) == float(correct_answer)
    except ValueError:
        is_correct = False
    
    # Обновление БД
    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        if is_correct:
            cursor.execute("""
                UPDATE duel_matches 
                SET winner_id = ?
                WHERE id = ? AND duel_id = ?
            """, (session['student_id'], match_id, duel_id))
            
            cursor.execute("""
                UPDATE duel_participants
                SET points = points + 1
                WHERE duel_id = ? AND student_id = ?
            """, (duel_id, session['student_id']))
            
            flash("✅ Ответ правильный! +1 балл", "success")
        else:
            flash(f"❌ Неверно. Правильный ответ: {correct_answer}", "error")
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка базы данных: {str(e)}", "error")
    finally:
        conn.close()

    return redirect(url_for('student_duel_view', duel_id=duel_id))

@app.route('/duel/<int:duel_id>/create_templates/<int:round_number>', methods=['GET', 'POST'])
def create_round_templates(duel_id, round_number):
    if not session.get('is_admin'):
        abort(403)
    
    conn = create_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM math_duels WHERE id = ?", (duel_id,))
        duel = cursor.fetchone()
        
        if request.method == 'POST':
            templates = request.form.getlist('templates[]')
            answer_formulas = request.form.getlist('answer_formulas[]')
            
            # Удаляем старые шаблоны
            cursor.execute("DELETE FROM duel_templates WHERE duel_id = ? AND round_number = ?", 
                         (duel_id, round_number))
            
            # Сохраняем новые
            for template, formula in zip(templates, answer_formulas):
                if template.strip() and formula.strip():
                    cursor.execute("""
                        INSERT INTO duel_templates (duel_id, round_number, template, answer_formula)
                        VALUES (?, ?, ?, ?)
                    """, (duel_id, round_number, template.strip(), formula.strip()))
            
            conn.commit()
            flash("Шаблоны успешно сохранены!", "success")
            
            # Предлагаем применить шаблоны
            cursor.execute("SELECT COUNT(*) FROM duel_matches WHERE duel_id = ? AND round_number = ?", 
                         (duel_id, round_number))
            match_count = cursor.fetchone()[0]
            
            if match_count > 0:
                return render_template('add_duel_tasks.html', 
                                    duel=duel,
                                    duel_id=duel_id,
                                    round_number=round_number,
                                    show_apply_button=True)
            
            return redirect(url_for('view_duel', duel_id=duel_id))
        
        return render_template('add_duel_tasks.html', 
                            duel=duel,
                            duel_id=duel_id,
                            round_number=round_number)
    
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {str(e)}", "error")
        return redirect(url_for('view_duel', duel_id=duel_id))
    finally:
        conn.close()



@app.route('/duel/<int:duel_id>/generate_tasks', methods=['POST'])
def generate_tasks(duel_id):
    if not session.get('is_admin'):
        abort(403)
    
    try:
        conn = create_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT current_round FROM math_duels WHERE id = ?", (duel_id,))
        round_number = cursor.fetchone()['current_round']
        
        generate_tasks_for_round(duel_id, round_number)
        flash("Задания успешно сгенерированы для всех пар раунда!", "success")
    except Exception as e:
        flash(f"Ошибка: {str(e)}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('view_duel', duel_id=duel_id))

@app.route('/duel/<int:duel_id>/apply_templates', methods=['POST'])
def apply_templates_to_round(duel_id):
    if not session.get('is_admin'):
        abort(403)
    
    conn = None
    try:
        conn = create_connection()
        cursor = conn.cursor()
        
        # Получаем текущий раунд
        cursor.execute("SELECT current_round FROM math_duels WHERE id = ?", (duel_id,))
        current_round = cursor.fetchone()['current_round']
        print(f"Текущий раунд: {current_round}")
        
        # Получаем шаблоны для этого раунда
        cursor.execute("""
            SELECT template, answer_formula 
            FROM duel_templates 
            WHERE duel_id = ? AND round_number = ?
        """, (duel_id, current_round))
        templates = cursor.fetchall()
        
        if not templates:
            flash("Нет шаблонов заданий для этого раунда", "error")
            return redirect(url_for('view_duel', duel_id=duel_id))
        
        # Получаем все матчи текущего раунда
        cursor.execute("""
            SELECT id FROM duel_matches 
            WHERE duel_id = ? AND round_number = ?
        """, (duel_id, current_round))
        matches = cursor.fetchall()
        
        # Для каждого матча генерируем задания из шаблонов
        for match in matches:
            tasks = []
            answers = []
            
            for template in templates:
                # Генерируем уникальные параметры
                params = {}
                for param in re.findall(r'\{([A-Z]+)\}', template['template']):
                    params[param] = random.randint(1, 10)
                
                # Подставляем параметры
                task_text = template['template']
                for param, value in params.items():
                    task_text = task_text.replace(f'{{{param}}}', str(value))
                
                # Вычисляем ответ (с обработкой ошибок)
                try:
                    # Подготавливаем формулу
                    formula = template['answer_formula']
                    for p in params:
                        formula = formula.replace(f'{{{p}}}', str(params[p]))
                    
                    answer = str(eval(formula))
                except Exception as e:
                    flash(f"Ошибка в формуле '{template['answer_formula']}': {str(e)}", "error")
                    continue
                
                tasks.append(task_text)
                answers.append(answer)
            
            # Обновляем матч
            cursor.execute("""
                UPDATE duel_matches 
                SET generated_task = ?, correct_answer = ?
                WHERE id = ?
            """, ('\n'.join(tasks), '\n'.join(answers), match['id']))
        
        # Проверка результатов
        cursor.execute("""
            SELECT id, generated_task, correct_answer 
            FROM duel_matches 
            WHERE duel_id = ? AND round_number = ?
        """, (duel_id, current_round))
        
        print("Проверка заданий после генерации:")
        for row in cursor.fetchall():
            print(f"Матч {row['id']}: Задание - {row['generated_task']}, Ответ - {row['correct_answer']}")
        
        conn.commit()
        flash("Шаблонные задания успешно применены ко всем парам раунда!", "success")
        return redirect(url_for('view_duel', duel_id=duel_id))
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Ошибка: {str(e)}")
        flash(f"Ошибка при применении шаблонов: {str(e)}", "error")
        return redirect(url_for('view_duel', duel_id=duel_id))
    finally:
        if conn:
            conn.close()
    

@app.route('/duel/<int:duel_id>/apply_templates', methods=['POST'])
def apply_templates_to_all_matches(duel_id):
    if not session.get('is_admin'):
        abort(403)

    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Получаем текущий раунд
        cursor.execute("SELECT current_round FROM math_duels WHERE id = ?", (duel_id,))
        current_round = cursor.fetchone()['current_round']
        
        # Получаем шаблоны для этого раунда
        cursor.execute("""
            SELECT template, answer_formula 
            FROM duel_templates 
            WHERE duel_id = ? AND round_number = ?
        """, (duel_id, current_round))
        templates = cursor.fetchall()
        
        if not templates:
            flash("Нет шаблонов заданий для текущего раунда", "error")
            return redirect(url_for('view_duel', duel_id=duel_id))
        
        # Получаем все активные матчи текущего раунда
        cursor.execute("""
            SELECT id FROM duel_matches 
            WHERE duel_id = ? AND round_number = ? AND winner_id IS NULL
        """, (duel_id, current_round))
        matches = cursor.fetchall()
        
        # Для каждого матча генерируем задания
        for match in matches:
            tasks = []
            answers = []
            
            for template in templates:
                # Генерируем уникальные параметры для каждого задания
                params = {}
                for param in re.findall(r'\{([A-Z]+)\}', template['template']):
                    params[param] = random.randint(1, 10)
                
                # Подставляем параметры в шаблон
                task_text = template['template']
                for param, value in params.items():
                    task_text = task_text.replace(f'{{{param}}}', str(value))
                
                # Вычисляем ответ
                try:
                    formula = template['answer_formula']
                    for p in params:
                        formula = formula.replace(f'{{{p}}}', str(params[p]))
                    answer = str(eval(formula))
                except Exception as e:
                    flash(f"Ошибка в формуле: {str(e)}", "error")
                    continue
                
                tasks.append(task_text)
                answers.append(answer)
            
            # Обновляем матч
            cursor.execute("""
                UPDATE duel_matches 
                SET generated_task = ?, correct_answer = ?
                WHERE id = ?
            """, ('\n'.join(tasks), '\n'.join(answers), match['id']))
        
        conn.commit()
        flash(f"Шаблоны успешно применены к {len(matches)} парам!", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {str(e)}", "error")
    finally:
        conn.close()
    
    return redirect(url_for('view_duel', duel_id=duel_id))

@app.route('/duel/<int:duel_id>/generate_tasks', methods=['POST'])
def generate_tasks_for_round(duel_id):
    """Новая версия функции для генерации заданий"""
    return apply_templates_to_all_matches(duel_id)

@app.route('/duel/<int:duel_id>/select_round', methods=['GET', 'POST'])
def select_round_for_templates(duel_id):
    if not session.get('is_admin'):
        abort(403)
    
    if request.method == 'POST':
        round_number = request.form.get('round_number')
        return redirect(url_for('create_round_templates', 
                             duel_id=duel_id, 
                             round_number=round_number))
    
    # GET запрос - показать форму выбора раунда
    conn = create_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT current_round FROM math_duels WHERE id = ?", (duel_id,))
    current_round = cursor.fetchone()['current_round']
    conn.close()
    
    return render_template('select_round.html',
                         duel_id=duel_id,
                         current_round=current_round)


@app.route('/student_duel/<int:duel_id>')
def student_duel_view(duel_id):
    if 'student_id' not in session:
        return redirect(url_for('index'))
    
    conn = create_connection()
    try:
        cursor = conn.cursor()
        
        # Получаем информацию о дуэли
        cursor.execute("""
            SELECT id, name, current_round 
            FROM math_duels 
            WHERE id = ?
        """, (duel_id,))
        duel = cursor.fetchone()
        
        if not duel:
            flash("Дуэль не найдена", "error")
            return redirect(url_for('student_lessons', student_id=session['student_id']))
        
        # Получаем текущего ученика
        cursor.execute("""
            SELECT id, name 
            FROM students 
            WHERE id = ?
        """, (session['student_id'],))
        current_student = cursor.fetchone()
        
        # Получаем текущий матч ученика
        cursor.execute("""
            SELECT dm.*, 
                   s1.name as student1_name, 
                   s2.name as student2_name,
                   t.title as task_title,
                   dm.generated_task,
                   dm.correct_answer
            FROM duel_matches dm
            LEFT JOIN students s1 ON dm.student1_id = s1.id
            LEFT JOIN students s2 ON dm.student2_id = s2.id
            LEFT JOIN tasks t ON dm.task_id = t.id
            WHERE dm.duel_id = ? 
            AND (dm.student1_id = ? OR dm.student2_id = ?)
            ORDER BY dm.round_number DESC
            LIMIT 1
        """, (duel_id, session['student_id'], session['student_id']))
        match = cursor.fetchone()
        
        if not match:
            flash("У вас нет активных матчей в этой дуэли", "warning")
            return redirect(url_for('student_lessons', student_id=session['student_id']))
        
        # Определяем противника
        opponent_id = match['student1_id'] if match['student1_id'] != session['student_id'] else match['student2_id']
        cursor.execute("SELECT id, name FROM students WHERE id = ?", (opponent_id,))
        opponent = cursor.fetchone()
        
        # Получаем прогресс ученика в турнире
        cursor.execute("""
            SELECT dm.bracket_type, 
                   dm.student1_id, 
                   dm.student2_id, 
                   dm.winner_id
            FROM duel_matches dm
            WHERE dm.duel_id = ? 
            AND (dm.student1_id = ? OR dm.student2_id = ?)
            ORDER BY 
                CASE dm.bracket_type
                    WHEN '1/64' THEN 1
                    WHEN '1/32' THEN 2
                    WHEN '1/16' THEN 3
                    WHEN '1/8' THEN 4
                    WHEN '1/4' THEN 5
                    WHEN '1/2' THEN 6
                    WHEN 'final' THEN 7
                    ELSE 8
                END
        """, (duel_id, session['student_id'], session['student_id']))
        
        # Инициализируем прогресс для всех возможных стадий
        student_progress = {
            '1/64': None,
            '1/32': None,
            '1/16': None,
            '1/8': None,
            '1/4': None,
            '1/2': None,
            'final': None
        }
        
        for m in cursor.fetchall():
            opponent_id = m['student1_id'] if m['student1_id'] != session['student_id'] else m['student2_id']
            cursor.execute("SELECT name FROM students WHERE id = ?", (opponent_id,))
            opponent_name = cursor.fetchone()['name']
            
            student_progress[m['bracket_type']] = {
                'opponent': opponent_name,
                'won': m['winner_id'] == session['student_id']
            }
        
        # Формируем задания для текущего матча
        tasks = []
        if match['generated_task'] and match['correct_answer']:
            task_texts = match['generated_task'].split('\n')
            answers = match['correct_answer'].split('\n')
            
            for i, text in enumerate(task_texts):
                tasks.append({
                    'text': text,
                    'answer': answers[i] if i < len(answers) else ''
                })
        
        return render_template('student_duel.html',
                            duel={
                                'id': duel['id'],
                                'name': duel['name'],
                                'current_round': duel['current_round']
                            },
                            match={
                                'id': match['id'],
                                'round_number': match['round_number'],
                                'student1_name': match['student1_name'],
                                'student2_name': match['student2_name']
                            },
                            current_student=current_student,
                            opponent=opponent,
                            student_progress=student_progress,
                            tasks=tasks)
    
    except Exception as e:
        flash(f"Ошибка: {str(e)}", "error")
        return redirect(url_for('index'))
    finally:
        conn.close()

def determine_stage(participant_count):
    """Определяет начальную стадию турнира по количеству участников"""
    stages = [
        (2, '1/2'),
        (4, '1/4'),
        (8, '1/8'), 
        (16, '1/16'),
        (32, '1/32'),
        (64, '1/64')
    ]
    
    # Находим ближайшую нижнюю степень двойки
    closest_stage = '1/2'  # минимальная стадия
    for threshold, stage in sorted(stages, reverse=True):
        if participant_count >= threshold:
            closest_stage = stage
            break
    
    return closest_stage


def generate_with_deepseek(prompt):
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "deepseek-chat",
        "messages": [{
            "role": "user",
            "content": f"""
            Придумай математическую задачу с параметрами {A} и {B} 
            по теме: '{prompt}'. Ответ дай в JSON формате: 
            {{"task": "текст задачи", "answer": "формула"}}
            """
        }],
        "temperature": 0.7
    }
    
    try:
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        app.logger.error(f"DeepSeek API error: {str(e)}")
        return None
        
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка запроса: {str(e)}")
        return {"error": str(e)}

@app.route('/generate_ai_template', methods=['POST'])
def generate_ai_template():
    try:
        # 1. Получаем и валидируем данные
        data = request.get_json()
        if not data or 'prompt' not in data:
            return jsonify({"error": "Missing 'prompt' parameter"}), 400
        
        prompt = data['prompt']
        
        # 2. Формируем правильный запрос к API
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        system_message = """
        You are a math task generator. Create a problem using {A} and {B} parameters.
        Return JSON format: {"task": "...", "answer": "..."}
        Use {A} and {B} in both task and answer formula.
        Example: {"task": "Solve {A}x + {B} = 0", "answer": "-{B}/{A}"}
        """
        
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "response_format": {"type": "json_object"}
        }
        
        # 3. Отправляем запрос к API
        response = requests.post(
            DEEPSEEK_API_URL,
            headers=headers,
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        
        # 4. Парсим ответ
        api_response = response.json()
        generated_content = json.loads(api_response['choices'][0]['message']['content'])
        
        # 5. Валидация ответа
        if not all(key in generated_content for key in ['task', 'answer']):
            raise ValueError("Invalid response format from API")
        
        # 6. Возвращаем результат
        return jsonify({
            "status": "success",
            "task": generated_content['task'],
            "answer": generated_content['answer']
        })
        
    except requests.exceptions.RequestException as e:
        app.logger.error(f"API request failed: {str(e)}")
        return jsonify({"error": "API service unavailable"}), 503
    except json.JSONDecodeError:
        app.logger.error("Invalid JSON response from API")
        return jsonify({"error": "Invalid API response"}), 502
    except Exception as e:
        app.logger.error(f"Generation error: {str(e)}")
        return jsonify({"error": str(e)}), 500
    
if __name__ == "__main__":
    # Настройка логгера с UTF-8
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('app.log', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)  # Используем stdout с UTF-8
        ]
    )
    
    # Запуск сервера
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,  # В продакшене debug должен быть False
        threaded=True
    )